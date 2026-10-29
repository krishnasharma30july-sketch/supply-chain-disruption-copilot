"""arq worker: executes runs and bulk email drafting outside the API process."""

from __future__ import annotations

import asyncio
import uuid

from arq import cron
from arq.connections import RedisSettings
from prometheus_client import start_http_server
from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.models import Communication
from app.db.session import get_sessionmaker, init_db
from app.llm.service import LLMCallError, LLMService, LLMUnavailable
from app.orchestrator.liveness import queued_run_ids, reap_stale_runs
from app.orchestrator.pipeline import execute_run_safely
from app.services.comm_ops import regenerate_with_llm

log = get_logger(__name__)


async def startup(ctx: dict) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    await asyncio.to_thread(init_db)  # idempotent; applies pending Alembic migrations
    if settings.metrics_enabled and settings.worker_metrics_port:
        start_http_server(settings.worker_metrics_port)
    ctx["llm"] = LLMService(settings, get_sessionmaker())
    try:
        from app.services.retrieval.embedder import get_embedder

        ctx["embedder"] = await asyncio.to_thread(get_embedder)
    except Exception:
        log.exception("embedder_unavailable_in_worker")
        ctx["embedder"] = None
    # Recover from a previous crash/restart: fail orphaned runs, re-enqueue runs whose job was lost.
    reaped = await reap_stale_runs()
    queued = await queued_run_ids()
    for run_id in queued:
        await ctx["redis"].enqueue_job("run_pipeline_job", run_id, _job_id=f"run:{run_id}:recover:{uuid.uuid4().hex[:6]}")
    log.info("worker_started", llm_mode=ctx["llm"].mode, embedder=ctx["embedder"] is not None,
             reaped_runs=len(reaped), requeued_runs=len(queued))


async def run_pipeline_job(ctx: dict, run_id: str) -> str:
    return await execute_run_safely(run_id, ctx["llm"], ctx.get("embedder"))


async def reap_stale_runs_job(ctx: dict) -> int:
    return len(await reap_stale_runs())


async def draft_emails_job(ctx: dict, run_id: str, comm_ids: list[str]) -> dict:
    llm: LLMService = ctx["llm"]
    done, failed = 0, 0
    async with get_sessionmaker()() as s:
        comms = (
            await s.execute(select(Communication).where(Communication.run_id == run_id, Communication.id.in_(comm_ids)))
        ).scalars().all()
        for c in comms:
            if c.status in ("approved", "sent"):
                continue
            try:
                _, failures = await regenerate_with_llm(s, c, llm, get_settings().kb_dir, "worker")
                done += 0 if failures else 1
                failed += 1 if failures else 0
            except (LLMUnavailable, LLMCallError) as e:
                log.warning("bulk_draft_stopped", error=str(e))
                failed += 1
                if isinstance(e, LLMUnavailable):
                    break
    return {"drafted": done, "failed": failed}


class WorkerSettings:
    functions = [run_pipeline_job, draft_emails_job]
    cron_jobs = [cron(reap_stale_runs_job, second=0, run_at_startup=False)]  # every minute
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    job_timeout = 3600
    # One job at a time keeps the shared LLM rate limiter predictable. The periodic reaper runs whenever this worker is
    # idle; orphans left by a dead worker are also reaped on startup.
    max_jobs = 1
    keep_result = 3600
    health_check_interval = 30  # `arq --check` (the compose healthcheck) reads this heartbeat from Redis
