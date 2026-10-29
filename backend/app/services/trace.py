"""Per-order reasoning trace: disruption -> matched seller/lane -> order -> SKUs -> ETA -> severity -> action -> email."""

from __future__ import annotations

from app.domain.models import DisruptionSpec, MitigationDecision, OrderImpact
from app.services.formatting import pretty_category


def _d(v) -> str:
    return v.strftime("%Y-%m-%d %H:%M") if hasattr(v, "strftime") else str(v)[:16].replace("T", " ")


def build_trace(spec: DisruptionSpec, imp: OrderImpact, dec: MitigationDecision, comm: dict | None) -> list[dict]:
    steps: list[dict] = []
    window = f"{_d(spec.start_at)} to {_d(spec.end_at)}"
    steps.append({
        "kind": "disruption",
        "title": spec.title,
        "detail": (
            f"{spec.disruption_type} | origin: {spec.origin.describe()} | destination: {spec.destination.describe()}"
            f"{' | nationwide' if spec.nationwide else ''} | legs: {', '.join(spec.legs)} | window {window}"
            + (f" + {spec.recovery_days:g} recovery days" if spec.recovery_days else "")
            + (f" | explicit delay {spec.explicit_delay_days:g} days" if spec.explicit_delay_days is not None else "")
        ),
    })
    affected = [li for li in imp.lines if li.affected]
    reasons = sorted({r for li in imp.lines for r in li.match_reasons})
    sellers = sorted({f"{li.seller_city.title()}/{li.seller_state} (seller {li.seller_id[:8]})" for li in imp.lines})
    steps.append({
        "kind": "match",
        "title": "Matched by structured scope filters",
        "detail": f"Rules: {', '.join(reasons) or 'none'} | sellers: {', '.join(sellers)} | destination {imp.cust_city.title()}/{imp.cust_state}",
    })
    steps.append({
        "kind": "order",
        "title": f"Order {imp.order_id[:8].upper()} ({imp.stage.replace('_', ' ')})",
        "detail": f"Purchased {_d(imp.purchase_ts)} | promised by {_d(imp.sla_ts)} | value R$ {imp.value_brl:.2f} | {len(imp.lines)} item(s), {len(affected)} delayed",
    })
    for li in imp.lines:
        legs = "; ".join(f"{h.leg} held {h.hold_days:.1f}d ({h.note})" for h in li.legs) or "not held"
        steps.append({
            "kind": "sku",
            "title": f"SKU {li.product_id[:8]}: {pretty_category(li.category_en)}",
            "detail": (
                f"{li.billable_weight_kg:.1f} kg billable | {li.handling_profile} | {li.product_class} | lane {li.lane} "
                f"({li.lane_level}, p50 {li.lane_p50:.1f}d / p90 {li.lane_p90:.1f}d) | {legs}"
            ),
        })
    steps.append({
        "kind": "eta",
        "title": "Delivery forecast",
        "detail": (
            f"Baseline ETA {_d(imp.baseline_eta)} -> projected {_d(imp.projected_eta)} (p90 {_d(imp.projected_eta_p90)}) "
            f"vs promise {_d(imp.sla_ts)}: {imp.days_late_calendar} calendar day(s) late | "
            f"P(breach) {imp.p_breach:.0%} (baseline {imp.p_breach_baseline:.0%})"
        ),
    })
    comp_txt = ", ".join(f"{c.name} {c.points:.1f}" for c in imp.severity_components if c.points)
    steps.append({
        "kind": "severity",
        "title": f"Severity {imp.severity_score:.0f} ({imp.severity_tier})",
        "detail": comp_txt or "Not delayed by the disruption.",
    })
    for s in dec.agent_trace:
        calls = "; ".join(f"{t['tool']}{'(' + repr(t['argument']) + ')' if t.get('argument') else ''} -> {t['result'][:160]}"
                          for t in s.get("tools", []))
        steps.append({
            "kind": "agent",
            "title": f"Investigator step {s.get('step')}" + (f": decided {s['decision']}" if s.get("decision") else ""),
            "detail": s.get("thought", "") + (f" | tools: {calls}" if calls else ""),
        })
    comp = dec.compensation
    comp_bits = []
    if comp.refund_total_brl:
        comp_bits.append(f"refund R$ {comp.refund_total_brl:.2f}")
    if comp.voucher_brl:
        comp_bits.append(f"voucher R$ {comp.voucher_brl:.2f} ({comp.voucher_pct:g}%)")
    if comp.human_callback:
        comp_bits.append("human callback")
    steps.append({
        "kind": "decision",
        "title": f"{dec.action} (rule {dec.rule_id}, decided by {dec.decided_by})",
        "detail": dec.explanation
        + (f" | compensation: {', '.join(comp_bits)}" if comp_bits else "")
        + (f" | reviewer rationale: {dec.llm_rationale}" if dec.llm_rationale else "")
        + f" | resolution ETA {_d(dec.resolution_eta)} ({dec.resolution_days_late} day(s) late)",
    })
    if comm:
        steps.append({
            "kind": "communication",
            "title": f"Email ({comm['generator']}, QA {'passed' if (comm.get('qa') or {}).get('passed') else 'failed'})",
            "detail": comm["subject"],
        })
    return steps
