"""Run events: appended to Postgres (the SSE endpoint tails them) and mirrored to structured logs."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.db.models import RunEvent

log = get_logger(__name__)


class EventEmitter:
    def __init__(self, run_id: str, session_factory: async_sessionmaker[AsyncSession] | None):
        self.run_id = run_id
        self.session_factory = session_factory
        self.seq = 0
        self.events: list[dict] = []  # kept in memory too (CLI mode has no database writes for events)

    async def init(self) -> None:
        if self.session_factory is None:
            return
        async with self.session_factory() as s:
            current = (await s.execute(select(func.max(RunEvent.seq)).where(RunEvent.run_id == self.run_id))).scalar()
            self.seq = int(current or 0)

    async def emit(self, stage: str, type_: str, message: str, payload: dict | None = None, level: str = "info") -> None:
        self.seq += 1
        event = {"seq": self.seq, "stage": stage, "type": type_, "level": level, "message": message, "payload": payload or {}}
        self.events.append(event)
        log.info("run_event", run_id=self.run_id, **{k: v for k, v in event.items() if k != "payload"})
        if self.session_factory is None:
            return
        async with self.session_factory() as s:
            s.add(
                RunEvent(
                    run_id=self.run_id, seq=self.seq, stage=stage, type=type_, level=level, message=message,
                    payload=payload or {},
                )
            )
            await s.commit()
