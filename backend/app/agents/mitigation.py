"""Mitigation agent: policy-as-code decisions, bounded LLM adjudication of flagged cases, persistence."""

from __future__ import annotations

import asyncio
import json
from collections import Counter

from sqlalchemy import insert

from app.agents.base import AgentContext, RunState
from app.agents.investigator import CaseTools, investigate
from app.db.models import OrderOutcome
from app.domain.models import MitigationDecision, OrderImpact
from app.llm.outputs import AdjudicationBatchOut
from app.llm.service import LLMCallError, LLMUnavailable
from app.services.analysis import decide
from app.services.comms import order_ref
from app.services.formatting import pretty_category
from app.services.policy_engine import delay_all
from app.services.reporting import ACTION_LABELS
from app.services.retrieval.kb import kb_index


def _case(imp: OrderImpact, dec: MitigationDecision) -> dict:
    plan = {ld.order_item_id: ld for ld in dec.line_decisions}
    return {
        "case_id": order_ref(imp.order_id),
        "stage": imp.stage,
        "days_late_without_action": imp.days_late_calendar,
        "items": [
            {
                "item": pretty_category(li.category_en),
                "product_class": li.product_class,
                "class_confidence": li.class_confidence,
                "handling_profile": li.handling_profile,
                "billable_kg": li.billable_weight_kg,
                "delayed": li.affected,
                "planned_action": plan[li.order_item_id].action if li.order_item_id in plan else "SHIP_NOW",
                "reason": plan[li.order_item_id].reason if li.order_item_id in plan else "Not affected by the disruption.",
            }
            for li in imp.lines
        ],
        "rule_plan_summary": dec.explanation,
        "review_reasons": dec.review_reasons,
        "days_late_with_rule_plan": dec.resolution_days_late,
    }


class MitigationAgent:
    name = "mitigation"
    label = "Deciding cancel / reroute / delay per order"

    async def run(self, state: RunState, ctx: AgentContext) -> None:
        assert state.spec is not None
        decisions = await asyncio.to_thread(decide, state.spec, ctx.ref, state.impacts, ctx.policy)
        by_order = {d.order_id: d for d in decisions}
        impacts_by_order = {i.order_id: i for i in state.impacts}

        review = [d for d in decisions if d.needs_review]
        review.sort(key=lambda d: -impacts_by_order[d.order_id].severity_score)
        to_adjudicate = review[: ctx.settings.max_adjudications] if (ctx.use_llm and ctx.llm.available) else []
        if review and not to_adjudicate:
            await ctx.emitter.emit(
                self.name, "info",
                f"{len(review)} orders need review; the LLM adjudicator is off, so the conservative rule plan stands.",
            )
        adjudicated = investigated = 0
        # The most severe flagged cases get a tool-using investigation first; undecided ones fall back to the batch.
        agent_cases = to_adjudicate[: ctx.settings.max_agent_cases]
        if agent_cases:
            pc = ctx.ref.policy_context(state.spec, ctx.policy)
            chunks = list(kb_index(ctx.settings.kb_dir).values())
            by_customer: dict[str, list[OrderImpact]] = {}
            for imp in state.impacts:
                by_customer.setdefault(imp.customer_unique_id, []).append(imp)
            for dec in agent_cases:
                imp = impacts_by_order[dec.order_id]
                tools = CaseTools(imp=imp, dec=dec, pc=pc, kb=chunks, customer_impacts=by_customer.get(imp.customer_unique_id, []))
                try:
                    choice, rationale, trace = await investigate(
                        ctx.llm, tools, run_id=ctx.run_id, max_steps=ctx.settings.agent_max_steps
                    )
                except (LLMUnavailable, LLMCallError) as e:
                    await ctx.emitter.emit(self.name, "warning", f"Investigation unavailable ({e}).", level="warning")
                    break
                called = [t["tool"] for s in trace for t in s["tools"]]
                if choice is None:
                    dec.agent_trace = trace
                    continue
                if choice == "DELAY_ALL":
                    new = delay_all(imp, pc, rationale)
                    new.review_reasons = dec.review_reasons
                    by_order[dec.order_id] = new
                else:
                    dec.llm_rationale = rationale
                final = by_order[dec.order_id]
                final.decided_by = "llm_investigator"
                final.agent_trace = trace
                final.needs_review = False
                investigated += 1
                await ctx.emitter.emit(
                    self.name, "info",
                    f"Investigated {order_ref(dec.order_id)}: called {', '.join(called) or 'no tools'} in "
                    f"{len(trace)} step(s), chose {'the rule plan' if choice == 'APPLY_RULE_PLAN' else 'delay all with compensation'}.",
                    {"order": order_ref(dec.order_id), "tools": called, "steps": len(trace), "choice": choice},
                )
            to_adjudicate = [d for d in to_adjudicate if by_order[d.order_id].needs_review]
        if to_adjudicate:
            kb = kb_index(ctx.settings.kb_dir)
            refs = sorted({r for d in to_adjudicate for r in d.policy_refs} | {"mitigation_playbook#mixed-orders-and-split-shipments"})
            policy_text = "\n\n".join(f"[{r}] {kb[r].heading}: {kb[r].text}" for r in refs if r in kb)
            bs = ctx.settings.adjudication_batch_size
            for start in range(0, len(to_adjudicate), bs):
                batch = to_adjudicate[start : start + bs]
                cases = {order_ref(d.order_id): d for d in batch}
                try:
                    res = await ctx.llm.generate(
                        agent="mitigation", role="adjudicator", prompt="adjudicator",
                        context={
                            "policy": policy_text,
                            "cases_json": json.dumps([_case(impacts_by_order[d.order_id], d) for d in batch], indent=1, default=str),
                        },
                        schema=AdjudicationBatchOut, run_id=ctx.run_id, max_output_tokens=8192,
                    )
                except (LLMUnavailable, LLMCallError) as e:
                    await ctx.emitter.emit(self.name, "warning", f"Adjudication unavailable ({e}); rule plans kept.", level="warning")
                    break
                for out in res.parsed.decisions:
                    dec = cases.get(out.case_id.strip().upper())
                    if dec is None:
                        continue
                    imp = impacts_by_order[dec.order_id]
                    if out.choice == "DELAY_ALL":
                        new = delay_all(imp, ctx.ref.policy_context(state.spec, ctx.policy), out.rationale.strip())
                        new.review_reasons = dec.review_reasons
                        by_order[dec.order_id] = new
                    else:
                        dec.decided_by = "llm_adjudicator"
                        dec.llm_rationale = out.rationale.strip()
                    by_order[dec.order_id].needs_review = False
                    adjudicated += 1
        state.decisions = [by_order[i.order_id] for i in state.impacts]

        if ctx.session_factory is not None:
            rows = []
            for imp in state.impacts:
                dec = by_order[imp.order_id]
                rows.append({
                    "run_id": ctx.run_id, "order_id": imp.order_id, "customer_unique_id": imp.customer_unique_id,
                    "stage": imp.stage,
                    "origin_states": sorted({li.seller_state for li in imp.lines}),
                    "dest_state": imp.cust_state, "categories": imp.categories, "product_classes": imp.product_classes,
                    "handling_profile": imp.handling_profile, "value_brl": imp.value_brl,
                    "billable_weight_kg": imp.billable_weight_kg, "affected": imp.affected, "hold_days": imp.hold_days,
                    "days_late": imp.days_late, "resolution_days_late": dec.resolution_days_late,
                    "p_breach": imp.p_breach, "severity_score": imp.severity_score, "severity_tier": imp.severity_tier,
                    "action": dec.action, "offer_cancellation": dec.offer_cancellation, "needs_review": dec.needs_review,
                    "decided_by": dec.decided_by, "est_cost_brl": dec.est_cost_brl, "sla_ts": imp.sla_ts,
                    "projected_eta": imp.projected_eta, "resolution_eta": dec.resolution_eta,
                    "detail": {"impact": imp.model_dump(mode="json"), "decision": dec.model_dump(mode="json")},
                })
            async with ctx.session_factory() as s:
                for start in range(0, len(rows), 1000):
                    await s.execute(insert(OrderOutcome), rows[start : start + 1000])
                await s.commit()

        actions = Counter(d.action for d in state.decisions)
        await ctx.emitter.emit(
            self.name, "result",
            "Decisions: " + ", ".join(f"{ACTION_LABELS.get(k, k)} {v}" for k, v in actions.most_common())
            + (f". {investigated} flagged orders investigated by the tool-using agent" if investigated else "")
            + (f". {adjudicated} flagged orders adjudicated by the LLM within policy-allowed options." if adjudicated else "."),
            {"actions": dict(actions), "needs_review": len(review), "adjudicated": adjudicated, "investigated": investigated},
        )
