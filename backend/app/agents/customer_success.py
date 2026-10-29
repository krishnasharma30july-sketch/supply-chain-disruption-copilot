"""Customer Success agent: one grounded email per affected customer (LLM for the highest-severity cohort)."""

from __future__ import annotations

import asyncio
import json
from datetime import date

from sqlalchemy import select

from app.agents.base import AgentContext, EmailDraft, RunState
from app.core.logging import get_logger
from app.db.models import Customer
from app.domain.models import MitigationDecision, OrderImpact
from app.llm.outputs import EmailBatchOut
from app.llm.service import LLMCallError, LLMUnavailable
from app.services.comms import build_customer_facts, template_email
from app.services.retrieval.kb import load_kb

log = get_logger(__name__)


def tone_guide(ctx: AgentContext) -> str:
    chunks = [c for c in load_kb(ctx.settings.kb_dir) if c.chunk_id.startswith("customer_comms_tone_guide#")]
    return "\n\n".join(f"{c.heading}:\n{c.text}" for c in chunks)


class CustomerSuccessAgent:
    name = "customer_success"
    label = "Drafting personalised customer emails"

    async def run(self, state: RunState, ctx: AgentContext) -> None:
        assert state.spec is not None
        impacts = {i.order_id: i for i in state.impacts}
        groups: dict[str, list[tuple[OrderImpact, MitigationDecision]]] = {}
        for dec in state.decisions:
            if dec.action == "MONITOR":
                continue
            imp = impacts[dec.order_id]
            groups.setdefault(imp.customer_unique_id, []).append((imp, dec))

        names: dict[str, str] = {}
        if groups and ctx.session_factory is not None:
            async with ctx.session_factory() as s:
                ids = list(groups)
                for start in range(0, len(ids), 5000):
                    rows = await s.execute(
                        select(Customer.customer_unique_id, Customer.first_name).where(
                            Customer.customer_unique_id.in_(ids[start : start + 5000])
                        )
                    )
                    for cid, first in rows.all():
                        names.setdefault(cid, first)

        locale = state.options.locale
        shift = (date.today() - ctx.ref.as_of.date()).days if state.options.date_rebase else 0
        drafts: list[EmailDraft] = []
        for cid, pairs in groups.items():
            pairs.sort(key=lambda p: -p[0].severity_score)
            first = names.get(cid, "there" if locale == "en" else "cliente")
            facts, allow = build_customer_facts(cid, first, pairs, state.spec, locale, ctx.run_id, shift)
            drafts.append(
                EmailDraft(
                    customer_unique_id=cid, first_name=first, order_ids=[p[0].order_id for p in pairs],
                    max_severity=max(p[0].severity_score for p in pairs), facts=facts, allow=allow,
                )
            )
        drafts.sort(key=lambda d: (-d.max_severity, d.customer_unique_id))
        state.drafts = drafts

        cap = state.options.max_llm_emails if state.options.max_llm_emails is not None else ctx.settings.max_llm_emails
        n_llm = min(cap, len(drafts)) if (ctx.use_llm and ctx.llm.available) else 0
        await ctx.emitter.emit(
            self.name, "info",
            f"{len(drafts)} customers need a message. Drafting {n_llm} with the LLM (highest severity first) "
            f"and {len(drafts) - n_llm} from grounded templates.",
            {"customers": len(drafts), "llm": n_llm},
        )
        if n_llm:
            await self.write_llm(drafts[:n_llm], ctx)
        for d in drafts:
            if not d.body:
                self.write_template(d)
        llm_ok = sum(1 for d in drafts if d.generator == "llm")
        await ctx.emitter.emit(
            self.name, "result",
            f"Drafted {len(drafts)} emails ({llm_ok} by the LLM, {len(drafts) - llm_ok} from templates).",
            {"llm": llm_ok, "template": len(drafts) - llm_ok},
        )

    def write_template(self, d: EmailDraft) -> None:
        d.subject, d.body = template_email(d.facts)
        d.generator, d.model, d.prompt_version = "template", None, "template-v1"

    async def write_llm(self, drafts: list[EmailDraft], ctx: AgentContext, feedback: dict[str, list[str]] | None = None) -> None:
        guide = tone_guide(ctx)
        bs = ctx.settings.email_batch_size
        batches = [drafts[i : i + bs] for i in range(0, len(drafts), bs)]

        async def one(batch: list[EmailDraft]) -> bool:
            by_ref = {d.facts.customer_ref: d for d in batch}
            fb = ""
            if feedback:
                fb = "\n".join(
                    f"- customer_ref {d.facts.customer_ref}: " + "; ".join(feedback.get(d.customer_unique_id, []))
                    for d in batch
                    if feedback.get(d.customer_unique_id)
                )
            try:
                res = await ctx.llm.generate(
                    agent=self.name, role="email", prompt="email",
                    context={
                        "locale": batch[0].facts.locale,
                        "tone_guide": guide,
                        "facts_json": json.dumps([d.facts.model_dump() for d in batch], ensure_ascii=False, indent=1),
                        "feedback": fb,
                    },
                    schema=EmailBatchOut, run_id=ctx.run_id, max_output_tokens=16384,
                )
            except (LLMUnavailable, LLMCallError) as e:
                log.warning("email_batch_failed", error=str(e), size=len(batch))
                for d in batch:
                    d.attempts += 1
                    d.llm_feedback.append(f"LLM unavailable: {e}")
                return False
            for out in res.parsed.emails:
                d = by_ref.get(out.customer_ref.strip())
                if d is None:
                    continue
                d.subject, d.body = out.subject.strip(), out.body.strip()
                d.generator, d.model, d.prompt_version = "llm", res.model, res.prompt_version
                d.attempts += 1
            return True

        results = await asyncio.gather(*(one(b) for b in batches))
        # Transient provider errors (e.g. 503 "high demand") often clear within a minute: give failed
        # batches one deferred, sequential retry before they fall back to grounded templates.
        failed = [b for b, ok in zip(batches, results, strict=True) if not ok]
        if failed and ctx.llm.available:
            await ctx.emitter.emit(self.name, "info", f"Retrying {len(failed)} email batch(es) after a transient LLM error.")
            await asyncio.sleep(10)
            for b in failed:
                if not ctx.llm.available:
                    break
                await one(b)
