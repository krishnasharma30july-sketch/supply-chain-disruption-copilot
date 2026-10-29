"""Operational behaviour: LLM breaker recovery, run liveness, cache bypass, schema migrations."""

import inspect
from datetime import datetime, timedelta, timezone

from app.core.config import get_settings
from app.db.models import Run
from app.llm.service import LLMService
from app.orchestrator.liveness import STALE_AFTER_S, is_stale


def online_llm(**overrides) -> LLMService:
    settings = get_settings().model_copy(update={"llm_provider": "gemini", "gemini_api_key": "test-key", **overrides})
    return LLMService(settings, session_factory=None)


def test_breaker_closes_again_after_its_cooldown():
    llm = online_llm()
    llm._trip("test failure", cooldown_s=60)
    assert not llm.available
    llm._trip("test failure", cooldown_s=0)
    assert llm.available  # half-open: the next call is a trial instead of staying offline until restart
    assert llm.breaker_reason is None


def test_quota_breaker_reopens_when_the_first_model_quota_resets():
    import time

    llm = online_llm()
    llm._exhausted = {"m1": time.monotonic() + 0.2, "m2": time.monotonic() + 3600}
    llm._trip_quota()  # every model exhausted: the cooldown is the earliest reset (m1, ~0.2 s), not a fixed value
    assert not llm.available
    time.sleep(0.3)
    assert llm.available  # m1's quota has reset, so the breaker lets the next call through


def test_regenerate_bypasses_the_response_cache():
    from app.services import comm_ops

    assert "use_cache=False" in inspect.getsource(comm_ops.regenerate_with_llm)


def test_run_liveness_detection():
    now = datetime.now(timezone.utc)
    fresh = Run(id="a", status="running", started_at=now, heartbeat_at=now)
    stale = Run(id="b", status="running", started_at=now - timedelta(hours=1),
                heartbeat_at=now - timedelta(seconds=STALE_AFTER_S + 5))
    never = Run(id="c", status="running", started_at=None, heartbeat_at=None)
    assert not is_stale(fresh, now)
    assert is_stale(stale, now)
    assert is_stale(never, now)


def test_alembic_revisions_form_one_linear_chain():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from app.db.session import BASELINE_REVISION, LEGACY_FIXUPS, MIGRATIONS_DIR

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    script = ScriptDirectory.from_config(cfg)
    assert len(script.get_heads()) == 1  # no branches
    chain = [r.revision for r in script.walk_revisions()]
    assert chain[-1] == BASELINE_REVISION
    assert all("IF NOT EXISTS" in m for m in LEGACY_FIXUPS)  # stamping a v1.x database must be idempotent
