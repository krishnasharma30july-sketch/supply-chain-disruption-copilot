"""Pure analysis steps shared by the preview endpoint, the run pipeline and the backtest."""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel

from app.core.policy_config import PolicyConfig, ScoringConfig
from app.domain.models import ConceptResolution, DisruptionSpec, MitigationDecision, OrderImpact
from app.services.impact import compute_impacts
from app.services.policy_engine import decide_order
from app.services.reference import ReferenceData
from app.services.scope import ConceptFilter, LineMatch, match_lines
from app.services.scoring import apply_scores


@dataclass
class ScopeResult:
    resolutions: list[ConceptResolution]
    concept_filter: ConceptFilter
    matched: dict[str, list[LineMatch]]
    warnings: list[str] = field(default_factory=list)


def resolve_scope(spec: DisruptionSpec, ref: ReferenceData) -> ScopeResult:
    warnings: list[str] = []
    resolutions, flt = ref.resolver.resolve(spec.category_terms)
    if spec.category_terms and not flt.active:
        warnings.append(
            "Product terms " + ", ".join(repr(t) for t in spec.category_terms)
            + " did not match any category, class or size profile; no product filter was applied."
        )
    matched = match_lines(spec, ref.lines, flt)
    return ScopeResult(resolutions=resolutions, concept_filter=flt, matched=matched, warnings=warnings)


def compute_order_impacts(
    spec: DisruptionSpec, ref: ReferenceData, matched: dict[str, list[LineMatch]], scoring: ScoringConfig
) -> list[OrderImpact]:
    impacts = compute_impacts(ref.lines_by_order, matched, spec, ref.engine_context())
    apply_scores(impacts, ref.value_sorted, ref.seller_stats, scoring)
    impacts.sort(key=lambda i: (-i.severity_score, -i.days_late, i.order_id))
    return impacts


def decide(spec: DisruptionSpec, ref: ReferenceData, impacts: list[OrderImpact], policy: PolicyConfig) -> list[MitigationDecision]:
    pc = ref.policy_context(spec, policy)
    return [decide_order(imp, pc) for imp in impacts]


class PreviewCounts(BaseModel):
    orders_in_scope: int
    orders_delayed: int
    orders_breaching: int
    customers_delayed: int
    sellers_delayed: int
    items_delayed: int
    gmv_delayed_brl: float
    warnings: list[str] = []


def preview(spec: DisruptionSpec, ref: ReferenceData, scoring: ScoringConfig) -> PreviewCounts:
    scope = resolve_scope(spec, ref)
    impacts = compute_impacts(ref.lines_by_order, scope.matched, spec, ref.engine_context())
    delayed = [i for i in impacts if i.affected]
    return PreviewCounts(
        orders_in_scope=len(impacts),
        orders_delayed=len(delayed),
        orders_breaching=sum(1 for i in delayed if i.days_late_calendar > 0),
        customers_delayed=len({i.customer_unique_id for i in delayed}),
        sellers_delayed=len({li.seller_id for i in delayed for li in i.lines if li.affected}),
        items_delayed=sum(1 for i in delayed for li in i.lines if li.affected),
        gmv_delayed_brl=round(sum(i.value_brl for i in delayed), 2),
        warnings=scope.warnings,
    )
