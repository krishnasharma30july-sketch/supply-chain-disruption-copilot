"""arq worker: executes runs and bulk email drafting outside the API process."""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

from arq import cron
from arq.connections import RedisSettings
from prometheus_client import start_http_server
from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.models import Communication, Run
from app.db.session import get_sessionmaker, init_db
from app.domain.models import RunOptions
from app.llm.service import LLMCallError, LLMService, LLMUnavailable
from app.orchestrator.liveness import queued_run_ids, reap_stale_runs
from app.orchestrator.pipeline import execute_run_safely
from app.services.comm_ops import regenerate_with_llm
from app.services.email_upgrade import upgrade_chunk

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
    status = await execute_run_safely(run_id, ctx["llm"], ctx.get("embedder"))
    if status == "succeeded":
        try:
            await _schedule_upgrade(ctx, run_id)
        except Exception:  # extra AI coverage must never turn a finished run into a failure
            log.exception("email_upgrade_schedule_failed", run_id=run_id)
    return status


async def _schedule_upgrade(ctx: dict, run_id: str) -> bool:
    """After a successful run, queue the background job that writes the remaining emails with the LLM."""
    settings, llm = get_settings(), ctx["llm"]
    async with get_sessionmaker()() as s:
        run = await s.get(Run, run_id)
        if run is None:
            return False
        opts = RunOptions.model_validate(run.options or {})
        wanted = opts.ai_all_emails if opts.ai_all_emails is not None else settings.ai_emails_for_everyone
        explicitly_off = opts.max_llm_emails == 0  # the analyst asked for no AI-written emails
        if not (wanted and opts.use_llm and llm.enabled and not explicitly_off and settings.email_upgrade_max_calls > 0):
            return False
        run.metrics = {**(run.metrics or {}), "ai_upgrade": {"state": "queued", "upgraded": 0, "failed": 0, "calls": 0}}
        await s.commit()
    await ctx["redis"].enqueue_job(
        "upgrade_emails_job", run_id, False, _job_id=f"upgrade:{run_id}:{uuid.uuid4().hex[:8]}", _defer_by=timedelta(seconds=3)
    )
    return True


async def upgrade_emails_job(ctx: dict, run_id: str, reset: bool = False) -> dict:
    """One slice of the background AI upgrade. It re-queues itself while work remains, so other jobs interleave."""
    progress = await upgrade_chunk(get_sessionmaker(), ctx["llm"], get_settings(), run_id, reset=reset)
    if progress.get("state") == "running":
        await ctx["redis"].enqueue_job("upgrade_emails_job", run_id, False, _job_id=f"upgrade:{run_id}:{uuid.uuid4().hex[:8]}")
    return progress


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
    functions = [run_pipeline_job, draft_emails_job, upgrade_emails_job]
    cron_jobs = [cron(reap_stale_runs_job, second=0, run_at_startup=False)]  # every minute
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    job_timeout = 3600
    # One job at a time keeps the shared LLM rate limiter predictable. The periodic reaper runs whenever this worker is
    # idle; orphans left by a dead worker are also reaped on startup.
    max_jobs = 1
    keep_result = 3600
    health_check_interval = 30  # `arq --check` (the compose healthcheck) reads this heartbeat from Redis
