"""Operations on stored communications: human edits (re-QA'd), LLM regeneration, approvals."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog, Communication
from app.llm.outputs import EmailBatchOut
from app.llm.service import LLMService
from app.services.comms import CustomerFacts, QAAllowances, render, unrender
from app.services.qa import QAReport, check_email
from app.services.retrieval.kb import load_kb

MAX_ATTEMPTS = 3


def tone_guide_text(kb_dir: Path) -> str:
    chunks = [c for c in load_kb(kb_dir) if c.chunk_id.startswith("customer_comms_tone_guide#")]
    return "\n\n".join(f"{c.heading}:\n{c.text}" for c in chunks)


def facts_of(comm: Communication) -> tuple[CustomerFacts, QAAllowances]:
    return CustomerFacts.model_validate(comm.facts["facts"]), QAAllowances.model_validate(comm.facts["allow"])


def qa_rendered(subject: str, body: str, facts: CustomerFacts, allow: QAAllowances) -> QAReport:
    return check_email(unrender(subject, allow), unrender(body, allow), facts, allow)


async def apply_human_edit(session: AsyncSession, comm: Communication, subject: str, body: str, actor: str) -> Communication:
    facts, allow = facts_of(comm)
    report = qa_rendered(subject, body, facts, allow)
    before = {"subject": comm.subject, "body": comm.body, "version": comm.version}
    comm.subject, comm.body = subject, body
    comm.generator = "human"
    comm.qa = {**report.model_dump(), "generator": "human"}
    comm.status = "draft" if report.passed else "qa_failed"
    comm.version += 1
    session.add(AuditLog(actor=actor, action="communication.edit", entity="communication", entity_id=comm.id,
                         detail={"before": before, "qa_passed": report.passed}))
    await session.commit()
    await session.refresh(comm)
    return comm


async def regenerate_with_llm(
    session: AsyncSession, comm: Communication, llm: LLMService, kb_dir: Path, actor: str
) -> tuple[Communication, list[str]]:
    """Redraft one email with the LLM (QA loop). Returns (communication, failures). Unchanged when every attempt fails."""
    facts, allow = facts_of(comm)
    feedback = ""
    failures: list[str] = []
    for _ in range(MAX_ATTEMPTS):
        res = await llm.generate(
            agent="customer_success", role="email", prompt="email",
            context={"locale": facts.locale, "tone_guide": tone_guide_text(kb_dir),
                     "facts_json": json.dumps([facts.model_dump()], ensure_ascii=False, indent=1), "feedback": feedback},
            schema=EmailBatchOut, run_id=comm.run_id, max_output_tokens=8192,
            use_cache=False,  # the user explicitly asked for a new draft
        )
        if not res.parsed.emails:
            failures = ["The model returned no email."]
            feedback = "- Return exactly one email."
            continue
        out = res.parsed.emails[0]
        report = check_email(out.subject.strip(), out.body.strip(), facts, allow)
        if report.passed:
            before = {"subject": comm.subject, "body": comm.body, "version": comm.version, "generator": comm.generator}
            comm.subject, comm.body = render(out.subject.strip(), allow), render(out.body.strip(), allow)
            comm.generator, comm.model, comm.prompt_version = "llm", res.model, res.prompt_version
            comm.qa = {**report.model_dump(), "generator": "llm"}
            comm.status = "draft"
            comm.version += 1
            comm.attempts += 1
            session.add(AuditLog(actor=actor, action="communication.regenerate", entity="communication",
                                 entity_id=comm.id, detail={"before": before}))
            await session.commit()
            await session.refresh(comm)
            return comm, []
        failures = report.failures()
        feedback = "\n".join(f"- customer_ref {facts.customer_ref}: {f}" for f in failures)
    return comm, failures


async def approve_communication(session: AsyncSession, comm: Communication, actor: str) -> Communication:
    comm.approved_by = actor
    comm.send_error = None
    return await set_status(session, comm, "approved", actor)


async def set_status(session: AsyncSession, comm: Communication, status: str, actor: str) -> Communication:
    before = comm.status
    comm.status = status
    session.add(AuditLog(actor=actor, action=f"communication.{status}", entity="communication", entity_id=comm.id,
                         detail={"before": before}))
    await session.commit()
    await session.refresh(comm)
    return comm
