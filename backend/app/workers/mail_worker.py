"""Mail worker: delivers approved emails from its own queue, so sending never waits behind a long pipeline run."""

from __future__ import annotations

from arq import Retry, func
from arq.connections import RedisSettings
from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.models import Communication
from app.db.session import get_sessionmaker
from app.services.mailer import send_communication

log = get_logger(__name__)

MAIL_QUEUE = "arq:mail"
SEND_MAX_TRIES = 4


async def startup(ctx: dict) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    ids: list[str] = []
    if settings.smtp_host:
        # Approved emails whose job was lost (e.g. Redis restarted) are queued again.
        async with get_sessionmaker()() as s:
            ids = list((await s.execute(
                select(Communication.id).where(Communication.status == "approved", Communication.sent_at.is_(None),
                                               Communication.send_error.is_(None))
            )).scalars().all())
        for comm_id in ids:
            await enqueue_send(ctx["redis"], comm_id)
    log.info("mail_worker_started", smtp=bool(settings.smtp_host), pending=len(ids))


async def send_communication_job(ctx: dict, comm_id: str) -> str:
    """Deliver one approved email; SMTP failures are retried with backoff and the last error is kept."""
    settings = get_settings()
    if not settings.smtp_host:
        return "smtp_not_configured"
    async with get_sessionmaker()() as s:
        comm = await s.get(Communication, comm_id)
        if comm is None or comm.status != "approved":
            return "skipped"
        try:
            await send_communication(s, comm, settings)
            return "sent"
        except Exception as e:  # SMTP / network errors
            comm.send_error = f"{type(e).__name__}: {str(e)[:300]}"
            await s.commit()
            tries = ctx.get("job_try", 1)
            log.warning("email_send_failed", comm_id=comm_id, attempt=tries, error=comm.send_error)
            if tries < SEND_MAX_TRIES:
                raise Retry(defer=10 * tries) from e
            return "failed"


async def enqueue_send(arq, comm_id: str, attempt: str = "") -> None:
    # A fixed job id makes double-approval clicks enqueue one delivery; a manual resend passes a new attempt tag.
    await arq.enqueue_job("send_communication_job", comm_id, _job_id=f"send:{comm_id}{attempt}", _queue_name=MAIL_QUEUE)


class MailWorkerSettings:
    functions = [func(send_communication_job, max_tries=SEND_MAX_TRIES)]
    on_startup = startup
    queue_name = MAIL_QUEUE
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 10
    job_timeout = 120
    keep_result = 600
    health_check_interval = 30
