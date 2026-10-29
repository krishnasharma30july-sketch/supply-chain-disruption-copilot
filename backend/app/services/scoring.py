"""Severity score (0-100) with an explainable per-component breakdown (config/scoring.yaml)."""

from __future__ import annotations

from bisect import bisect_right

from app.core.policy_config import ScoringConfig
from app.domain.models import OrderImpact, SellerStat, SeverityComponent, Tier

DEFAULT_SELLER_LATE_RATE = 0.08


def _weight_value(kg: float, profile: str, cfg: ScoringConfig) -> float:
    if profile == "BULKY":
        return 1.0
    for tier in cfg.weight_tiers:
        if kg <= tier.max_kg:
            return tier.value
    return 1.0


def tier_for(score: float, cfg: ScoringConfig) -> Tier:
    if score >= cfg.tiers["critical"]:
        return "critical"
    if score >= cfg.tiers["high"]:
        return "high"
    if score >= cfg.tiers["medium"]:
        return "medium"
    return "low"


def score_order(
    imp: OrderImpact,
    value_sorted: list[float],
    seller_stats: dict[str, SellerStat],
    cfg: ScoringConfig,
) -> tuple[float, Tier, list[SeverityComponent]]:
    if not imp.affected:
        return 0.0, "low", [
            SeverityComponent(
                name="not_delayed", value=0, weight=0, points=0,
                explanation="The order is in the disruption's geography but its timeline is not affected.",
            )
        ]
    w = cfg.weights
    affected_lines = [li for li in imp.lines if li.affected]

    breach = min(imp.days_late / cfg.breach_days_cap, 1.0)
    prob = imp.p_breach
    affected_kg = sum(li.billable_weight_kg for li in affected_lines)
    affected_profile = "BULKY" if any(li.handling_profile == "BULKY" for li in affected_lines) else "OTHER"
    weight_v = _weight_value(affected_kg, affected_profile, cfg)
    value_pct = bisect_right(value_sorted, imp.value_brl) / len(value_sorted) if value_sorted else 0.5
    rates = []
    for li in affected_lines:
        st = seller_stats.get(li.seller_id)
        rates.append(st.late_delivery_rate if st and st.late_delivery_rate is not None else DEFAULT_SELLER_LATE_RATE)
    seller_v = min(max(rates, default=DEFAULT_SELLER_LATE_RATE) / cfg.seller_late_rate_cap, 1.0)
    sellers = {li.seller_id for li in imp.lines}
    if len(sellers) > 1:
        complexity = 1.0
    elif len(affected_lines) < len(imp.lines):
        complexity = 0.7
    elif len(imp.lines) > 1:
        complexity = 0.4
    else:
        complexity = 0.0

    comps = [
        SeverityComponent(
            name="sla_breach", value=round(breach, 3), weight=w["sla_breach"],
            points=round(breach * w["sla_breach"], 2),
            explanation=f"{imp.days_late:.1f} days past the promised date (capped at {cfg.breach_days_cap:g}).",
        ),
        SeverityComponent(
            name="breach_probability", value=round(prob, 3), weight=w["breach_probability"],
            points=round(prob * w["breach_probability"], 2),
            explanation=f"{prob:.0%} chance the delivery misses the promised date given lane variability.",
        ),
        SeverityComponent(
            name="freight_weight", value=round(weight_v, 3), weight=w["freight_weight"],
            points=round(weight_v * w["freight_weight"], 2),
            explanation=f"{affected_kg:.1f} kg billable on delayed items"
            + (" (bulky freight)." if affected_profile == "BULKY" else "."),
        ),
        SeverityComponent(
            name="order_value", value=round(value_pct, 3), weight=w["order_value"],
            points=round(value_pct * w["order_value"], 2),
            explanation=f"Order value R$ {imp.value_brl:.2f} is above {value_pct:.0%} of active orders.",
        ),
        SeverityComponent(
            name="seller_reliability", value=round(seller_v, 3), weight=w["seller_reliability"],
            points=round(seller_v * w["seller_reliability"], 2),
            explanation=f"Seller historical late-delivery rate {max(rates, default=0):.0%}.",
        ),
        SeverityComponent(
            name="complexity", value=complexity, weight=w["complexity"],
            points=round(complexity * w["complexity"], 2),
            explanation=f"{len(imp.lines)} item(s) from {len(sellers)} seller(s); {len(affected_lines)} delayed.",
        ),
    ]
    score = round(sum(c.points for c in comps), 1)
    return score, tier_for(score, cfg), comps


def apply_scores(
    impacts: list[OrderImpact],
    value_sorted: list[float],
    seller_stats: dict[str, SellerStat],
    cfg: ScoringConfig,
) -> None:
    for imp in impacts:
        score, tier, comps = score_order(imp, value_sorted, seller_stats, cfg)
        imp.severity_score = score
        imp.severity_tier = tier
        imp.severity_components = comps
