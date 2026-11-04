"""Typed loaders for the versioned YAML configuration (policy, scoring, classes, assumptions)."""

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

from app.core.config import get_settings


class AssumptionItem(BaseModel):
    id: str
    title: str
    statement: str
    impact: str


class AssumptionParams(BaseModel):
    origin_dwell_days: float
    recovery_factor: float
    partial_impact_factor: float = Field(default=1.0, gt=0, le=1)
    last_mile_days: float
    volumetric_divisor: float
    parcel_max_kg: float
    parcel_max_dim_cm: float
    medium_max_kg: float
    bulky_dim_cm: float
    bulky_sum_dims_cm: float
    heavy_min_kg: float
    lane_min_samples: int
    alt_seller_recency_days: int

    @model_validator(mode="after")
    def _check(self) -> "AssumptionParams":
        for name in ("volumetric_divisor", "parcel_max_kg", "parcel_max_dim_cm", "medium_max_kg", "bulky_dim_cm",
                     "bulky_sum_dims_cm", "heavy_min_kg", "lane_min_samples", "alt_seller_recency_days"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be greater than 0")
        for name in ("origin_dwell_days", "recovery_factor", "last_mile_days"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must not be negative")
        if self.recovery_factor > 1:
            raise ValueError("recovery_factor is a share of the disruption duration: 0 to 1")
        if self.parcel_max_kg >= self.medium_max_kg:
            raise ValueError("parcel_max_kg must be below medium_max_kg")
        return self


class AssumptionsConfig(BaseModel):
    version: str
    params: AssumptionParams
    items: list[AssumptionItem]

    @model_validator(mode="after")
    def _unique_ids(self) -> "AssumptionsConfig":
        ids = [i.id for i in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("assumption ids must be unique")
        return self


class WeightTier(BaseModel):
    max_kg: float
    value: float


class ComplexityLevels(BaseModel):
    """Share (0..1) of the complexity points for each kind of order."""

    multi_seller: float = 1.0
    mixed_impact: float = 0.7   # some items of the order are not affected
    multi_item: float = 0.4
    single_item: float = 0.0


class ScoringConfig(BaseModel):
    version: str
    weights: dict[str, float]
    breach_days_cap: float
    seller_late_rate_cap: float
    default_seller_late_rate: float = 0.08  # used when a seller has no delivery history
    complexity: ComplexityLevels = Field(default_factory=ComplexityLevels)
    weight_tiers: list[WeightTier]
    tiers: dict[str, float]

    @model_validator(mode="after")
    def _check(self) -> "ScoringConfig":
        expected = {"sla_breach", "breach_probability", "freight_weight", "order_value", "seller_reliability", "complexity"}
        if set(self.weights) != expected:
            raise ValueError(f"scoring weights must be exactly {sorted(expected)}")
        if abs(sum(self.weights.values()) - 100) > 1e-6:
            raise ValueError("scoring weights must sum to 100")
        if not {"critical", "high", "medium"} <= set(self.tiers):
            raise ValueError("tiers must define critical, high and medium")
        if self.breach_days_cap <= 0 or self.seller_late_rate_cap <= 0:
            raise ValueError("breach_days_cap and seller_late_rate_cap must be greater than 0")
        if not 0 <= self.default_seller_late_rate <= 1:
            raise ValueError("default_seller_late_rate must be between 0 and 1")
        kgs = [t.max_kg for t in self.weight_tiers]
        if kgs != sorted(kgs) or len(set(kgs)) != len(kgs):
            raise ValueError("weight_tiers must be in ascending order of max_kg")
        if any(not 0 <= t.value <= 1 for t in self.weight_tiers):
            raise ValueError("weight tier values must be between 0 and 1")
        if not self.tiers["critical"] > self.tiers["high"] > self.tiers["medium"] > 0:
            raise ValueError("tiers must satisfy critical > high > medium > 0")
        if any(not 0 <= v <= 1 for v in self.complexity.model_dump().values()):
            raise ValueError("complexity levels must be between 0 and 1")
        return self


class AltSellerPolicy(BaseModel):
    max_price_delta: float
    min_gain_days: float


class AltRoutePolicy(BaseModel):
    min_gain_days: float
    extra_transit_days: float
    freight_uplift: float


class CompensationTier(BaseModel):
    min_days: int
    max_days: int | None
    freight_refund: bool
    voucher_pct: float
    voucher_cap_brl: float
    label: str


class PolicyConfig(BaseModel):
    version: str
    monitor_max_p_breach: float
    notify_min_shift_days: float
    perishable_cancel_days_late: int
    essential_offer_cancel_days_late: int
    bulky_offer_cancel_days_late: int
    general_offer_cancel_days_late: int
    low_class_confidence: float
    alt_seller: AltSellerPolicy
    alt_route: AltRoutePolicy
    compensation_tiers: list[CompensationTier] = Field(min_length=1)
    human_callback_tiers: list[str]

    @model_validator(mode="after")
    def _check(self) -> "PolicyConfig":
        if not 0 <= self.monitor_max_p_breach <= 1:
            raise ValueError("monitor_max_p_breach must be between 0 and 1")
        for name in ("notify_min_shift_days", "perishable_cancel_days_late", "essential_offer_cancel_days_late",
                     "bulky_offer_cancel_days_late", "general_offer_cancel_days_late"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must not be negative")
        if self.alt_seller.max_price_delta < 0 or self.alt_seller.min_gain_days < 0:
            raise ValueError("alt_seller limits must not be negative")
        ar = self.alt_route
        if ar.min_gain_days < 0 or ar.extra_transit_days < 0 or ar.freight_uplift < 0:
            raise ValueError("alt_route values must not be negative")
        tiers = self.compensation_tiers
        for i, t in enumerate(tiers):
            if t.min_days < 1:
                raise ValueError("compensation tiers start at 1 day late")
            if not 0 <= t.voucher_pct <= 100 or t.voucher_cap_brl < 0:
                raise ValueError("voucher_pct must be 0 to 100 and voucher_cap_brl must not be negative")
            last = i == len(tiers) - 1
            if last and t.max_days is not None:
                raise ValueError("the last compensation tier must be open-ended (max_days: null)")
            if not last:
                if t.max_days is None or t.max_days < t.min_days:
                    raise ValueError("a compensation tier's max_days must be at least its min_days")
                if tiers[i + 1].min_days != t.max_days + 1:
                    raise ValueError("compensation tiers must be contiguous: no gaps and no overlaps")
        return self

    def tier_for(self, days_late: int) -> CompensationTier | None:
        for tier in self.compensation_tiers:
            if days_late >= tier.min_days and (tier.max_days is None or days_late <= tier.max_days):
                return tier
        return None


class ClassConfidence(BaseModel):
    missing_category: float = 0.3   # the product has no category at all
    unlisted_category: float = 0.9  # a category that is not in the curated class map (falls back to the default class)


class ConceptKeywords(BaseModel):
    profiles: dict[str, list[str]]
    classes: dict[str, list[str]]


class CategoryClassesConfig(BaseModel):
    version: str
    manual_translations: dict[str, str]
    default_class: str
    confidence: ClassConfidence = Field(default_factory=ClassConfidence)
    classes: dict[str, list[str]]
    concept_keywords: ConceptKeywords

    def class_of(self, category_pt: str | None) -> tuple[str, float]:
        if not category_pt:
            return self.default_class, self.confidence.missing_category
        for cls, cats in self.classes.items():
            if category_pt in cats:
                return cls, 1.0
        return self.default_class, self.confidence.unlisted_category


def _load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@lru_cache
def get_assumptions() -> AssumptionsConfig:
    return AssumptionsConfig.model_validate(_load_yaml(get_settings().config_dir / "assumptions.yaml"))


@lru_cache
def get_scoring() -> ScoringConfig:
    return ScoringConfig.model_validate(_load_yaml(get_settings().config_dir / "scoring.yaml"))


@lru_cache
def get_policy() -> PolicyConfig:
    return PolicyConfig.model_validate(_load_yaml(get_settings().config_dir / "policy.yaml"))


@lru_cache
def get_category_classes() -> CategoryClassesConfig:
    return CategoryClassesConfig.model_validate(_load_yaml(get_settings().config_dir / "category_classes.yaml"))
