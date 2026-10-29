"""Supervisor: runs the agent DAG for one run, with persisted events, cancellation and failure capture."""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.analyst import LogisticsAnalystAgent
from app.agents.base import AgentContext, RunState
from app.agents.customer_success import CustomerSuccessAgent
from app.agents.intake import IntakeAgent
from app.agents.mitigation import MitigationAgent
from app.agents.qa_critic import QACriticAgent
from app.agents.reporter import ReporterAgent
from app.agents.scout import ScoutAgent
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.metrics import RUN_DURATION, RUNS_FINISHED
from app.core.policy_config import get_assumptions, get_category_classes, get_policy, get_scoring
from app.db.models import Communication, LlmCall, OrderOutcome, Run, RunSummary
from app.db.session import get_sessionmaker
from app.domain.models import DisruptionSpec, RunOptions
from app.llm.service import PROMPT_VERSIONS, LLMService
from app.orchestrator.events import EventEmitter
from app.orchestrator.liveness import heartbeat_loop, is_stale
from app.services.reference import get_reference
from app.services.retrieval.reranker import get_reranker

log = get_logger(__name__)

STAGES = [
    ("intake", "Intake"),
    ("scout", "Scout"),
    ("analyst", "Logistics Analyst"),
    ("mitigation", "Mitigation"),
    ("customer_success", "Customer Success"),
    ("qa_critic", "QA Critic"),
    ("reporter", "Reporter"),
]


def config_snapshot(llm: LLMService) -> dict:
    s = get_settings()
    a = get_assumptions()
    return {
        "policy": get_policy().model_dump(),
        "scoring": get_scoring().model_dump(),
        "assumptions": {"version": a.version, "params": a.params.model_dump()},
        "category_classes_version": get_category_classes().version,
        "prompt_versions": PROMPT_VERSIONS,
        "llm": {
            "provider": s.llm_provider if llm.enabled else "offline",
            "models": {r: s.model_for(r) for r in ("intake", "email", "adjudicator", "summary")},
            "max_llm_emails": s.max_llm_emails,
        },
        "embed_model": s.embed_model,
    }


async def create_run(
    session: AsyncSession,
    *,
    raw_query: str | None,
    spec: DisruptionSpec | None,
    options: RunOptions,
    llm: LLMService,
) -> Run:
    title = spec.title if spec else (raw_query or "Disruption scenario").strip()[:80]
    run = Run(
        id=str(uuid.uuid4()),
        title=title,
        raw_query=raw_query,
        spec=spec.model_dump(mode="json") if spec else None,
        spec_source=spec.source if spec else None,
        status="queued",
        options=options.model_dump(),
        config_snapshot=config_snapshot(llm),
        llm_mode="online" if (llm.enabled and options.use_llm) else "offline",
        metrics={},
    )
    session.add(run)
    await session.commit()
    return run


async def _cancel_requested(run_id: str) -> bool:
    async with get_sessionmaker()() as s:
        return bool((await s.execute(select(Run.cancel_requested).where(Run.id == run_id))).scalar())


async def _llm_usage(run_id: str) -> dict:
    async with get_sessionmaker()() as s:
        row = (
            await s.execute(
                select(
                    func.count(LlmCall.id),
                    func.count(LlmCall.id).filter(LlmCall.cached.is_(True)),
                    func.count(LlmCall.id).filter(LlmCall.status != "ok"),
                    func.coalesce(func.sum(LlmCall.input_tokens), 0),
                    func.coalesce(func.sum(LlmCall.output_tokens), 0),
                    func.coalesce(func.sum(LlmCall.latency_ms), 0),
                ).where(LlmCall.run_id == run_id)
            )
        ).one()
    return {"calls": row[0], "cached": row[1], "errors": row[2], "input_tokens": int(row[3]),
            "output_tokens": int(row[4]), "latency_ms_total": int(row[5])}


async def execute_run(run_id: str, llm: LLMService, embedder=None) -> str:
    """Execute (or re-execute) a run. Returns the final status."""
    settings = get_settings()
    sf = get_sessionmaker()
    async with sf() as s:
        run = await s.get(Run, run_id)
        if run is None:
            log.error("run_not_found", run_id=run_id)
            return "missing"
        if run.status in ("succeeded", "canceled"):
            log.info("run_already_finished", run_id=run_id, status=run.status)
            return run.status
        if run.status == "running" and not is_stale(run):
            log.info("run_owned_by_live_worker", run_id=run_id)
            return "running"
        for model in (OrderOutcome, Communication, RunSummary):
            await s.execute(delete(model).where(model.run_id == run_id))
        run.status = "running"
        run.started_at = datetime.now(timezone.utc)
        run.heartbeat_at = run.started_at
        run.finished_at = None
        run.error = None
        raw_query = run.raw_query
        spec = DisruptionSpec.model_validate(run.spec) if run.spec else None
        options = RunOptions.model_validate(run.options or {})
        await s.commit()

    emitter = EventEmitter(run_id, sf)
    await emitter.init()
    llm.reset_breaker()
    state = RunState(raw_query=raw_query, spec=spec, options=options)
    t_run = time.monotonic()
    stage_ms: dict[str, int] = {}
    stop_heartbeat = asyncio.Event()
    heartbeat = asyncio.create_task(heartbeat_loop(run_id, stop_heartbeat))
    try:
        return await _run_stages(run_id, llm, embedder, settings, sf, emitter, state, stage_ms, t_run)
    finally:
        stop_heartbeat.set()
        await heartbeat


async def _run_stages(run_id, llm, embedder, settings, sf, emitter, state, stage_ms, t_run) -> str:
    current_stage = "setup"
    options = state.options
    use_llm = options.use_llm and llm.enabled
    try:
        # Emitted first: the UI treats the latest run_started as the beginning of the current attempt.
        await emitter.emit(
            "run", "run_started",
            f"Run started (LLM {'on: ' + settings.model_for('email') if use_llm else 'off: deterministic mode'}).",
            {"llm": use_llm},
        )
        async with sf() as s:
            ref = await get_reference(s, embedder.embed if embedder is not None else None)
        reranker = None
        if settings.rerank_enabled and embedder is not None:
            try:
                reranker = await asyncio.to_thread(get_reranker)  # process-cached after the first load
            except Exception:
                log.exception("reranker_unavailable")
        ctx = AgentContext(
            run_id=run_id, settings=settings, session_factory=sf, llm=llm, ref=ref, emitter=emitter,
            scoring=get_scoring(), policy=get_policy(), use_llm=use_llm, embedder=embedder, reranker=reranker,
        )
        await emitter.emit("run", "info", f"Snapshot as of {ref.as_of:%Y-%m-%d}.", {"as_of": ref.as_of.isoformat()})
        writer = CustomerSuccessAgent()
        agents = [IntakeAgent(), ScoutAgent(), LogisticsAnalystAgent(), MitigationAgent(), writer,
                  QACriticAgent(writer), ReporterAgent()]
        for agent in agents:
            if await _cancel_requested(run_id):
                await emitter.emit("run", "run_canceled", "Run canceled by the user.", level="warning")
                await _finish(run_id, "canceled", state, stage_ms, t_run, llm)
                return "canceled"
            current_stage = agent.name
            await emitter.emit(agent.name, "stage_started", agent.label)
            t0 = time.monotonic()
            await agent.run(state, ctx)
            stage_ms[agent.name] = int((time.monotonic() - t0) * 1000)
            await emitter.emit(agent.name, "stage_completed", f"{agent.label}: done", {"duration_ms": stage_ms[agent.name]})
            if agent.name == "intake" and state.spec is not None:
                async with sf() as s:
                    run = await s.get(Run, run_id)
                    assert run is not None
                    run.spec = state.spec.model_dump(mode="json")
                    run.spec_source = state.spec_source
                    run.title = state.spec.title
                    await s.commit()
        await _finish(run_id, "succeeded", state, stage_ms, t_run, llm)
        await emitter.emit("run", "run_completed", "Run completed.", {"duration_ms": int((time.monotonic() - t_run) * 1000)})
        return "succeeded"
    except Exception as e:
        log.exception("run_failed", run_id=run_id, stage=current_stage)
        await emitter.emit(current_stage, "run_failed", f"Run failed during {current_stage}: {e}", level="error")
        await _finish(run_id, "failed", state, stage_ms, t_run, llm, error={"stage": current_stage, "message": str(e)[:2000]})
        return "failed"


async def _finish(run_id: str, status: str, state: RunState, stage_ms: dict, t_run: float, llm: LLMService, error: dict | None = None) -> None:
    usage = await _llm_usage(run_id)
    delayed = sum(1 for i in state.impacts if i.affected)
    metrics = {
        "orders_in_scope": len(state.impacts),
        "orders_delayed": delayed,
        "orders_breaching": sum(1 for i in state.impacts if i.affected and i.days_late_calendar > 0),
        "customers_contacted": len(state.drafts),
        "stage_ms": stage_ms,
        "duration_ms": int((time.monotonic() - t_run) * 1000),
        "llm_usage": usage,
        "llm_degraded": llm.breaker_reason,
        "communications": state.metrics.get("communications", {}),
    }
    async with get_sessionmaker()() as s:
        run = await s.get(Run, run_id)
        if run is None:
            return
        run.status = status
        run.finished_at = datetime.now(timezone.utc)
        run.metrics = metrics
        run.error = error
        if llm.breaker_reason and run.llm_mode == "online":
            run.llm_mode = "degraded"
        await s.commit()
    RUNS_FINISHED.labels(status).inc()
    RUN_DURATION.labels(status).observe(metrics["duration_ms"] / 1000)


async def execute_run_safely(run_id: str, llm: LLMService, embedder=None) -> str:
    """Never raises (worker entry point)."""
    try:
        return await execute_run(run_id, llm, embedder)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("run_crashed", run_id=run_id)
        return "failed"
