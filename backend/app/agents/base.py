"""Agent contract and the shared run state passed between agents."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.policy_config import PolicyConfig, ScoringConfig
from app.domain.models import DisruptionSpec, MitigationDecision, OrderImpact, RunOptions
from app.llm.service import LLMService
from app.orchestrator.events import EventEmitter
from app.services.analysis import ScopeResult
from app.services.comms import CustomerFacts, QAAllowances
from app.services.reference import ReferenceData


@dataclass
class EmailDraft:
    customer_unique_id: str
    first_name: str
    order_ids: list[str]
    max_severity: float
    facts: CustomerFacts
    allow: QAAllowances
    subject: str = ""
    body: str = ""  # placeholder form ([[TRACK_LINK]] ...)
    generator: str = "template"  # llm | template
    model: str | None = None
    prompt_version: str | None = None
    attempts: int = 0
    qa: dict = field(default_factory=dict)
    status: str = "draft"
    llm_feedback: list[str] = field(default_factory=list)


@dataclass
class RunState:
    raw_query: str | None
    spec: DisruptionSpec | None
    options: RunOptions
    spec_source: str | None = None
    intake_warnings: list[str] = field(default_factory=list)
    scope: ScopeResult | None = None
    retrieval: dict[str, Any] = field(default_factory=dict)
    impacts: list[OrderImpact] = field(default_factory=list)
    decisions: list[MitigationDecision] = field(default_factory=list)
    drafts: list[EmailDraft] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentContext:
    run_id: str
    settings: Settings
    session_factory: async_sessionmaker[AsyncSession] | None
    llm: LLMService
    ref: ReferenceData
    emitter: EventEmitter
    scoring: ScoringConfig
    policy: PolicyConfig
    use_llm: bool
    embedder: Any = None  # app.services.retrieval.embedder.Embedder | None
    reranker: Any = None  # app.services.retrieval.reranker.Reranker | None


class Agent(Protocol):
    name: str
    label: str

    async def run(self, state: RunState, ctx: AgentContext) -> None: ...
