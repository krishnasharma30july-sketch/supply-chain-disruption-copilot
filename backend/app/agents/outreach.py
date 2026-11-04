"""Outreach agent: call briefs for the care team and notices to the most exposed sellers.

These are drafts for people to use; nothing is sent automatically. Each text is grounded on a fact sheet and verified by
a deterministic checker (one redraft round with the reasons), with a deterministic template as the fallback.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import insert

from app.agents.base import AgentContext, EmailDraft, RunState
from app.agents.customer_success import tone_guide
from app.core.logging import get_logger
from app.db.models import OutreachItem
from app.llm.outputs import CallBriefBatchOut, SellerNoticeBatchOut
from app.llm.service import LLMCallError, LLMUnavailable
from app.services.outreach import (
    SellerAllow,
    SellerFacts,
    brief_body,
    build_seller_facts,
    check_call_brief,
    check_seller_notice,
    template_brief,
    template_seller_notice,
)
from app.services.qa import QAReport

log = get_logger(__name__)

DRAFT_ROUNDS = 2  # the first draft plus one redraft with the checker's reasons


@dataclass
class Drafted:
    out: Any
    generator: str  # llm | template
    model: str | None
    prompt_version: str | None
    report: QAReport
    attempts: int


Generate = Callable[[list[str], dict[str, list[str]]], Awaitable[dict[str, tuple[Any, str, str]]]]


async def draft_with_checks(
    refs: list[str],
    ctx: AgentContext,
    *,
    use_ai: bool,
    generate: Generate,
    check: Callable[[str, Any], QAReport],
    template: Callable[[str], Any],
) -> dict[str, Drafted]:
    """LLM draft -> deterministic check -> one redraft with the reasons -> template. Returns a result for every ref."""
    results: dict[str, Drafted] = {}
    attempts: dict[str, int] = defaultdict(int)
    feedback: dict[str, list[str]] = {}
    pending = list(refs)
    if use_ai:
        for _ in range(DRAFT_ROUNDS):
            if not pending or not ctx.llm.available:
                break
            got = await generate(pending, feedback)
            still: list[str] = []
            for ref in pending:
                attempts[ref] += 1
                item = got.get(ref)
                if item is None:
                    still.append(ref)
                    feedback[ref] = ["The model returned nothing usable for this item."]
                    continue
                out, model, version = item
                report = check(ref, out)
                if report.passed:
                    results[ref] = Drafted(out, "llm", model, version, report, attempts[ref])
                else:
                    still.append(ref)
                    feedback[ref] = report.failures()
            pending = still
    for ref in refs:
        if ref not in results:
            out = template(ref)
            results[ref] = Drafted(out, "template", None, "template-v1", check(ref, out), attempts[ref])
    return results


def critical_drafts(state: RunState, limit: int) -> list[EmailDraft]:
    """Customers with at least one critical-severity order who are being contacted, most severe first."""
    critical = {i.customer_unique_id for i in state.impacts if i.affected and i.severity_tier == "critical"}
    drafts = [d for d in state.drafts if d.customer_unique_id in critical]
    drafts.sort(key=lambda d: (-d.max_severity, d.customer_unique_id))
    return drafts[: max(0, limit)]


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


class OutreachAgent:
    name = "outreach"
    label = "Preparing care-team call briefs and seller notices"

    async def run(self, state: RunState, ctx: AgentContext) -> None:
        assert state.spec is not None
        s = ctx.settings
        use_ai = ctx.use_llm and ctx.llm.available
        locale = state.options.locale
        briefs = critical_drafts(state, s.max_call_briefs) if s.max_call_briefs else []
        sellers = build_seller_facts(state.impacts, state.spec, locale, s.max_seller_notices) if s.max_seller_notices else []
        if not briefs and not sellers:
            await ctx.emitter.emit(self.name, "result", "No call briefs or seller notices are needed for this scenario.")
            state.metrics["outreach"] = {"call_briefs": 0, "seller_notices": 0}
            return

        rows: list[dict] = []
        n_brief_ai = n_notice_ai = failed = 0
        if briefs:
            by_ref = {d.facts.customer_ref: d for d in briefs}
            res = await draft_with_checks(
                list(by_ref), ctx, use_ai=use_ai, generate=self._brief_generator(by_ref, ctx),
                check=lambda r, o: check_call_brief(o, by_ref[r].facts, by_ref[r].allow),
                template=lambda r: template_brief(by_ref[r].facts),
            )
            for ref, d in by_ref.items():
                r = res[ref]
                n_brief_ai += r.generator == "llm"
                failed += not r.report.passed
                refs = ", ".join(o.order_ref for o in d.facts.orders[:3]) + ("…" if len(d.facts.orders) > 3 else "")
                rows.append({
                    "id": str(uuid.uuid4()), "run_id": ctx.run_id, "kind": "call_brief", "ref": ref,
                    "title": f"Call {d.first_name} about {refs}", "subject": None, "body": brief_body(r.out),
                    "data": {"fields": r.out.model_dump(), "customer_unique_id": d.customer_unique_id,
                             "order_ids": d.order_ids, "first_name": d.first_name, "locale": d.facts.locale},
                    "generator": r.generator, "model": r.model, "prompt_version": r.prompt_version,
                    "qa": {**r.report.model_dump(), "generator": r.generator}, "priority": d.max_severity,
                    "status": "draft" if r.report.passed else "qa_failed",
                })
        if sellers:
            facts_by_ref: dict[str, tuple[SellerFacts, SellerAllow]] = {f.seller_ref: (f, a) for f, a in sellers}
            res = await draft_with_checks(
                list(facts_by_ref), ctx, use_ai=use_ai, generate=self._notice_generator(facts_by_ref, ctx),
                check=lambda r, o: check_seller_notice(o.subject.strip(), o.body.strip(), *facts_by_ref[r]),
                template=lambda r: template_seller_notice(facts_by_ref[r][0]),
            )
            for ref, (facts, _allow) in facts_by_ref.items():
                r = res[ref]
                n_notice_ai += r.generator == "llm"
                failed += not r.report.passed
                rows.append({
                    "id": str(uuid.uuid4()), "run_id": ctx.run_id, "kind": "seller_notice", "ref": ref,
                    "title": f"Notice to seller {ref} ({facts.location})", "subject": r.out.subject.strip(),
                    "body": r.out.body.strip(), "data": {"facts": facts.model_dump()},
                    "generator": r.generator, "model": r.model, "prompt_version": r.prompt_version,
                    "qa": {**r.report.model_dump(), "generator": r.generator}, "priority": float(facts.orders_affected),
                    "status": "draft" if r.report.passed else "qa_failed",
                })

        if ctx.session_factory is not None and rows:
            async with ctx.session_factory() as session:
                await session.execute(insert(OutreachItem), rows)
                await session.commit()

        state.metrics["outreach"] = {
            "call_briefs": len(briefs), "call_briefs_llm": n_brief_ai,
            "seller_notices": len(sellers), "seller_notices_llm": n_notice_ai, "qa_failed": failed,
        }
        await ctx.emitter.emit(
            self.name, "result",
            f"Prepared {_count(len(briefs), 'call brief')} for the care team ({n_brief_ai} AI-written) and "
            f"{_count(len(sellers), 'seller notice')} ({n_notice_ai} AI-written). Nothing is sent automatically.",
            state.metrics["outreach"],
        )

    # ------------------------------------------------------------------ LLM generators

    def _brief_generator(self, by_ref: dict[str, EmailDraft], ctx: AgentContext) -> Generate:
        guide = tone_guide(ctx)

        async def generate(refs: list[str], feedback: dict[str, list[str]]) -> dict[str, tuple[Any, str, str]]:
            out: dict[str, tuple[Any, str, str]] = {}
            bs = ctx.settings.email_batch_size

            async def one(batch: list[str]) -> None:
                facts = [by_ref[r].facts for r in batch]
                fb = "\n".join(f"- customer_ref {r}: " + "; ".join(feedback[r]) for r in batch if feedback.get(r))
                try:
                    res = await ctx.llm.generate(
                        agent=self.name, role="email", prompt="call_brief",
                        context={
                            "locale": facts[0].locale, "tone_guide": guide, "feedback": fb,
                            "facts_json": json.dumps([f.model_dump(exclude={"allowed_placeholders"}) for f in facts], ensure_ascii=False, indent=1),
                        },
                        schema=CallBriefBatchOut, run_id=ctx.run_id, max_output_tokens=8192,
                    )
                except (LLMUnavailable, LLMCallError) as e:
                    log.warning("call_brief_batch_failed", error=str(e)[:200], size=len(batch))
                    return
                for b in res.parsed.briefs:
                    ref = b.customer_ref.strip()
                    if ref in batch and ref not in out:
                        out[ref] = (b, res.model, res.prompt_version)

            await asyncio.gather(*(one(refs[i : i + bs]) for i in range(0, len(refs), bs)))
            return out

        return generate

    def _notice_generator(self, facts_by_ref: dict[str, tuple[SellerFacts, SellerAllow]], ctx: AgentContext) -> Generate:
        async def generate(refs: list[str], feedback: dict[str, list[str]]) -> dict[str, tuple[Any, str, str]]:
            out: dict[str, tuple[Any, str, str]] = {}
            bs = ctx.settings.email_batch_size

            async def one(batch: list[str]) -> None:
                facts = [facts_by_ref[r][0] for r in batch]
                fb = "\n".join(f"- seller_ref {r}: " + "; ".join(feedback[r]) for r in batch if feedback.get(r))
                try:
                    res = await ctx.llm.generate(
                        agent=self.name, role="email", prompt="seller_notice",
                        context={
                            "locale": facts[0].locale, "feedback": fb,
                            "facts_json": json.dumps([f.model_dump(exclude={"locale"}) for f in facts], ensure_ascii=False, indent=1),
                        },
                        schema=SellerNoticeBatchOut, run_id=ctx.run_id, max_output_tokens=8192,
                    )
                except (LLMUnavailable, LLMCallError) as e:
                    log.warning("seller_notice_batch_failed", error=str(e)[:200], size=len(batch))
                    return
                for n in res.parsed.notices:
                    ref = n.seller_ref.strip()
                    if ref in batch and ref not in out:
                        out[ref] = (n, res.model, res.prompt_version)

            await asyncio.gather(*(one(refs[i : i + bs]) for i in range(0, len(refs), bs)))
            return out

        return generate
