"""Architecture guards."""

from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
ALLOWED_ACTUALS_READERS = {"db/models.py", "etl/build.py", "evals/backtest.py"}


def test_runtime_code_never_reads_unmasked_actuals():
    offenders = []
    for path in APP.rglob("*.py"):
        rel = path.relative_to(APP).as_posix()
        if rel in ALLOWED_ACTUALS_READERS:
            continue
        text = path.read_text(encoding="utf-8")
        if "OrderActual" in text or "order_actuals" in text:
            offenders.append(rel)
    assert offenders == [], f"Only ETL/backtest may touch unmasked actuals: {offenders}"


def test_prompts_exist_for_every_versioned_prompt():
    from app.llm.service import PROMPT_VERSIONS

    prompts = APP / "llm" / "prompts"
    for name in PROMPT_VERSIONS:
        assert (prompts / f"{name}.system.j2").exists()
        assert (prompts / f"{name}.user.j2").exists()
