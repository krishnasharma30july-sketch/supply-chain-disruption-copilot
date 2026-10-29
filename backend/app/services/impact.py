"""Logistics impact: per-leg holds, projected ETA, breach probability; aggregated per order."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.core.policy_config import AssumptionParams
from app.domain.models import DisruptionSpec, LegHold, LineImpact, LineRecord, OrderImpact, SellerStat
from app.services.eta import (
    PROFILE_RANK,
    LaneBook,
    baseline_eta,
    days_between,
    handover_expected,
    prob_exceeds_given,
)
from app.services.scope import LineMatch


@dataclass
class EngineContext:
    as_of: datetime
    lanes: LaneBook
    seller_stats: dict[str, SellerStat]
    global_handling_days: float
    global_approval_days: float
    params: AssumptionParams


def _stuck_until_end(window_start: datetime, window_end: datetime, spec: DisruptionSpec, as_of: datetime) -> float:
    """Days held when a shipment is inside the disrupted area during [window_start, window_end):
    if that presence overlaps the disruption (and is not already in the past), it waits until the disruption ends."""
    start, end = spec.start_at, spec.end_at
    stuck_from = max(start, as_of, window_start)
    if stuck_from < min(end, window_end):
        return days_between(stuck_from, end)
    return 0.0


def _leg_holds(
    line: LineRecord,
    match: LineMatch,
    handover: datetime,
    eta0: datetime,
    spec: DisruptionSpec,
    ctx: EngineContext,
) -> list[LegHold]:
    """Days each exposed leg is held by the disruption window [start, end)."""
    start, end = spec.start_at, spec.end_at
    as_of = ctx.as_of
    p = ctx.params
    dwell = timedelta(days=p.origin_dwell_days)
    holds: list[LegHold] = []

    if "first_mile" in match.legs:
        if line.stage in ("pending_approval", "awaiting_pickup"):
            if start <= handover < end:
                holds.append(
                    LegHold(
                        leg="first_mile",
                        hold_days=days_between(handover, end),
                        note="Carrier handover falls inside the disruption window; pickup waits until it ends.",
                    )
                )
        elif line.stage == "in_transit" and line.carrier_ts is not None:
            held = _stuck_until_end(line.carrier_ts, line.carrier_ts + dwell, spec, as_of)
            if held > 0:
                holds.append(
                    LegHold(
                        leg="first_mile",
                        hold_days=held,
                        note="Shipment left the seller recently and is assumed to still be inside the origin region (A7).",
                    )
                )

    if "linehaul" in match.legs:
        lh_start = max(handover, as_of)
        lh_end = eta0 - timedelta(days=p.last_mile_days)
        if lh_end > lh_start:
            if match.corridor:
                # Nationwide / lane-specific / seller-only scope: the whole inter-state journey is exposed.
                overlap = days_between(max(start, lh_start), min(end, lh_end))
                if overlap > 0:
                    holds.append(LegHold(leg="linehaul", hold_days=overlap,
                                         note="Inter-state transit overlaps a corridor-wide disruption."))
            else:
                # Regional disruption: only the part of the linehaul inside the disrupted region is exposed:
                # leaving the origin region (first day after handover) or entering the destination region.
                if match.origin_hit:
                    held = _stuck_until_end(handover, handover + dwell, spec, as_of)
                    if held > 0:
                        holds.append(LegHold(leg="linehaul", hold_days=held,
                                             note="Still leaving the disrupted origin region when the disruption hits."))
                if match.dest_hit:
                    held = _stuck_until_end(lh_end - dwell, lh_end, spec, as_of)
                    if held > 0:
                        holds.append(LegHold(leg="linehaul", hold_days=held,
                                             note="Enters the disrupted destination region during the disruption."))

    if "last_mile" in match.legs:
        lm_start = max(eta0 - timedelta(days=p.last_mile_days), as_of)
        if lm_start < end and eta0 > start:
            holds.append(
                LegHold(
                    leg="last_mile",
                    hold_days=days_between(max(lm_start, start), end),
                    note="Final delivery falls inside the disruption window; it waits until the window ends.",
                )
            )
    return [h for h in holds if h.hold_days > 0]


def line_impact(
    line: LineRecord,
    match: LineMatch | None,
    spec: DisruptionSpec,
    ctx: EngineContext,
) -> LineImpact:
    lane = ctx.lanes.get(line.seller_state, line.cust_state)
    handover = handover_expected(line, ctx.as_of, ctx.seller_stats, ctx.global_handling_days, ctx.global_approval_days)
    eta0 = baseline_eta(handover, lane, ctx.as_of, "p50")
    eta0_p90 = max(baseline_eta(handover, lane, ctx.as_of, "p90"), eta0)

    holds: list[LegHold] = []
    if match is not None:
        holds = _leg_holds(line, match, handover, eta0, spec, ctx)
    hold = max((h.hold_days for h in holds), default=0.0)
    if hold > 0:
        if spec.explicit_delay_days is not None:
            hold = spec.explicit_delay_days
        else:
            # partial_impact_factor (A8) scales how much of the window a held shipment actually loses.
            hold = hold * ctx.params.partial_impact_factor + spec.recovery_days

    projected = eta0 + timedelta(days=hold)
    projected_p90 = eta0_p90 + timedelta(days=hold)
    available = days_between(handover, line.sla_ts)
    elapsed = max(0.0, days_between(handover, ctx.as_of)) if line.stage == "in_transit" else 0.0
    return LineImpact(
        order_item_id=line.order_item_id,
        product_id=line.product_id,
        seller_id=line.seller_id,
        seller_city=line.seller_city,
        seller_state=line.seller_state,
        category_en=line.category_en,
        category_pt=line.category_pt,
        product_class=line.product_class,
        class_confidence=line.class_confidence,
        handling_profile=line.handling_profile,
        billable_weight_kg=round(line.billable_weight_kg, 2),
        price=line.price,
        freight=line.freight,
        match_reasons=match.reasons if match else [],
        affected=hold > 0,
        handover_expected=handover,
        baseline_eta=eta0,
        projected_eta=projected,
        projected_eta_p90=projected_p90,
        lane=f"{line.seller_state}->{line.cust_state}",
        lane_level=lane.level,
        lane_p50=round(lane.p50, 2),
        lane_p90=round(lane.p90, 2),
        legs=holds,
        hold_days=round(hold, 2),
        p_breach=round(prob_exceeds_given(lane, available - hold, elapsed), 3),
        p_breach_baseline=round(prob_exceeds_given(lane, available, elapsed), 3),
    )


def order_impact(
    order_lines: list[LineRecord],
    matches: dict[int, LineMatch],
    spec: DisruptionSpec,
    ctx: EngineContext,
) -> OrderImpact:
    """Aggregate line impacts. All lines of the order are included; unmatched lines keep their baseline ETA."""
    first = order_lines[0]
    impacts = [line_impact(line, matches.get(line.order_item_id), spec, ctx) for line in order_lines]
    projected = max(li.projected_eta for li in impacts)
    projected_p90 = max(li.projected_eta_p90 for li in impacts)
    baseline = max(li.baseline_eta for li in impacts)
    sla = first.sla_ts
    days_late = max(0.0, days_between(sla, projected))
    profile = max((li.handling_profile for li in impacts), key=lambda p: PROFILE_RANK.get(p, 0))
    classes: list[str] = []
    categories: list[str] = []
    for li in impacts:
        if li.product_class not in classes:
            classes.append(li.product_class)
        if li.category_en not in categories:
            categories.append(li.category_en)
    return OrderImpact(
        order_id=first.order_id,
        customer_id=first.customer_id,
        customer_unique_id=first.customer_unique_id,
        stage=first.stage,
        purchase_ts=first.purchase_ts,
        approved_ts=first.approved_ts,
        carrier_ts=first.carrier_ts,
        sla_ts=sla,
        cust_city=first.cust_city,
        cust_state=first.cust_state,
        value_brl=round(sum(l.price + l.freight for l in order_lines), 2),
        freight_brl=round(sum(l.freight for l in order_lines), 2),
        billable_weight_kg=round(sum(l.billable_weight_kg for l in order_lines), 2),
        handling_profile=profile,
        product_classes=classes,
        categories=categories,
        lines=impacts,
        affected=any(li.affected for li in impacts),
        hold_days=round(max(li.hold_days for li in impacts), 2),
        baseline_eta=baseline,
        projected_eta=projected,
        projected_eta_p90=projected_p90,
        days_late=round(days_late, 2),
        days_late_calendar=max(0, (projected.date() - sla.date()).days),
        baseline_days_late=round(max(0.0, days_between(sla, baseline)), 2),
        p_breach=max(li.p_breach for li in impacts),
        p_breach_baseline=max(li.p_breach_baseline for li in impacts),
    )


def compute_impacts(
    lines_by_order: dict[str, list[LineRecord]],
    matched: dict[str, list[LineMatch]],
    spec: DisruptionSpec,
    ctx: EngineContext,
) -> list[OrderImpact]:
    out: list[OrderImpact] = []
    for order_id, matches in matched.items():
        by_item = {m.line.order_item_id: m for m in matches}
        out.append(order_impact(lines_by_order[order_id], by_item, spec, ctx))
    return out
