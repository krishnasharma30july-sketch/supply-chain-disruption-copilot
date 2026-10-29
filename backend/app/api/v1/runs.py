import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import Principal, require_approver
from app.api.deps import get_arq, get_llm, get_ref
from app.api.schemas import (
    BulkResult,
    CommunicationOut,
    CommunicationPage,
    EventOut,
    IdsRequest,
    OrderDetail,
    OrderPage,
    OrderRow,
    RunCreate,
    RunDetail,
    RunList,
    RunOut,
    SummaryOut,
)
from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError
from app.core.policy_config import get_assumptions
from app.db.models import Communication, OrderOutcome, Run, RunEvent, RunSummary
from app.db.session import get_session, get_sessionmaker
from app.domain.models import DisruptionSpec, MitigationDecision, OrderImpact
from app.llm.service import LLMService
from app.orchestrator.liveness import is_stale
from app.orchestrator.pipeline import create_run
from app.services.export import orders_csv, report_markdown
from app.services.reference import ReferenceData
from app.services.retrieval.kb import kb_index
from app.services.spec_builder import apply_ambiguities
from app.services.trace import build_trace

router = APIRouter(prefix="/runs", tags=["runs"])
TERMINAL = {"succeeded", "failed", "canceled"}


async def _get_run(session: AsyncSession, run_id: str) -> Run:
    run = await session.get(Run, run_id)
    if run is None:
        raise NotFoundError(f"Run {run_id} not found.")
    return run


async def _enqueue(arq, run_id: str) -> None:
    await arq.enqueue_job("run_pipeline_job", run_id, _job_id=f"run:{run_id}:{uuid.uuid4().hex[:8]}")


@router.post("", status_code=202, response_model=RunOut)
async def create(
    req: RunCreate,
    session: AsyncSession = Depends(get_session),
    llm: LLMService = Depends(get_llm),
    arq=Depends(get_arq),
    _ref: ReferenceData = Depends(get_ref),  # fail fast (503) when the dataset is not loaded
) -> Run:
    spec = apply_ambiguities(req.spec) if req.spec else None
    run = await create_run(session, raw_query=(req.query or "").strip() or None, spec=spec, options=req.options, llm=llm)
    await _enqueue(arq, run.id)
    return run


@router.get("", response_model=RunList)
async def list_runs(
    session: AsyncSession = Depends(get_session),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> RunList:
    total = (await session.execute(select(func.count(Run.id)))).scalar_one()
    rows = (await session.execute(select(Run).order_by(Run.created_at.desc()).limit(limit).offset(offset))).scalars().all()
    return RunList(total=total, items=[RunOut.model_validate(r) for r in rows])


@router.get("/{run_id}", response_model=RunDetail)
async def get_run(run_id: str, session: AsyncSession = Depends(get_session)) -> Run:
    return await _get_run(session, run_id)


@router.post("/{run_id}/cancel", response_model=RunOut)
async def cancel_run(run_id: str, session: AsyncSession = Depends(get_session)) -> Run:
    run = await _get_run(session, run_id)
    if run.status in TERMINAL:
        raise ConflictError(f"Run is already {run.status}.")
    run.cancel_requested = True
    # A queued run, or a running one whose worker is gone (stale heartbeat), is canceled right away;
    # a live worker picks the flag up between stages.
    if run.status == "queued" or (run.status == "running" and is_stale(run)):
        run.status = "canceled"
        run.finished_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(run)
    return run


@router.post("/{run_id}/retry", status_code=202, response_model=RunOut)
async def retry_run(run_id: str, session: AsyncSession = Depends(get_session), arq=Depends(get_arq)) -> Run:
    run = await _get_run(session, run_id)
    orphaned = run.status == "running" and is_stale(run)
    if run.status not in ("failed", "canceled") and not orphaned:
        raise ConflictError("Only failed, canceled or orphaned runs can be retried.")
    run.status = "queued"
    run.cancel_requested = False
    await session.commit()
    await session.refresh(run)
    await _enqueue(arq, run.id)
    return run


@router.get("/{run_id}/events/list", response_model=list[EventOut])
async def list_events(run_id: str, after: int = 0, session: AsyncSession = Depends(get_session)) -> list[RunEvent]:
    await _get_run(session, run_id)
    rows = await session.execute(
        select(RunEvent).where(RunEvent.run_id == run_id, RunEvent.seq > after).order_by(RunEvent.seq).limit(1000)
    )
    return list(rows.scalars().all())


@router.get("/{run_id}/events")
async def stream_events(
    run_id: str,
    request: Request,
    after: int = 0,
    last_event_id: str | None = Header(default=None),
) -> StreamingResponse:
    """Server-Sent Events: replays persisted events after `after`/Last-Event-ID, then tails until the run ends."""
    async with get_sessionmaker()() as s:
        await _get_run(s, run_id)
    start = after
    if last_event_id and last_event_id.isdigit():
        start = max(start, int(last_event_id))

    async def gen():
        last = start
        idle_ticks = 0
        while True:
            if await request.is_disconnected():
                return
            async with get_sessionmaker()() as s:
                rows = (
                    await s.execute(
                        select(RunEvent).where(RunEvent.run_id == run_id, RunEvent.seq > last).order_by(RunEvent.seq).limit(200)
                    )
                ).scalars().all()
                status = (await s.execute(select(Run.status).where(Run.id == run_id))).scalar()
            for r in rows:
                data = EventOut.model_validate(r, from_attributes=True).model_dump(mode="json")
                yield f"id: {r.seq}\nevent: run_event\ndata: {json.dumps(data)}\n\n"
                last = r.seq
            if not rows:
                if status in TERMINAL:
                    yield f"event: end\ndata: {json.dumps({'status': status})}\n\n"
                    return
                idle_ticks += 1
                if idle_ticks % 30 == 0:
                    yield ": keep-alive\n\n"
            else:
                idle_ticks = 0
            await asyncio.sleep(0.5)

    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@router.get("/{run_id}/summary", response_model=SummaryOut)
async def get_summary(run_id: str, session: AsyncSession = Depends(get_session)) -> RunSummary:
    await _get_run(session, run_id)
    summary = await session.get(RunSummary, run_id)
    if summary is None:
        raise NotFoundError("The summary is not available yet.")
    return summary


SORTS = {
    "severity": (OrderOutcome.severity_score.desc(), OrderOutcome.days_late.desc()),
    "days_late": (OrderOutcome.days_late.desc(), OrderOutcome.severity_score.desc()),
    "value": (OrderOutcome.value_brl.desc(),),
    "weight": (OrderOutcome.billable_weight_kg.desc(),),
}


@router.get("/{run_id}/orders", response_model=OrderPage)
async def list_orders(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    severity: list[str] | None = Query(None),
    action: list[str] | None = Query(None),
    stage: list[str] | None = Query(None),
    affected: bool | None = Query(None, description="true = only orders delayed by the disruption"),
    needs_review: bool | None = None,
    dest_state: str | None = None,
    origin_state: str | None = None,
    q: str | None = Query(None, max_length=64),
    sort: Literal["severity", "days_late", "value", "weight"] = "severity",
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> OrderPage:
    await _get_run(session, run_id)
    conds = [OrderOutcome.run_id == run_id]
    if severity:
        conds.append(OrderOutcome.severity_tier.in_(severity))
    if action:
        conds.append(OrderOutcome.action.in_(action))
    if stage:
        conds.append(OrderOutcome.stage.in_(stage))
    if affected is not None:
        conds.append(OrderOutcome.affected.is_(affected))
    if needs_review is not None:
        conds.append(OrderOutcome.needs_review.is_(needs_review))
    if dest_state:
        conds.append(OrderOutcome.dest_state == dest_state.upper())
    if origin_state:
        conds.append(OrderOutcome.origin_states.any(origin_state.upper()))
    if q:
        term = q.strip().lower()
        conds.append(or_(OrderOutcome.order_id.startswith(term), OrderOutcome.customer_unique_id.startswith(term)))
    total = (await session.execute(select(func.count()).select_from(OrderOutcome).where(*conds))).scalar_one()
    rows = (
        await session.execute(
            select(OrderOutcome).where(*conds).order_by(*SORTS[sort], OrderOutcome.order_id).limit(limit).offset(offset)
        )
    ).scalars().all()
    return OrderPage(total=total, items=[OrderRow.model_validate(r) for r in rows])


@router.get("/{run_id}/orders/{order_id}", response_model=OrderDetail)
async def order_detail(run_id: str, order_id: str, session: AsyncSession = Depends(get_session)) -> OrderDetail:
    run = await _get_run(session, run_id)
    row = await session.get(OrderOutcome, {"run_id": run_id, "order_id": order_id})
    if row is None:
        raise NotFoundError(f"Order {order_id} is not part of run {run_id}.")
    comm = (
        await session.execute(
            select(Communication).where(Communication.run_id == run_id, Communication.order_ids.any(order_id))
        )
    ).scalars().first()
    impact = OrderImpact.model_validate(row.detail["impact"])
    decision = MitigationDecision.model_validate(row.detail["decision"])
    comm_out = CommunicationOut.model_validate(comm) if comm else None
    trace = build_trace(DisruptionSpec.model_validate(run.spec), impact, decision, comm_out.model_dump() if comm_out else None) if run.spec else []
    kb = kb_index(get_settings().kb_dir)
    citations = [
        {"id": ref, "title": f"{kb[ref].doc_title}: {kb[ref].heading}", "text": kb[ref].text}
        for ref in decision.policy_refs if ref in kb
    ]
    return OrderDetail(
        row=OrderRow.model_validate(row), impact=row.detail["impact"], decision=row.detail["decision"],
        trace=trace, policy_citations=citations, communication=comm_out,
    )


@router.get("/{run_id}/communications", response_model=CommunicationPage)
async def list_communications(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    status: list[str] | None = Query(None),
    generator: list[str] | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> CommunicationPage:
    await _get_run(session, run_id)
    conds = [Communication.run_id == run_id]
    if status:
        conds.append(Communication.status.in_(status))
    if generator:
        conds.append(Communication.generator.in_(generator))
    total = (await session.execute(select(func.count()).select_from(Communication).where(*conds))).scalar_one()
    rows = (
        await session.execute(
            select(Communication).where(*conds)
            .order_by(Communication.max_severity.desc(), Communication.customer_unique_id).limit(limit).offset(offset)
        )
    ).scalars().all()
    return CommunicationPage(total=total, items=[CommunicationOut.model_validate(r) for r in rows])


@router.post("/{run_id}/communications/approve", response_model=BulkResult)
async def bulk_approve(
    run_id: str, req: IdsRequest, session: AsyncSession = Depends(get_session),
    user: Principal = Depends(require_approver), arq=Depends(get_arq),
) -> BulkResult:
    from app.services.comm_ops import approve_communication
    from app.workers.mail_worker import enqueue_send

    await _get_run(session, run_id)
    rows = (
        await session.execute(select(Communication).where(Communication.run_id == run_id, Communication.id.in_(req.ids)))
    ).scalars().all()
    updated, skipped, detail = 0, 0, []
    for c in rows:
        if c.status == "draft":
            await approve_communication(session, c, user.email)
            await enqueue_send(arq, c.id)
            updated += 1
        else:
            skipped += 1
            detail.append(f"{c.id}: status is {c.status}")
    skipped += len(req.ids) - len(rows)
    return BulkResult(updated=updated, skipped=skipped, detail=detail[:50])


@router.post("/{run_id}/communications/draft", status_code=202, response_model=BulkResult)
async def bulk_draft(
    run_id: str, req: IdsRequest, session: AsyncSession = Depends(get_session), arq=Depends(get_arq),
    llm: LLMService = Depends(get_llm),
) -> BulkResult:
    """Queue LLM drafting for template emails (runs in the worker)."""
    from app.core.errors import DomainError

    if not llm.enabled:
        raise DomainError("The LLM is not configured (offline mode).", title="LLM unavailable", status_code=503)
    cap = get_settings().max_bulk_drafts
    if len(req.ids) > cap:
        raise DomainError(
            f"At most {cap} emails can be drafted with AI per request (free-tier quota protection).",
            title="Too many emails", status_code=422,
        )
    await _get_run(session, run_id)
    ids = (
        await session.execute(
            select(Communication.id).where(Communication.run_id == run_id, Communication.id.in_(req.ids))
        )
    ).scalars().all()
    if ids:
        await arq.enqueue_job("draft_emails_job", run_id, list(ids))
    return BulkResult(updated=len(ids), skipped=len(req.ids) - len(ids), detail=["queued"] if ids else [])


@router.get("/{run_id}/export")
async def export(
    run_id: str, format: Literal["md", "json", "csv"] = "md", session: AsyncSession = Depends(get_session)
):
    run = await _get_run(session, run_id)
    summary = await session.get(RunSummary, run_id)
    orders = (
        await session.execute(
            select(OrderOutcome).where(OrderOutcome.run_id == run_id)
            .order_by(OrderOutcome.severity_score.desc(), OrderOutcome.order_id)
        )
    ).scalars().all()
    order_dicts = [OrderRow.model_validate(o).model_dump() for o in orders]
    short = run.id[:8]
    if format == "csv":
        return PlainTextResponse(
            orders_csv(order_dicts), media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="run-{short}-orders.csv"'},
        )
    comms = (
        await session.execute(
            select(Communication).where(Communication.run_id == run_id).order_by(Communication.max_severity.desc())
        )
    ).scalars().all()
    comm_dicts = [CommunicationOut.model_validate(c).model_dump(mode="json") for c in comms]
    run_dict = RunDetail.model_validate(run).model_dump(mode="json")
    summary_dict = SummaryOut.model_validate(summary).model_dump(mode="json") if summary else None
    if format == "json":
        payload = {"run": run_dict, "summary": summary_dict,
                   "orders": [OrderRow.model_validate(o).model_dump(mode="json") for o in orders],
                   "communications": comm_dicts}
        return PlainTextResponse(
            json.dumps(payload, ensure_ascii=False, indent=1), media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="run-{short}.json"'},
        )
    llm_first = sorted(comm_dicts, key=lambda c: (c["generator"] != "llm", -c["max_severity"]))
    md = report_markdown(run_dict, summary_dict, order_dicts, llm_first,
                         [i.model_dump() for i in get_assumptions().items])
    return PlainTextResponse(
        md, media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="run-{short}-report.md"'},
    )
