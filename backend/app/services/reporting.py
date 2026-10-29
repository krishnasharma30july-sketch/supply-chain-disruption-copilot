"""Run-level impact statistics, deterministic recommendations and the narrative fact allow-list."""

from __future__ import annotations

from collections import Counter, defaultdict

from app.domain.models import DisruptionSpec, MitigationDecision, OrderImpact
from app.services.fact_check import scan
from app.services.formatting import pretty_category

TIERS = ["critical", "high", "medium", "low"]
ACTION_LABELS = {
    "MONITOR": "monitor only (no customer contact)",
    "NOTIFY_ONLY": "proactive heads-up (still on time)",
    "CANCEL_REFUND": "cancel and refund (perishable)",
    "REROUTE_ALT_SELLER": "re-source from an alternate seller",
    "REROUTE_ALT_ROUTE": "reroute via alternate carrier/air",
    "DELAY_WITH_COMPENSATION": "delay with compensation",
}


def _round(v: float, n: int = 2) -> float:
    return round(float(v), n)


def compute_stats(
    spec: DisruptionSpec,
    impacts: list[OrderImpact],
    decisions: list[MitigationDecision],
    comms_summary: dict,
    seller_geo: dict[str, tuple[str, str, float | None, float | None]],
) -> dict:
    dec_by_order = {d.order_id: d for d in decisions}
    delayed = [i for i in impacts if i.affected]
    breaching = [i for i in delayed if i.days_late_calendar > 0]
    newly = [i for i in breaching if i.baseline_days_late <= 0]
    affected_lines = [(i, li) for i in delayed for li in i.lines if li.affected]

    by_cat: dict[str, dict] = defaultdict(lambda: {"orders": set(), "items": 0, "value": 0.0})
    for i, li in affected_lines:
        key = pretty_category(li.category_en)
        by_cat[key]["orders"].add(i.order_id)
        by_cat[key]["items"] += 1
        by_cat[key]["value"] += li.price + li.freight
    top_categories = sorted(
        ({"category": k, "orders": len(v["orders"]), "items": v["items"], "value_brl": _round(v["value"])} for k, v in by_cat.items()),
        key=lambda r: (-r["orders"], r["category"]),
    )[:10]

    by_seller: dict[str, dict] = defaultdict(lambda: {"orders": set(), "value": 0.0})
    for i, li in affected_lines:
        by_seller[li.seller_id]["orders"].add(i.order_id)
        by_seller[li.seller_id]["value"] += li.price + li.freight
    top_sellers = []
    for sid, v in sorted(by_seller.items(), key=lambda kv: (-len(kv[1]["orders"]), kv[0]))[:10]:
        city, state = (seller_geo.get(sid) or ("", "", None, None))[:2]
        top_sellers.append({"seller_id": sid, "city": city, "state": state, "orders": len(v["orders"]), "value_brl": _round(v["value"])})

    lanes = Counter()
    origin_states = Counter()
    for _i, li in affected_lines:
        lanes[li.lane] += 1
        origin_states[li.seller_state] += 1
    dest_states = Counter(i.cust_state for i in delayed)

    actions = Counter(dec_by_order[i.order_id].action for i in impacts if i.order_id in dec_by_order)
    comp_refunds = sum(d.compensation.refund_total_brl for d in decisions)
    comp_vouchers = sum(d.compensation.voucher_brl for d in decisions)
    reroute_cost = sum(a.extra_cost_brl for d in decisions for a in d.alternatives if a.accepted)
    bulky = [i for i in delayed if any(li.affected and li.handling_profile == "BULKY" for li in i.lines)]
    heavy_items = sorted(
        (
            {"order_id": i.order_id, "product_id": li.product_id, "category": pretty_category(li.category_en),
             "billable_kg": li.billable_weight_kg, "profile": li.handling_profile, "seller_state": li.seller_state}
            for i, li in affected_lines
        ),
        key=lambda r: -r["billable_kg"],
    )[:10]

    return {
        "scope": {
            "orders_in_scope": len(impacts),
            "orders_delayed": len(delayed),
            "orders_breaching": len(breaching),
            "orders_newly_breaching": len(newly),
            "items_delayed": len(affected_lines),
            "skus_delayed": len({li.product_id for _, li in affected_lines}),
            "sellers_delayed": len({li.seller_id for _, li in affected_lines}),
            "customers_delayed": len({i.customer_unique_id for i in delayed}),
            "gmv_delayed_brl": _round(sum(i.value_brl for i in delayed)),
            "billable_kg_delayed": _round(sum(li.billable_weight_kg for _, li in affected_lines), 1),
            "avg_hold_days": _round(sum(i.hold_days for i in delayed) / len(delayed), 1) if delayed else 0.0,
            "avg_days_late_breaching": _round(sum(i.days_late for i in breaching) / len(breaching), 1) if breaching else 0.0,
            "bulky_orders_delayed": len(bulky),
        },
        "severity": {t: sum(1 for i in delayed if i.severity_tier == t) for t in TIERS},
        "actions": dict(sorted(actions.items())),
        "flags": {
            "offer_cancellation": sum(1 for d in decisions if d.offer_cancellation),
            "split_shipment": sum(1 for d in decisions if d.split_shipment),
            "needs_review": sum(1 for d in decisions if d.needs_review),
            "adjudicated_by_llm": sum(1 for d in decisions if d.decided_by in ("llm_adjudicator", "llm_investigator")),
            "investigated_by_agent": sum(1 for d in decisions if d.decided_by == "llm_investigator"),
            "human_callback": sum(1 for d in decisions if d.compensation.human_callback),
        },
        "cost": {
            "refunds_brl": _round(comp_refunds),
            "vouchers_brl": _round(comp_vouchers),
            "reroute_extra_brl": _round(reroute_cost),
            "total_brl": _round(comp_refunds + comp_vouchers + reroute_cost),
        },
        "top_categories": top_categories,
        "top_sellers": top_sellers,
        "top_lanes": [{"lane": k, "items": v} for k, v in lanes.most_common(10)],
        "origin_states": dict(origin_states.most_common()),
        "dest_states": dict(dest_states.most_common()),
        "heaviest_delayed_items": heavy_items,
        "communications": comms_summary,
    }


def recommendations(stats: dict, spec: DisruptionSpec) -> list[str]:
    s = stats["scope"]
    recs: list[str] = []
    if s["orders_delayed"] == 0:
        return ["No orders are delayed by this disruption. Keep monitoring and re-run if the scope or duration changes."]
    crit = stats["severity"].get("critical", 0)
    if crit:
        recs.append(f"Assign customer care to call the {_n(crit, 'critical-severity customer')} before the emails go out.")
    rr = stats["actions"].get("REROUTE_ALT_ROUTE", 0)
    if rr:
        recs.append(f"Pre-book alternate carrier or air capacity for {_n(rr, 'parcel order')} blocked on the affected legs.")
    ras = stats["actions"].get("REROUTE_ALT_SELLER", 0)
    if ras:
        recs.append(f"Confirm stock with the alternate sellers for {_n(ras, 'order')} being re-sourced outside the disrupted area.")
    origin_side = spec.nationwide or bool({"first_mile", "linehaul"} & set(spec.legs))
    if stats["top_sellers"] and origin_side:
        top = stats["top_sellers"][:3]
        names = ", ".join(f"{t['seller_id'][:8]} ({t['city'].title()}/{t['state']}, {t['orders']} orders)" for t in top)
        recs.append(f"Contact the most exposed sellers to hold or re-plan handovers: {names}.")
    if "last_mile" in spec.legs and stats["dest_states"]:
        dests = ", ".join(f"{k} ({v})" for k, v in list(stats["dest_states"].items())[:3])
        recs.append(
            f"Coordinate with last-mile partners in the affected destinations ({dests} delayed orders) to queue deliveries "
            "for the first days after the disruption, oldest promises first."
        )
    breaching = s["orders_breaching"]
    if breaching:
        recs.append(
            f"Review and approve the customer emails, starting with the {_n(breaching, 'order')} that will miss the "
            "promised date (sorted by severity on the Customer emails tab)."
        )
    if s["bulky_orders_delayed"]:
        recs.append(f"Plan dedicated recovery capacity for {_n(s['bulky_orders_delayed'], 'bulky order')}; bulky freight clears the backlog last.")
    review = stats["flags"].get("needs_review", 0)
    if review:
        recs.append(f"Review the {_n(review, 'order')} flagged for mixed or uncertain handling.")
    cancel = stats["actions"].get("CANCEL_REFUND", 0)
    if cancel:
        recs.append(f"Process {_n(cancel, 'proactive cancellation')} of perishable goods and trigger the refunds.")
    return recs[:8]


def _n(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def narrative_facts(stats: dict, spec: DisruptionSpec) -> dict:
    """Pre-formatted facts handed to the LLM; the narrative may only use numbers that appear here."""
    s = stats["scope"]
    pct_breach = round(100 * s["orders_breaching"] / s["orders_delayed"]) if s["orders_delayed"] else 0
    return {
        "disruption": spec.title,
        "duration_days": spec.duration_days,
        "recovery_days": spec.recovery_days,
        "orders_in_scope": s["orders_in_scope"],
        "orders_delayed": s["orders_delayed"],
        "orders_breaching_promise": s["orders_breaching"],
        "percent_of_delayed_breaching": pct_breach,
        "orders_newly_breaching": s["orders_newly_breaching"],
        "customers_delayed": s["customers_delayed"],
        "sellers_delayed": s["sellers_delayed"],
        "skus_delayed": s["skus_delayed"],
        "gmv_delayed_brl": s["gmv_delayed_brl"],
        "avg_hold_days": s["avg_hold_days"],
        "bulky_orders_delayed": s["bulky_orders_delayed"],
        "severity_counts": stats["severity"],
        "orders_by_decision": {ACTION_LABELS.get(k, k): v for k, v in stats["actions"].items()},
        "estimated_mitigation_cost_brl": stats["cost"]["total_brl"],
        "top_categories": [{"category": c["category"], "delayed_orders": c["orders"]} for c in stats["top_categories"][:3]],
        "top_origin_states_by_delayed_items": [
            {"state": k, "delayed_items": v} for k, v in list(stats["origin_states"].items())[:3]
        ],
    }


def _collect_numbers(obj, out: list[float]) -> None:
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        out.append(float(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _collect_numbers(v, out)


def check_narrative(text: str, facts: dict) -> list[str]:
    nums: list[float] = []
    _collect_numbers(facts, nums)
    return scan(text, allowed_dates=[], allowed_money=nums, allowed_percents=nums, allowed_numbers=nums)


def template_narrative(stats: dict, spec: DisruptionSpec) -> tuple[str, str]:
    s = stats["scope"]
    if s["orders_delayed"] == 0:
        return (
            "No active orders are delayed by this disruption.",
            f"The disruption touches {s['orders_in_scope']} active orders geographically, but none of their delivery "
            "timelines overlap the disruption window. No customer action is required.",
        )
    headline = (
        f"{s['orders_delayed']} active orders are delayed; {s['orders_breaching']} will miss their promised delivery date."
    )
    cats = ", ".join(c["category"] for c in stats["top_categories"][:3])
    sev = stats["severity"]
    body = (
        f"{spec.title}. The disruption delays {s['orders_delayed']} of {s['orders_in_scope']} orders in scope, "
        f"affecting {s['customers_delayed']} customers, {s['sellers_delayed']} sellers and {s['skus_delayed']} SKUs "
        f"(R$ {s['gmv_delayed_brl']:,.2f} in order value). Shipments are held {s['avg_hold_days']} days on average, "
        f"and {s['orders_breaching']} orders will now arrive after the promised date. "
        f"Severity: {sev.get('critical', 0)} critical, {sev.get('high', 0)} high, {sev.get('medium', 0)} medium and "
        f"{sev.get('low', 0)} low. The most affected categories are {cats}. "
        f"The mitigation plan is estimated to cost R$ {stats['cost']['total_brl']:,.2f} in refunds, vouchers and reroutes."
    )
    return headline, body
