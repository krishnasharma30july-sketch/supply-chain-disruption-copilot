"""Request/response models of the public API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.models import DisruptionSpec, RunOptions
from app.services.analysis import PreviewCounts


class ParseRequest(BaseModel):
    query: str = Field(min_length=5, max_length=2000)
    use_llm: bool = True


class OptionPreview(BaseModel):
    ambiguity_index: int
    option_index: int
    label: str
    preview: PreviewCounts


class ParseResponse(BaseModel):
    spec: DisruptionSpec
    source: str
    model: str | None
    warnings: list[str]
    preview: PreviewCounts
    option_previews: list[OptionPreview]


class PreviewRequest(BaseModel):
    spec: DisruptionSpec


class PreviewResponse(BaseModel):
    spec: DisruptionSpec
    preview: PreviewCounts


class RunCreate(BaseModel):
    query: str | None = Field(default=None, max_length=2000)
    spec: DisruptionSpec | None = None
    options: RunOptions = Field(default_factory=RunOptions)

    @model_validator(mode="after")
    def _one(self) -> RunCreate:
        if not self.spec and not (self.query and self.query.strip()):
            raise ValueError("Provide a scenario description (query) or an interpreted spec.")
        return self


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    title: str
    raw_query: str | None
    status: str
    spec: dict | None
    spec_source: str | None
    options: dict
    llm_mode: str
    metrics: dict
    error: dict | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class RunDetail(RunOut):
    config_snapshot: dict


class RunList(BaseModel):
    total: int
    items: list[RunOut]


class OrderRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    order_id: str
    customer_unique_id: str
    stage: str
    origin_states: list[str]
    dest_state: str
    categories: list[str]
    product_classes: list[str]
    handling_profile: str
    value_brl: float
    billable_weight_kg: float
    affected: bool
    hold_days: float
    days_late: float
    resolution_days_late: int
    p_breach: float
    severity_score: float
    severity_tier: str
    action: str
    offer_cancellation: bool
    needs_review: bool
    decided_by: str
    est_cost_brl: float
    sla_ts: datetime
    projected_eta: datetime
    resolution_eta: datetime


class OrderPage(BaseModel):
    total: int
    items: list[OrderRow]


class CommunicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    run_id: str
    customer_unique_id: str
    order_ids: list[str]
    first_name: str
    locale: str
    subject: str
    body: str
    generator: str
    model: str | None
    prompt_version: str | None
    status: str
    qa: dict
    max_severity: float
    version: int
    attempts: int
    created_at: datetime
    updated_at: datetime
    email_to: str | None = None
    approved_by: str | None = None
    sent_at: datetime | None = None
    message_id: str | None = None
    send_error: str | None = None


class CommunicationPage(BaseModel):
    total: int
    items: list[CommunicationOut]


class CommunicationEdit(BaseModel):
    subject: str = Field(min_length=3, max_length=200)
    body: str = Field(min_length=20, max_length=8000)


class IdsRequest(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=1000)


class BulkResult(BaseModel):
    updated: int
    skipped: int
    detail: list[str] = []


class OrderDetail(BaseModel):
    row: OrderRow
    impact: dict
    decision: dict
    trace: list[dict]
    policy_citations: list[dict]
    communication: CommunicationOut | None


class SummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    run_id: str
    stats: dict
    retrieval: dict
    headline: str
    narrative: str
    recommendations: list
    number_check: dict
    generator: str


class SearchHitOut(BaseModel):
    doc_id: str
    doc_type: str
    ref_id: str
    title: str
    snippet: str
    metadata: dict
    score: float
    dense_rank: int | None
    sparse_rank: int | None
    rerank_score: float | None = None


class SearchResponse(BaseModel):
    query: str
    mode: Literal["hybrid", "dense", "sparse"]
    effective_mode: str
    hits: list[SearchHitOut]


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    seq: int
    stage: str
    type: str
    level: str
    message: str
    payload: dict[str, Any]
    ts: datetime
