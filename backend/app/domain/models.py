"""Domain contracts shared by agents, services, the API and persistence."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

UF_CODES: tuple[str, ...] = (
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA",
    "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
)

Stage = Literal["pending_approval", "awaiting_pickup", "in_transit", "delivered"]
Leg = Literal["first_mile", "linehaul", "last_mile"]
LEGS: tuple[Leg, ...] = ("first_mile", "linehaul", "last_mile")
DisruptionType = Literal["weather", "strike", "infrastructure", "capacity", "supplier", "seller_outage", "other"]
Action = Literal[
    "MONITOR",
    "NOTIFY_ONLY",
    "CANCEL_REFUND",
    "REROUTE_ALT_SELLER",
    "REROUTE_ALT_ROUTE",
    "DELAY_WITH_COMPENSATION",
]
ACTIONS: tuple[Action, ...] = (
    "MONITOR", "NOTIFY_ONLY", "CANCEL_REFUND", "REROUTE_ALT_SELLER", "REROUTE_ALT_ROUTE", "DELAY_WITH_COMPENSATION",
)
Tier = Literal["critical", "high", "medium", "low"]
Role = Literal["origin", "destination"]


# --------------------------------------------------------------------------- disruption spec


class CityRef(BaseModel):
    name: str = Field(description="normalized city name (unaccented, lowercase)")
    state: str
    label: str = Field(description="display label, e.g. 'Sao Paulo, SP'")

    @field_validator("state")
    @classmethod
    def _uf(cls, v: str) -> str:
        v = v.upper()
        if v not in UF_CODES:
            raise ValueError(f"unknown state code {v}")
        return v


class RadiusRef(BaseModel):
    center: CityRef
    km: float = Field(gt=0, le=1500)
    lat: float
    lng: float


class GeoScope(BaseModel):
    states: list[str] = Field(default_factory=list)
    cities: list[CityRef] = Field(default_factory=list)
    radius: RadiusRef | None = None

    @field_validator("states")
    @classmethod
    def _states(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for s in v:
            s = s.upper()
            if s not in UF_CODES:
                raise ValueError(f"unknown state code {s}")
            if s not in out:
                out.append(s)
        return out

    @field_validator("cities")
    @classmethod
    def _cities(cls, v: list[CityRef]) -> list[CityRef]:
        seen: set[tuple[str, str]] = set()
        out: list[CityRef] = []
        for c in v:
            key = (c.name, c.state)
            if key not in seen:
                seen.add(key)
                out.append(c)
        return out

    def is_empty(self) -> bool:
        return not (self.states or self.cities or self.radius)

    def describe(self) -> str:
        parts: list[str] = []
        if self.states:
            parts.append("states " + ", ".join(self.states))
        if self.cities:
            parts.append("cities " + ", ".join(c.label for c in self.cities))
        if self.radius:
            parts.append(f"within {self.radius.km:g} km of {self.radius.center.label}")
        return "; ".join(parts) if parts else "-"


class AmbiguityOption(BaseModel):
    label: str
    kind: Literal["state", "city"]
    state: str
    city: CityRef | None = None


class Ambiguity(BaseModel):
    mention: str
    roles: list[Role] = Field(min_length=1)
    options: list[AmbiguityOption] = Field(min_length=2)
    chosen: int = 0
    reason: str = ""

    @model_validator(mode="after")
    def _chosen(self) -> Ambiguity:
        if not 0 <= self.chosen < len(self.options):
            raise ValueError("chosen must index into options")
        return self


class DisruptionSpec(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    disruption_type: DisruptionType = "other"
    legs: list[Leg] = Field(min_length=1)
    origin: GeoScope = Field(default_factory=GeoScope)
    destination: GeoScope = Field(default_factory=GeoScope)
    # "any": origin-side legs match on origin geography and destination-side legs on destination geography.
    # "lane": both ends must match (e.g. "freight from SP to the Northeast").
    match_mode: Literal["any", "lane"] = "any"
    nationwide: bool = False
    category_terms: list[str] = Field(default_factory=list, max_length=10)
    seller_ids: list[str] = Field(default_factory=list, max_length=50)
    start_at: datetime
    duration_days: float = Field(gt=0, le=60)
    explicit_delay_days: float | None = Field(default=None, ge=0, le=60)
    recovery_days: float = Field(default=0, ge=0, le=30)
    all_modes: bool = False
    confidence: float = Field(default=0.5, ge=0, le=1)
    ambiguities: list[Ambiguity] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    source: Literal["llm", "rules", "user"] = "rules"

    @field_validator("legs")
    @classmethod
    def _legs(cls, v: list[Leg]) -> list[Leg]:
        return [leg for leg in LEGS if leg in v]

    @field_validator("category_terms")
    @classmethod
    def _terms(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for t in v:
            t = t.strip()
            if t and t.lower() not in {x.lower() for x in out}:
                out.append(t[:60])
        return out

    @model_validator(mode="after")
    def _scope(self) -> DisruptionSpec:
        if not (
            self.nationwide
            or not self.origin.is_empty()
            or not self.destination.is_empty()
            or self.seller_ids
            or self.category_terms
        ):
            raise ValueError(
                "The disruption has no scope: add a location (origin/destination), mark it nationwide, "
                "or name affected sellers or product types."
            )
        return self

    @property
    def end_at(self) -> datetime:
        return self.start_at + timedelta(days=self.duration_days)

    def has_geo(self) -> bool:
        return self.nationwide or not self.origin.is_empty() or not self.destination.is_empty()


class RunOptions(BaseModel):
    locale: Literal["en", "pt-BR"] = "en"
    max_llm_emails: int | None = Field(default=None, ge=0, le=500)
    date_rebase: bool = False
    use_llm: bool = True


# --------------------------------------------------------------------------- reference data


@dataclass(slots=True)
class LaneStat:
    origin: str
    dest: str
    n: int
    mean: float
    p50: float
    p90: float
    quantiles: list[float]  # 21 values at p = 0.00, 0.05, ..., 1.00
    level: Literal["lane", "destination", "national"]

    @property
    def key(self) -> str:
        return f"{self.origin}->{self.dest}"


@dataclass(slots=True)
class SellerStat:
    seller_id: str
    orders_n: int
    late_handover_rate: float | None
    late_delivery_rate: float | None
    median_handling_days: float | None


@dataclass(slots=True)
class AltSeller:
    product_id: str
    seller_id: str
    seller_state: str
    seller_city: str
    last_sale_ts: datetime
    avg_price: float
    sales_n: int


@dataclass(slots=True)
class LineRecord:
    """One order item (unit) of an actionable order, denormalized for the deterministic engine."""

    order_id: str
    order_item_id: int
    product_id: str
    seller_id: str
    customer_id: str
    customer_unique_id: str
    stage: str
    purchase_ts: datetime
    approved_ts: datetime | None
    carrier_ts: datetime | None
    sla_ts: datetime
    shipping_limit_ts: datetime | None
    price: float
    freight: float
    seller_city: str
    seller_state: str
    seller_lat: float | None
    seller_lng: float | None
    cust_city: str
    cust_state: str
    cust_lat: float | None
    cust_lng: float | None
    category_pt: str | None
    category_en: str
    product_class: str
    class_confidence: float
    handling_profile: str
    billable_weight_kg: float
    weight_kg: float
    length_cm: float
    height_cm: float
    width_cm: float
    imputed: bool = False
    extra: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- concept resolution


class ConceptMatch(BaseModel):
    kind: Literal["category", "class", "profile"]
    value: str
    label: str
    score: float
    via: Literal["lexical", "keyword", "semantic"]


class ConceptResolution(BaseModel):
    term: str
    matches: list[ConceptMatch] = Field(default_factory=list)


# --------------------------------------------------------------------------- impact & decisions


class LegHold(BaseModel):
    leg: Leg
    hold_days: float
    note: str


class LineImpact(BaseModel):
    order_item_id: int
    product_id: str
    seller_id: str
    seller_city: str
    seller_state: str
    category_en: str
    category_pt: str | None
    product_class: str
    class_confidence: float
    handling_profile: str
    billable_weight_kg: float
    price: float
    freight: float
    match_reasons: list[str]
    affected: bool
    handover_expected: datetime
    baseline_eta: datetime
    projected_eta: datetime
    projected_eta_p90: datetime
    lane: str
    lane_level: str
    lane_p50: float
    lane_p90: float
    legs: list[LegHold] = Field(default_factory=list)
    hold_days: float = 0.0
    p_breach: float
    p_breach_baseline: float


class SeverityComponent(BaseModel):
    name: str
    value: float
    weight: float
    points: float
    explanation: str


class OrderImpact(BaseModel):
    order_id: str
    customer_id: str
    customer_unique_id: str
    stage: str
    purchase_ts: datetime
    approved_ts: datetime | None
    carrier_ts: datetime | None
    sla_ts: datetime
    cust_city: str
    cust_state: str
    value_brl: float
    freight_brl: float
    billable_weight_kg: float
    handling_profile: str
    product_classes: list[str]
    categories: list[str]
    lines: list[LineImpact]
    affected: bool
    hold_days: float
    baseline_eta: datetime
    projected_eta: datetime
    projected_eta_p90: datetime
    days_late: float
    days_late_calendar: int
    baseline_days_late: float
    p_breach: float
    p_breach_baseline: float
    severity_score: float = 0.0
    severity_tier: Tier = "low"
    severity_components: list[SeverityComponent] = Field(default_factory=list)


class Compensation(BaseModel):
    tier_label: str = ""
    freight_refund_brl: float = 0.0
    voucher_pct: float = 0.0
    voucher_brl: float = 0.0
    refund_total_brl: float = 0.0
    human_callback: bool = False


class AltOption(BaseModel):
    kind: Literal["alt_seller", "alt_route"]
    order_item_id: int
    seller_id: str | None = None
    seller_state: str | None = None
    seller_city: str | None = None
    eta: datetime | None = None
    gain_days: float = 0.0
    price_delta_pct: float | None = None
    extra_cost_brl: float = 0.0
    accepted: bool = False
    reason: str


class LineDecision(BaseModel):
    order_item_id: int
    action: Action
    rule_id: str
    reason: str


class MitigationDecision(BaseModel):
    order_id: str
    action: Action
    rule_id: str
    reason_codes: list[str]
    explanation: str
    line_decisions: list[LineDecision] = Field(default_factory=list)
    split_shipment: bool = False
    offer_cancellation: bool = False
    alternatives: list[AltOption] = Field(default_factory=list)
    resolution_eta: datetime
    resolution_eta_p90: datetime
    resolution_days_late: int
    compensation: Compensation = Field(default_factory=Compensation)
    est_cost_brl: float = 0.0
    needs_review: bool = False
    review_reasons: list[str] = Field(default_factory=list)
    allowed_actions: list[Action] = Field(default_factory=list)
    decided_by: Literal["rules", "llm_adjudicator", "llm_investigator"] = "rules"
    llm_rationale: str | None = None
    policy_refs: list[str] = Field(default_factory=list)
    agent_trace: list[dict] = Field(default_factory=list)  # tool-using investigation steps (see agents/investigator.py)
