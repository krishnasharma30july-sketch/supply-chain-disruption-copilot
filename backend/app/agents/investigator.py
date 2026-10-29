"""Tool-using investigation of the most severe flagged orders (bounded ReAct loop with structured output).

The model plans which read-only tools to call, reads their results and decides. Tools are deterministic functions
over the case (no side effects); the final choice is validated against the two policy-allowed options and applied
through the same policy code as the batch adjudicator. Every step is kept in the decision's `agent_trace`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from app.domain.models import MitigationDecision, OrderImpact
from app.llm.outputs import InvestigationStepOut
from app.llm.service import LLMService
from app.services.comms import order_ref
from app.services.formatting import pretty_category
from app.services.policy_engine import PolicyContext, delay_all
from app.services.retrieval.kb import KbChunk

MAX_RESULT_CHARS = 1500
_WORD = re.compile(r"[a-z]{3,}")


def _d(v) -> str:
    return v.strftime("%Y-%m-%d") if hasattr(v, "strftime") else str(v)[:10]


@dataclass
class CaseTools:
    imp: OrderImpact
    dec: MitigationDecision
    pc: PolicyContext
    kb: list[KbChunk]
    customer_impacts: list[OrderImpact] = field(default_factory=list)

    def run(self, tool: str, argument: str) -> dict:
        fn = getattr(self, f"tool_{tool}", None)
        if fn is None:
            return {"error": f"unknown tool {tool!r}"}
        return fn(argument)

    def tool_delivery_forecast(self, _: str) -> dict:
        i = self.imp
        return {
            "promised_by": _d(i.sla_ts), "baseline_eta": _d(i.baseline_eta), "eta_with_disruption": _d(i.projected_eta),
            "eta_p90": _d(i.projected_eta_p90), "days_late": i.days_late_calendar, "p_breach": round(i.p_breach, 2),
            "delayed_items": [
                {"item": pretty_category(li.category_en), "hold_days": round(li.hold_days, 1), "handling": li.handling_profile}
                for li in i.lines if li.affected
            ],
        }

    def tool_alternatives(self, _: str) -> dict:
        cat = {li.order_item_id: pretty_category(li.category_en) for li in self.imp.lines}
        return {"options": [
            {"item": cat.get(a.order_item_id, str(a.order_item_id)), "kind": a.kind,
             "from": f"{a.seller_city or ''}/{a.seller_state or ''}".strip("/") or None,
             "days_gained": round(a.gain_days, 1), "extra_cost_brl": round(a.extra_cost_brl, 2),
             "accepted_by_rules": a.accepted, "reason": a.reason}
            for a in self.dec.alternatives
        ] or "No alternate seller or carrier was available for any delayed item."}

    def tool_compare_plans(self, _: str) -> dict:
        alt = delay_all(self.imp, self.pc, "")

        def summary(d: MitigationDecision) -> dict:
            return {
                "days_late": d.resolution_days_late, "cost_brl": round(d.est_cost_brl, 2),
                "split_shipment": d.split_shipment, "cancels_items": d.offer_cancellation,
                "item_actions": sorted({ld.action for ld in d.line_decisions}),
                "compensation_brl": round(d.compensation.refund_total_brl + d.compensation.voucher_brl, 2),
            }

        return {"APPLY_RULE_PLAN": summary(self.dec), "DELAY_ALL": summary(alt)}

    def tool_customer_orders(self, _: str) -> dict:
        others = [o for o in self.customer_impacts if o.order_id != self.imp.order_id]
        return {"other_affected_orders": len(others), "orders": [
            {"order": order_ref(o.order_id), "days_late": o.days_late_calendar, "severity": round(o.severity_score)}
            for o in others[:5]
        ]}

    def tool_policy(self, argument: str) -> dict:
        q = set(_WORD.findall(argument.lower())) or {"delay"}
        scored = sorted(
            ((len(q & set(_WORD.findall(f"{c.heading} {c.text}".lower()))), c) for c in self.kb), key=lambda t: -t[0]
        )
        hits = [c for s, c in scored[:2] if s > 0]
        return {"sections": [{"id": c.chunk_id, "heading": c.heading, "text": c.text[:600]} for c in hits]
                or "No policy section matched."}


def case_facts(imp: OrderImpact, dec: MitigationDecision) -> dict:
    return {
        "case_id": order_ref(imp.order_id), "stage": imp.stage, "value_brl": round(imp.value_brl, 2),
        "items": [{"item": pretty_category(li.category_en), "product_class": li.product_class,
                   "class_confidence": li.class_confidence, "delayed": li.affected} for li in imp.lines],
        "rule_plan": dec.explanation, "review_reasons": dec.review_reasons,
    }


def _clip(result: dict) -> str:
    text = json.dumps(result, ensure_ascii=False, default=str)
    return text if len(text) <= MAX_RESULT_CHARS else text[:MAX_RESULT_CHARS] + "...(truncated)"


async def investigate(
    llm: LLMService, tools: CaseTools, *, run_id: str | None, max_steps: int = 3
) -> tuple[str | None, str, list[dict]]:
    """Returns (choice or None if undecided, rationale, trace)."""
    trace: list[dict] = []
    transcript_lines: list[str] = []
    case_json = json.dumps(case_facts(tools.imp, tools.dec), indent=1, ensure_ascii=False)
    for step in range(1, max_steps + 1):
        must_decide = step == max_steps
        res = await llm.generate(
            agent="mitigation", role="adjudicator", prompt="investigator",
            context={"case_json": case_json, "transcript": "\n".join(transcript_lines), "step": step,
                     "max_steps": max_steps, "must_decide": must_decide},
            schema=InvestigationStepOut, run_id=run_id, max_output_tokens=2048,
        )
        out = res.parsed
        entry: dict = {"step": step, "thought": out.thought.strip()[:300], "tools": []}
        if out.decision != "CONTINUE":
            entry["decision"] = out.decision
            trace.append(entry)
            return out.decision, out.rationale.strip(), trace
        if must_decide:
            trace.append(entry)
            break
        calls = out.tool_calls[:4]
        if not calls:  # neither a decision nor a tool call: ask again with an explicit nudge
            transcript_lines.append(f"Step {step}: no tool called and no decision. Call a tool or decide.")
            trace.append(entry)
            continue
        transcript_lines.append(f"Step {step} thought: {entry['thought']}")
        for c in calls:
            result = tools.run(c.tool, c.argument)
            text = _clip(result)
            entry["tools"].append({"tool": c.tool, "argument": c.argument[:80], "result": text[:400]})
            transcript_lines.append(f"- {c.tool}({c.argument!r}) -> {text}")
        trace.append(entry)
    return None, "", trace
