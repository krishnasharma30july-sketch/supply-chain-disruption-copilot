import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import Principal, current_user, require_approver
from app.api.deps import get_arq, get_llm
from app.api.schemas import CommunicationEdit, CommunicationOut
from app.core.config import get_settings
from app.core.errors import ConflictError, DomainError, NotFoundError, UnprocessableError
from app.db.models import Communication
from app.db.session import get_session
from app.llm.service import LLMCallError, LLMService, LLMUnavailable
from app.services.comm_ops import apply_human_edit, approve_communication, regenerate_with_llm, set_status
from app.workers.mail_worker import enqueue_send

router = APIRouter(prefix="/communications", tags=["communications"])
LOCKED = {"approved", "sent"}


async def _get(session: AsyncSession, comm_id: str) -> Communication:
    comm = await session.get(Communication, comm_id)
    if comm is None:
        raise NotFoundError(f"Communication {comm_id} not found.")
    return comm


@router.get("/{comm_id}", response_model=CommunicationOut)
async def get_communication(comm_id: str, session: AsyncSession = Depends(get_session)) -> Communication:
    return await _get(session, comm_id)


@router.patch("/{comm_id}", response_model=CommunicationOut)
async def edit_communication(
    comm_id: str, req: CommunicationEdit, session: AsyncSession = Depends(get_session),
    user: Principal = Depends(current_user),
) -> Communication:
    """Human edit. The edited text is re-checked by the QA critic; failing edits are saved as qa_failed."""
    comm = await _get(session, comm_id)
    if comm.status in LOCKED:
        raise ConflictError(f"{comm.status.capitalize()} emails are locked." + (" Reject it first to edit." if comm.status == "approved" else ""))
    return await apply_human_edit(session, comm, req.subject.strip(), req.body.strip(), user.email)


@router.post("/{comm_id}/approve", response_model=CommunicationOut)
async def approve(
    comm_id: str, session: AsyncSession = Depends(get_session), user: Principal = Depends(require_approver),
    arq=Depends(get_arq),
) -> Communication:
    """Approve and queue delivery (SMTP). Requires the approver role."""
    comm = await _get(session, comm_id)
    if comm.status == "qa_failed":
        raise ConflictError("This email failed QA. Edit or regenerate it before approving.")
    if comm.status in LOCKED:
        return comm
    comm = await approve_communication(session, comm, user.email)
    await enqueue_send(arq, comm.id)
    return comm


@router.post("/{comm_id}/send", response_model=CommunicationOut)
async def resend(
    comm_id: str, session: AsyncSession = Depends(get_session), _user: Principal = Depends(require_approver),
    arq=Depends(get_arq),
) -> Communication:
    """Retry delivery of an approved email whose sending failed."""
    comm = await _get(session, comm_id)
    if comm.status != "approved":
        raise ConflictError(f"Only approved, unsent emails can be sent (status is {comm.status}).")
    if not get_settings().smtp_host:
        raise DomainError("Outbound email is not configured (SMTP_HOST).", title="Email disabled", status_code=503)
    comm.send_error = None
    await session.commit()
    await enqueue_send(arq, comm.id, attempt=f":{uuid.uuid4().hex[:6]}")
    return comm


@router.post("/{comm_id}/reject", response_model=CommunicationOut)
async def reject(
    comm_id: str, session: AsyncSession = Depends(get_session), user: Principal = Depends(require_approver)
) -> Communication:
    comm = await _get(session, comm_id)
    if comm.status == "sent":
        raise ConflictError("This email was already sent.")
    return await set_status(session, comm, "rejected", user.email)


@router.post("/{comm_id}/regenerate", response_model=CommunicationOut)
async def regenerate(
    comm_id: str, session: AsyncSession = Depends(get_session), llm: LLMService = Depends(get_llm),
    user: Principal = Depends(current_user),
) -> Communication:
    """Redraft with the LLM (QA loop up to 3 attempts). The current version is kept if every attempt fails QA."""
    comm = await _get(session, comm_id)
    if comm.status in LOCKED:
        raise ConflictError(f"{comm.status.capitalize()} emails are locked." + (" Reject it first to regenerate." if comm.status == "approved" else ""))
    if not llm.available:
        raise DomainError("The LLM is not available (offline mode or circuit breaker open).", title="LLM unavailable", status_code=503)
    try:
        comm, failures = await regenerate_with_llm(session, comm, llm, get_settings().kb_dir, user.email)
    except (LLMUnavailable, LLMCallError) as e:
        raise DomainError(str(e), title="LLM unavailable", status_code=503) from e
    if failures:
        raise UnprocessableError("The regenerated email failed QA; the previous version was kept. " + "; ".join(failures[:4]))
    return comm
