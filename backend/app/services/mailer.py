"""Outbound email for approved communications (SMTP; Mailpit in the local stack)."""

from __future__ import annotations

import asyncio
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import AuditLog, Communication


def synthetic_address(customer_unique_id: str) -> str:
    """Olist has no customer contact data (A14), so every customer gets a stable address on a reserved domain."""
    return f"customer-{customer_unique_id[:12]}@customers.example"


def build_message(comm: Communication, settings: Settings) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = comm.email_to or synthetic_address(comm.customer_unique_id)
    msg["Subject"] = comm.subject
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(idstring=comm.id.replace("-", "")[:16], domain="olist.example")
    msg["X-Copilot-Run"] = comm.run_id
    msg["X-Copilot-Communication"] = f"{comm.id} v{comm.version}"
    msg["Content-Language"] = comm.locale
    msg.set_content(comm.body)
    return msg


def _send_blocking(msg: EmailMessage, settings: Settings) -> None:
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout_s) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password or "")
        smtp.send_message(msg)


async def send_communication(session: AsyncSession, comm: Communication, settings: Settings) -> Communication:
    """Send one approved email. Raises on SMTP errors (the caller retries); idempotent once sent."""
    if comm.status != "approved" or comm.sent_at is not None:
        return comm
    comm.email_to = comm.email_to or synthetic_address(comm.customer_unique_id)
    msg = build_message(comm, settings)
    await asyncio.to_thread(_send_blocking, msg, settings)
    comm.status = "sent"
    comm.sent_at = datetime.now(timezone.utc)
    comm.message_id = msg["Message-ID"]
    comm.send_error = None
    session.add(AuditLog(actor="worker", action="communication.sent", entity="communication", entity_id=comm.id,
                         detail={"to": comm.email_to, "message_id": comm.message_id, "version": comm.version}))
    await session.commit()
    await session.refresh(comm)
    return comm
