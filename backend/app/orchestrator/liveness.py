"""Run liveness: heartbeats while a worker executes a run, and a reaper for runs whose worker died."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from app.core.logging import get_logger
from app.db.models import Run
from app.db.session import get_sessionmaker
from app.orchestrator.events import EventEmitter

log = get_logger(__name__)

HEARTBEAT_EVERY_S = 20.0
STALE_AFTER_S = 90.0


async def beat(run_id: str) -> None:
    async with get_sessionmaker()() as s:
        await s.execute(update(Run).where(Run.id == run_id).values(heartbeat_at=datetime.now(timezone.utc)))
        await s.commit()


async def heartbeat_loop(run_id: str, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await beat(run_id)
        except Exception:  # a missed beat must never break the run
            log.exception("heartbeat_failed", run_id=run_id)
        try:
            await asyncio.wait_for(stop.wait(), timeout=HEARTBEAT_EVERY_S)
        except asyncio.TimeoutError:
            pass


def is_stale(run: Run, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    last = run.heartbeat_at or run.started_at
    return last is None or (now - last) > timedelta(seconds=STALE_AFTER_S)


async def reap_stale_runs() -> list[str]:
    """Mark 'running' runs whose heartbeat stopped as failed (their worker crashed or was restarted)."""
    sf = get_sessionmaker()
    reaped: list[str] = []
    async with sf() as s:
        runs = (await s.execute(select(Run).where(Run.status == "running"))).scalars().all()
        now = datetime.now(timezone.utc)
        for run in runs:
            if not is_stale(run, now):
                continue
            canceled = run.cancel_requested
            run.status = "canceled" if canceled else "failed"
            run.finished_at = now
            if not canceled:
                run.error = {
                    "stage": "worker",
                    "message": "The worker stopped while this run was in progress (restart or crash). Retry the run.",
                }
            reaped.append(run.id)
        await s.commit()
    for run_id in reaped:
        emitter = EventEmitter(run_id, sf)
        await emitter.init()
        await emitter.emit("run", "run_failed", "The worker stopped during this run; it was marked as failed and can be retried.",
                           level="error")
        log.warning("run_reaped", run_id=run_id)
    return reaped


async def queued_run_ids() -> list[str]:
    async with get_sessionmaker()() as s:
        return list((await s.execute(select(Run.id).where(Run.status == "queued").order_by(Run.created_at))).scalars())
