"""Policy-as-code mitigation engine (rules R0..R8, thresholds in config/policy.yaml).

Each affected line gets a plan (cancel / reroute / delay); plans roll up into one order decision with
deterministic compensation. Every rule cites the knowledge-base section that justifies it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.core.policy_config import AssumptionParams, PolicyConfig
from app.domain.models import (
    Action,
    AltOption,
    AltSeller,
    Compensation,
    DisruptionSpec,
    LineDecision,
    LineImpact,
    MitigationDecision,
    OrderImpact,
    SellerStat,
)
from app.services.eta import LaneBook, days_between
from app.services.scope import geo_match

RULE_REFS: dict[str, list[str]] = {
    "R0": ["mitigation_playbook#monitoring"],
    "R1": ["mitigation_playbook#proactive-notification"],
    "R2": ["mitigation_playbook#perishable-goods", "compensation_policy#cancellation-and-refunds"],
    "R3": ["mitigation_playbook#reroute-via-alternate-seller"],
    "R4": ["mitigation_playbook#reroute-via-alternate-route"],
    "R5": ["mitigation_playbook#bulky-and-heavy-freight", "compensation_policy#compensation-tiers"],
    "R6": ["mitigation_playbook#gifts-and-seasonal-items", "compensation_policy#compensation-tiers"],
    "R7": ["mitigation_playbook#long-delays", "compensation_policy#compensation-tiers"],
    "R8": ["mitigation_playbook#standard-delays", "compensation_policy#compensation-tiers"],
    "ADJ": ["mitigation_playbook#mixed-orders-and-split-shipments", "compensation_policy#compensation-tiers"],
    "ESSENTIAL": ["mitigation_playbook#essential-items"],
    "SPLIT": ["mitigation_playbook#mixed-orders-and-split-shipments"],
    "CALLBACK": ["compensation_policy#critical-cases"],
    "OFFER_CANCEL": ["compensation_policy#cancellation-and-refunds"],
}

# Disruptions of the transport network; alternate carriers/corridors only help with these.
TRANSPORT_DISRUPTIONS = {"weather", "strike", "infrastructure", "capacity", "other"}

# Order-level primary action when items differ (most customer-impacting first).
ACTION_PRIORITY: list[Action] = ["CANCEL_REFUND", "DELAY_WITH_COMPENSATION", "REROUTE_ALT_SELLER", "REROUTE_ALT_ROUTE"]


@dataclass
class PolicyContext:
    as_of: datetime
    spec: DisruptionSpec
    lanes: LaneBook
    seller_stats: dict[str, SellerStat]
    seller_geo: dict[str, tuple[str, str, float | None, float | None]]  # seller_id -> (city, state, lat, lng)
    alt_sellers: dict[str, list[AltSeller]]
    global_handling_days: float
    policy: PolicyConfig
    params: AssumptionParams


@dataclass
class LinePlan:
    line: LineImpact
    action: Action
    rule_id: str
    reason: str
    offer_cancellation: bool = False
    eta: datetime | None = None
    eta_p90: datetime | None = None
    extra_cost: float = 0.0
    alternatives: list[AltOption] = field(default_factory=list)
    refs: list[str] = field(default_factory=list)


def _handling_days(seller_id: str, pc: PolicyContext) -> float:
    st = pc.seller_stats.get(seller_id)
    if st is not None and st.median_handling_days is not None:
        return st.median_handling_days
    return pc.global_handling_days


def _seller_in_disruption(seller_id: str, state: str, city: str, pc: PolicyContext) -> bool:
    spec = pc.spec
    if spec.nationwide:
        return True
    if seller_id in spec.seller_ids:
        return True
    geo = pc.seller_geo.get(seller_id)
    lat, lng = (geo[2], geo[3]) if geo else (None, None)
    return bool(geo_match(spec.origin, city, state, lat, lng))


def _alt_seller_options(li: LineImpact, imp: OrderImpact, pc: PolicyContext) -> list[AltOption]:
    options: list[AltOption] = []
    for alt in pc.alt_sellers.get(li.product_id, []):
        if alt.seller_id == li.seller_id:
            continue
        if _seller_in_disruption(alt.seller_id, alt.seller_state, alt.seller_city, pc):
            options.append(
                AltOption(
                    kind="alt_seller", order_item_id=li.order_item_id, seller_id=alt.seller_id,
                    seller_state=alt.seller_state, seller_city=alt.seller_city,
                    reason="Rejected: alternate seller is inside the disruption scope.",
                )
            )
            continue
        lane = pc.lanes.get(alt.seller_state, imp.cust_state)
        handover = pc.as_of + timedelta(days=_handling_days(alt.seller_id, pc))
        eta = handover + timedelta(days=lane.p50)
        gain = days_between(eta, li.projected_eta)
        delta = (alt.avg_price - li.price) / li.price if li.price > 0 else 0.0
        accepted = delta <= pc.policy.alt_seller.max_price_delta and gain >= pc.policy.alt_seller.min_gain_days
        if accepted:
            reason = f"Accepted: delivers {gain:.1f} days sooner at {delta:+.0%} price."
        elif delta > pc.policy.alt_seller.max_price_delta:
            reason = f"Rejected: price {delta:+.0%} exceeds the {pc.policy.alt_seller.max_price_delta:.0%} limit."
        else:
            reason = f"Rejected: gain of {gain:.1f} days is below the {pc.policy.alt_seller.min_gain_days:g}-day minimum."
        options.append(
            AltOption(
                kind="alt_seller", order_item_id=li.order_item_id, seller_id=alt.seller_id,
                seller_state=alt.seller_state, seller_city=alt.seller_city, eta=eta,
                gain_days=round(gain, 2), price_delta_pct=round(delta * 100, 1),
                extra_cost_brl=round(max(0.0, alt.avg_price - li.price), 2), accepted=accepted, reason=reason,
            )
        )
    return options


def _line_plan(li: LineImpact, imp: OrderImpact, pc: PolicyContext) -> LinePlan:
    pol = pc.policy
    days_late = imp.days_late_calendar
    legs = {h.leg for h in li.legs}
    spec = pc.spec

    # R2 perishable goods
    if li.product_class == "PERISHABLE" and days_late >= pol.perishable_cancel_days_late:
        return LinePlan(
            line=li, action="CANCEL_REFUND", rule_id="R2", refs=RULE_REFS["R2"],
            reason=f"Perishable item would arrive {days_late} days late; cancel and refund in full.",
        )

    alternatives: list[AltOption] = []

    # R3 alternate seller (not yet handed over, destination side not blocked)
    if imp.stage in ("pending_approval", "awaiting_pickup") and "last_mile" not in legs:
        options = _alt_seller_options(li, imp, pc)
        alternatives.extend(options)
        accepted = sorted(
            (o for o in options if o.accepted), key=lambda o: (-o.gain_days, o.price_delta_pct or 0.0)
        )
        if accepted:
            best = accepted[0]
            lane = pc.lanes.get(best.seller_state or li.seller_state, imp.cust_state)
            handover = pc.as_of + timedelta(days=_handling_days(best.seller_id or "", pc))
            return LinePlan(
                line=li, action="REROUTE_ALT_SELLER", rule_id="R3", refs=RULE_REFS["R3"],
                reason=(
                    f"Same product available from a seller in {best.seller_city}/{best.seller_state} outside the "
                    f"disruption; {best.gain_days:.1f} days faster."
                ),
                eta=best.eta, eta_p90=handover + timedelta(days=lane.p90),
                extra_cost=best.extra_cost_brl, alternatives=alternatives,
            )

    # R4 alternate route for parcels blocked at origin / on the linehaul (transport disruptions only)
    if li.handling_profile == "PARCEL" and legs and legs <= {"first_mile", "linehaul"}:
        if spec.disruption_type not in TRANSPORT_DISRUPTIONS:
            alternatives.append(
                AltOption(
                    kind="alt_route", order_item_id=li.order_item_id,
                    reason="Rejected: the problem is at the seller/supplier, so the goods are not ready to ship; "
                    "another carrier cannot help.",
                )
            )
        elif spec.nationwide or spec.all_modes:
            alternatives.append(
                AltOption(
                    kind="alt_route", order_item_id=li.order_item_id,
                    reason="Rejected: the disruption is nationwide or blocks all transport modes.",
                )
            )
        else:
            lane = pc.lanes.get(li.seller_state, imp.cust_state)
            depart = max(pc.as_of, li.handover_expected)
            eta = depart + timedelta(days=lane.p50 + pol.alt_route.extra_transit_days)
            eta_p90 = depart + timedelta(days=lane.p90 + pol.alt_route.extra_transit_days)
            gain = days_between(eta, li.projected_eta)
            cost = round(li.freight * pol.alt_route.freight_uplift, 2)
            accepted = gain >= pol.alt_route.min_gain_days
            alternatives.append(
                AltOption(
                    kind="alt_route", order_item_id=li.order_item_id, eta=eta, gain_days=round(gain, 2),
                    extra_cost_brl=cost, accepted=accepted,
                    reason=(
                        f"Accepted: alternate carrier/air corridor saves {gain:.1f} days for R$ {cost:.2f} extra."
                        if accepted
                        else f"Rejected: saves only {gain:.1f} days (minimum {pol.alt_route.min_gain_days:g})."
                    ),
                )
            )
            if accepted:
                return LinePlan(
                    line=li, action="REROUTE_ALT_ROUTE", rule_id="R4", refs=RULE_REFS["R4"],
                    reason=f"Small parcel ({li.billable_weight_kg:.1f} kg) can bypass the blocked leg; {gain:.1f} days faster.",
                    eta=eta, eta_p90=eta_p90, extra_cost=cost, alternatives=alternatives,
                )

    eta, eta_p90 = li.projected_eta, li.projected_eta_p90
    # R5 bulky freight
    if li.handling_profile == "BULKY":
        offer = days_late > pol.bulky_offer_cancel_days_late
        return LinePlan(
            line=li, action="DELAY_WITH_COMPENSATION", rule_id="R5", refs=RULE_REFS["R5"],
            reason=f"Bulky freight ({li.billable_weight_kg:.1f} kg billable) is uneconomical to reroute; keep on route and compensate.",
            offer_cancellation=offer, eta=eta, eta_p90=eta_p90, alternatives=alternatives,
        )
    # R6 gifts & seasonal
    if li.product_class == "GIFT_SEASONAL":
        return LinePlan(
            line=li, action="DELAY_WITH_COMPENSATION", rule_id="R6", refs=RULE_REFS["R6"],
            reason="Gift/seasonal item bought for a date; compensate and offer a no-fee cancellation.",
            offer_cancellation=True, eta=eta, eta_p90=eta_p90, alternatives=alternatives,
        )
    # R7 long delays
    if days_late > pol.general_offer_cancel_days_late:
        return LinePlan(
            line=li, action="DELAY_WITH_COMPENSATION", rule_id="R7", refs=RULE_REFS["R7"],
            reason=f"Delay of {days_late} days exceeds {pol.general_offer_cancel_days_late}; compensate and offer cancellation.",
            offer_cancellation=True, eta=eta, eta_p90=eta_p90, alternatives=alternatives,
        )
    # R8 standard delay (+ essential-items cancellation offer)
    essential = li.product_class == "ESSENTIAL" and days_late >= pol.essential_offer_cancel_days_late
    return LinePlan(
        line=li, action="DELAY_WITH_COMPENSATION", rule_id="R8",
        refs=RULE_REFS["R8"] + (RULE_REFS["ESSENTIAL"] if essential else []),
        reason="Keep on route with a clear update and tiered compensation."
        + (" Essential item: no-fee cancellation offered." if essential else ""),
        offer_cancellation=essential, eta=eta, eta_p90=eta_p90, alternatives=alternatives,
    )


def _calendar_late(eta: datetime, sla: datetime) -> int:
    return max(0, (eta.date() - sla.date()).days)


def finalize(
    imp: OrderImpact,
    plans: list[LinePlan],
    pc: PolicyContext,
    *,
    decided_by: str = "rules",
    rationale: str | None = None,
) -> MitigationDecision:
    """Roll line plans up into an order decision with deterministic compensation and cost."""
    pol = pc.policy
    planned = {p.line.order_item_id: p for p in plans}
    unaffected = [li for li in imp.lines if li.order_item_id not in planned]
    cancelled = [p for p in plans if p.action == "CANCEL_REFUND"]
    delivering_etas = [(p.eta, p.eta_p90) for p in plans if p.action != "CANCEL_REFUND" and p.eta is not None]
    delivering_etas += [(li.projected_eta, li.projected_eta_p90) for li in unaffected]

    if delivering_etas:
        resolution_eta = max(e for e, _ in delivering_etas)
        resolution_p90 = max(p for _, p in delivering_etas)
        resolution_late = _calendar_late(resolution_eta, imp.sla_ts)
    else:
        resolution_eta, resolution_p90, resolution_late = imp.projected_eta, imp.projected_eta_p90, 0

    actions = {p.action for p in plans}
    primary: Action = next(a for a in ACTION_PRIORITY if a in actions)
    primary_plan = next(p for p in plans if p.action == primary)

    # Compensation
    comp = Compensation()
    cancel_refund = sum(p.line.price + p.line.freight for p in cancelled)
    delivering_lines = [p.line for p in plans if p.action != "CANCEL_REFUND"] + unaffected
    if delivering_lines and resolution_late > 0:
        tier = pol.tier_for(resolution_late)
        if tier is not None:
            comp.tier_label = tier.label
            if tier.freight_refund:
                comp.freight_refund_brl = round(sum(li.freight for li in delivering_lines), 2)
            if tier.voucher_pct > 0:
                items_value = sum(li.price for li in delivering_lines)
                comp.voucher_pct = tier.voucher_pct
                comp.voucher_brl = round(min(items_value * tier.voucher_pct / 100.0, tier.voucher_cap_brl), 2)
    if cancelled:
        comp.tier_label = "Proactive cancellation" if not comp.tier_label else comp.tier_label + " + cancellation"
    comp.refund_total_brl = round(cancel_refund + comp.freight_refund_brl, 2)
    comp.human_callback = imp.severity_tier in pol.human_callback_tiers

    offer = any(p.offer_cancellation for p in plans) or resolution_late > pol.general_offer_cancel_days_late
    split = bool(cancelled and delivering_lines) or bool(unaffected and plans) or len(actions) > 1
    extra = sum(p.extra_cost for p in plans)
    est_cost = round(comp.refund_total_brl + comp.voucher_brl + extra, 2)

    rule_ids = sorted({p.rule_id for p in plans})
    codes = list(rule_ids)
    refs: list[str] = []
    for p in plans:
        for r in p.refs:
            if r not in refs:
                refs.append(r)
    for flag, cond in (("SPLIT", split), ("OFFER_CANCEL", offer), ("CALLBACK", comp.human_callback)):
        if cond:
            codes.append(flag)
            for r in RULE_REFS[flag]:
                if r not in refs:
                    refs.append(r)

    reasons: list[str] = []
    for p in plans:
        if p.reason not in reasons:
            reasons.append(p.reason)
    if split and unaffected:
        reasons.append(f"{len(unaffected)} unaffected item(s) ship now (split shipment).")
    if comp.tier_label and resolution_late > 0:
        reasons.append(f"{resolution_late} day(s) late after mitigation: {comp.tier_label} tier.")

    alternatives = [a for p in plans for a in p.alternatives]
    return MitigationDecision(
        order_id=imp.order_id,
        action=primary,
        rule_id=primary_plan.rule_id,
        reason_codes=codes,
        explanation=" ".join(reasons),
        line_decisions=[
            LineDecision(order_item_id=p.line.order_item_id, action=p.action, rule_id=p.rule_id, reason=p.reason)
            for p in plans
        ],
        split_shipment=split,
        offer_cancellation=offer,
        alternatives=alternatives,
        resolution_eta=resolution_eta,
        resolution_eta_p90=max(resolution_p90, resolution_eta),
        resolution_days_late=resolution_late,
        compensation=comp,
        est_cost_brl=est_cost,
        decided_by="llm_adjudicator" if decided_by == "llm_adjudicator" else "rules",
        llm_rationale=rationale,
        policy_refs=refs,
    )


def _no_action(imp: OrderImpact, action: Action, rule: str, reason: str) -> MitigationDecision:
    return MitigationDecision(
        order_id=imp.order_id,
        action=action,
        rule_id=rule,
        reason_codes=[rule],
        explanation=reason,
        resolution_eta=imp.projected_eta,
        resolution_eta_p90=imp.projected_eta_p90,
        resolution_days_late=imp.days_late_calendar,
        policy_refs=list(RULE_REFS[rule]),
        allowed_actions=[action],
    )


def decide_order(imp: OrderImpact, pc: PolicyContext) -> MitigationDecision:
    pol = pc.policy
    if not imp.affected:
        return _no_action(imp, "MONITOR", "R0", "In the disruption's geography, but the delivery timeline is not affected.")
    if imp.days_late_calendar == 0:
        shift = days_between(imp.baseline_eta, imp.projected_eta)
        if shift >= pol.notify_min_shift_days:
            return _no_action(
                imp, "NOTIFY_ONLY", "R1",
                f"Delivery moves by {shift:.1f} days but still arrives by the promised date; send a heads-up.",
            )
        if imp.p_breach < pol.monitor_max_p_breach:
            return _no_action(
                imp, "MONITOR", "R0",
                f"Delivery moves by {shift:.1f} days but stays within the promise with low breach risk; monitor silently.",
            )
        return _no_action(
            imp, "NOTIFY_ONLY", "R1", f"Breach risk is {imp.p_breach:.0%}; proactively inform the customer."
        )

    plans = [_line_plan(li, imp, pc) for li in imp.lines if li.affected]
    decision = finalize(imp, plans, pc)

    review: list[str] = []
    if len({p.action for p in plans}) > 1:
        review.append("Items in this order fall under different actions: " + ", ".join(sorted({p.action for p in plans})) + ".")
    low_conf = [p.line for p in plans if p.line.class_confidence < pol.low_class_confidence]
    if low_conf:
        review.append("Product type is uncertain (no category in the catalog); the rule decision may not fit.")
    decision.needs_review = bool(review)
    decision.review_reasons = review
    decision.allowed_actions = sorted({p.action for p in plans} | {"DELAY_WITH_COMPENSATION"})
    return decision


def delay_all(imp: OrderImpact, pc: PolicyContext, rationale: str) -> MitigationDecision:
    """Adjudicator override: every delayed item stays on its original route with compensation."""
    plans = [
        LinePlan(
            line=li, action="DELAY_WITH_COMPENSATION", rule_id="ADJ", refs=RULE_REFS["ADJ"],
            reason="Reviewer decision: keep the delayed items on their original route and compensate.",
            eta=li.projected_eta, eta_p90=li.projected_eta_p90,
        )
        for li in imp.lines
        if li.affected
    ]
    decision = finalize(imp, plans, pc, decided_by="llm_adjudicator", rationale=rationale)
    decision.allowed_actions = ["DELAY_WITH_COMPENSATION"]
    return decision
