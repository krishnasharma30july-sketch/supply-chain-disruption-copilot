"""Command-line interface: ETL, inline runs (no worker needed), sample generation, evaluations, backtest.

Usage (inside the backend container):  python -m app.cli --help
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import configure_logging

cli = typer.Typer(add_completion=False, no_args_is_help=True, help="Supply Chain Disruption Copilot CLI")

SAMPLE_SCENARIOS = [
    ("scenario_1_sao_paulo_rains", "Heavy rains are delaying all freight leaving Sao Paulo for the next 5 days"),
    ("scenario_2_rio_last_mile_strike", "Last-mile courier strike in Rio de Janeiro for 3 days"),
]


@cli.command()
def etl(
    force: bool = typer.Option(False, help="Rebuild even if the inputs did not change."),
    as_of: Optional[str] = typer.Option(None, help="Override AS_OF, e.g. 2018-08-01T00:00:00"),
) -> None:
    """Download (if needed), validate and load the Olist snapshot and retrieval index."""
    configure_logging(get_settings().log_level)
    from app.etl.build import run_etl

    result = run_etl(force=force, as_of=datetime.fromisoformat(as_of) if as_of else None)
    typer.echo(json.dumps({k: v for k, v in result.items() if k != "files"}, indent=1, default=str))


@cli.command(name="create-user")
def create_user_cmd(
    email: str = typer.Option(..., help="Login email."),
    role: str = typer.Option("analyst", help="analyst | approver | admin"),
    name: str = typer.Option("", help="Display name."),
    password: str = typer.Option(..., prompt=True, hide_input=True, confirmation_prompt=True),
) -> None:
    """Create a user account (roles: analyst runs and edits, approver also approves and sends)."""
    from app.db.session import get_sessionmaker, init_db
    from app.services.users import create_user

    init_db()

    async def main() -> None:
        async with get_sessionmaker()() as s:
            user = await create_user(s, email, name or email, role, password)
        typer.echo(f"Created {user.email} ({user.role}).")

    try:
        asyncio.run(main())
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e


async def _run_inline(query: str, *, offline: bool, locale: str, max_emails: int | None) -> str:
    from app.db.session import get_sessionmaker, init_db
    from app.domain.models import RunOptions
    from app.llm.service import LLMService
    from app.orchestrator.pipeline import create_run, execute_run
    from app.services.retrieval.embedder import get_embedder

    settings = get_settings()
    init_db()
    llm = LLMService(settings, get_sessionmaker())
    try:
        embedder = await asyncio.to_thread(get_embedder)
    except Exception as e:  # pragma: no cover
        typer.echo(f"Embedding model unavailable ({e}); retrieval falls back to full-text only.", err=True)
        embedder = None
    options = RunOptions(locale=locale, max_llm_emails=max_emails, use_llm=not offline)  # type: ignore[arg-type]
    async with get_sessionmaker()() as s:
        run = await create_run(s, raw_query=query, spec=None, options=options, llm=llm)
    status = await execute_run(run.id, llm, embedder)
    typer.echo(f"Run {run.id} finished: {status}")
    return run.id


async def _export(run_id: str, out_dir: Path) -> None:
    from app.api.schemas import CommunicationOut, OrderRow, RunDetail, SummaryOut
    from app.core.policy_config import get_assumptions
    from app.db.models import Communication, OrderOutcome, Run, RunSummary
    from app.db.session import get_sessionmaker
    from app.services.export import orders_csv, report_markdown

    out_dir.mkdir(parents=True, exist_ok=True)
    async with get_sessionmaker()() as s:
        run = await s.get(Run, run_id)
        summary = await s.get(RunSummary, run_id)
        orders = (await s.execute(select(OrderOutcome).where(OrderOutcome.run_id == run_id)
                                  .order_by(OrderOutcome.severity_score.desc(), OrderOutcome.order_id))).scalars().all()
        comms = (await s.execute(select(Communication).where(Communication.run_id == run_id)
                                 .order_by(Communication.max_severity.desc()))).scalars().all()
    run_d = RunDetail.model_validate(run).model_dump(mode="json")
    sum_d = SummaryOut.model_validate(summary).model_dump(mode="json") if summary else None
    order_d = [OrderRow.model_validate(o).model_dump() for o in orders]
    comm_d = [CommunicationOut.model_validate(c).model_dump(mode="json") for c in comms]
    llm_first = sorted(comm_d, key=lambda c: (c["generator"] != "llm", -c["max_severity"]))
    (out_dir / "impact_summary.md").write_text(
        report_markdown(run_d, sum_d, order_d, llm_first, [i.model_dump() for i in get_assumptions().items]),
        encoding="utf-8",
    )
    (out_dir / "orders.csv").write_text(orders_csv(order_d), encoding="utf-8")
    for i, c in enumerate(llm_first[:2], start=1):
        qa = c.get("qa") or {}
        checks = "\n".join(f"- [{'x' if ch['passed'] else ' '}] {ch['name']}: {ch['detail']}" for ch in qa.get("checks", []))
        (out_dir / f"email_example_{i}.md").write_text(
            f"# Email to {c['first_name']} (orders {', '.join(o[:8].upper() for o in c['order_ids'])})\n\n"
            f"- Generator: {c['generator']} ({c.get('model') or 'template'})\n- Status: {c['status']}\n\n"
            f"**Subject:** {c['subject']}\n\n```text\n{c['body']}\n```\n\n## QA checks\n\n{checks}\n",
            encoding="utf-8",
        )
    (out_dir / "run.json").write_text(
        json.dumps({"run": run_d, "summary": sum_d}, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    typer.echo(f"Wrote {out_dir}")


@cli.command()
def run(
    query: str = typer.Argument(..., help="Disruption scenario, e.g. 'Heavy rains are delaying all freight leaving Sao Paulo for the next 5 days'"),
    offline: bool = typer.Option(False, help="Do not call the LLM (deterministic parser and templates)."),
    locale: str = typer.Option("en", help="en or pt-BR"),
    max_emails: Optional[int] = typer.Option(None, help="Cap on LLM-drafted emails (default from settings)."),
    out: Optional[Path] = typer.Option(None, help="Write the report, CSV and sample emails to this directory."),
) -> None:
    """Run the full agent pipeline inline and print the summary."""
    configure_logging("WARNING")

    async def main() -> None:
        run_id = await _run_inline(query, offline=offline, locale=locale, max_emails=max_emails)
        target = out or Path("/tmp") / f"run-{run_id[:8]}"
        await _export(run_id, target)
        typer.echo((target / "impact_summary.md").read_text(encoding="utf-8")[:4000])

    asyncio.run(main())


@cli.command()
def samples(
    out: Path = typer.Option(Path("/app/samples"), help="Output directory."),
    offline: bool = typer.Option(False, help="Do not call the LLM."),
    max_emails: int = typer.Option(10, help="LLM-drafted emails per scenario."),
) -> None:
    """Generate the deliverable sample reports and emails for the two reference scenarios."""
    configure_logging("WARNING")

    async def main() -> None:
        for folder, query in SAMPLE_SCENARIOS:
            run_id = await _run_inline(query, offline=offline, locale="en", max_emails=max_emails)
            await _export(run_id, out / folder)

    asyncio.run(main())


@cli.command()
def backtest(out: Path = typer.Option(Path("/app/samples/backtest_truckers_strike_2018"), help="Output directory.")) -> None:
    """Replay real events (2018 truckers' strike, Black Friday 2017, Carnival 2018) against what actually happened."""
    configure_logging("WARNING")
    from app.evals.backtest import run_backtest

    report = run_backtest()
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(report, encoding="utf-8")
    typer.echo(report)


@cli.command(name="eval")
def evaluate(
    llm: bool = typer.Option(False, help="Also evaluate the LLM interpreter (uses API quota)."),
    out: Path = typer.Option(Path("/app/samples/evaluation"), help="Output directory."),
) -> None:
    """Parsing accuracy (40 scenarios) and retrieval quality (dense vs sparse vs hybrid vs reranked)."""
    configure_logging("WARNING")
    from app.evals.suite import run_suite

    report = asyncio.run(run_suite(use_llm=llm))
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(report, encoding="utf-8")
    typer.echo(report)


if __name__ == "__main__":
    cli()
