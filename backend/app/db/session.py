from collections.abc import AsyncIterator
from functools import lru_cache
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings


@lru_cache
def get_async_engine() -> AsyncEngine:
    return create_async_engine(get_settings().database_url, pool_pre_ping=True, pool_size=10, max_overflow=10)


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_async_engine(), expire_on_commit=False)


@lru_cache
def get_sync_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency."""
    async with get_sessionmaker()() as session:
        yield session


MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
BASELINE_REVISION = "0001"
# Serialises migrations when several processes (seed, API replicas, worker) start at once.
MIGRATION_LOCK_ID = 7_242_001
# v1.x databases were created with create_all and this one additive change before Alembic was introduced.
LEGACY_FIXUPS = ["ALTER TABLE runs ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ"]


def _alembic_config(conn) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.attributes["connection"] = conn
    return cfg


def init_db(engine: Engine | None = None) -> None:
    """Bring the schema to the latest Alembic revision (idempotent and safe to call from every process).

    A database created by v1.x (tables present, no alembic_version) is stamped at the baseline first."""
    engine = engine or get_sync_engine()
    with engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": MIGRATION_LOCK_ID})
        tables = set(inspect(conn).get_table_names())
        cfg = _alembic_config(conn)
        if "runs" in tables and "alembic_version" not in tables:
            for stmt in LEGACY_FIXUPS:
                conn.execute(text(stmt))
            command.stamp(cfg, BASELINE_REVISION)
        command.upgrade(cfg, "head")
