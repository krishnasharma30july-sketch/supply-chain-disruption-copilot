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


class AssumptionsConfig(BaseModel):
    version: str
    params: AssumptionParams
    items: list[AssumptionItem]


class WeightTier(BaseModel):
    max_kg: float
    value: float


class ScoringConfig(BaseModel):
    version: str
    weights: dict[str, float]
    breach_days_cap: float
    seller_late_rate_cap: float
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

    def tier_for(self, days_late: int) -> CompensationTier | None:
        for tier in self.compensation_tiers:
            if days_late >= tier.min_days and (tier.max_days is None or days_late <= tier.max_days):
                return tier
        return None


class ConceptKeywords(BaseModel):
    profiles: dict[str, list[str]]
    classes: dict[str, list[str]]


class CategoryClassesConfig(BaseModel):
    version: str
    manual_translations: dict[str, str]
    default_class: str
    classes: dict[str, list[str]]
    concept_keywords: ConceptKeywords

    def class_of(self, category_pt: str | None) -> tuple[str, float]:
        if not category_pt:
            return self.default_class, 0.3
        for cls, cats in self.classes.items():
            if category_pt in cats:
                return cls, 1.0
        return self.default_class, 0.9


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
