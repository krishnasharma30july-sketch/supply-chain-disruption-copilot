"""QA Critic agent: checks every draft against the facts and policy, asks for redrafts, falls back to templates."""

from __future__ import annotations

import uuid

from sqlalchemy import insert

from app.agents.base import AgentContext, EmailDraft, RunState
from app.agents.customer_success import CustomerSuccessAgent
from app.db.models import Communication
from app.services.comms import render
from app.services.qa import check_email

MAX_REDRAFT_ROUNDS = 2


class QACriticAgent:
    name = "qa_critic"
    label = "Fact-checking emails against the decisions and policy"

    def __init__(self, writer: CustomerSuccessAgent):
        self.writer = writer

    def _check(self, d: EmailDraft) -> bool:
        report = check_email(d.subject, d.body, d.facts, d.allow)
        d.qa = report.model_dump()
        d.qa["generator"] = d.generator
        return report.passed

    async def run(self, state: RunState, ctx: AgentContext) -> None:
        drafts = state.drafts
        ai_written = sum(1 for d in drafts if d.generator == "llm")
        failing = [d for d in drafts if d.generator == "llm" and not self._check(d)]
        for d in drafts:
            if d.generator != "llm":
                self._check(d)
        initial_fail = len(failing)
        rounds = 0
        while failing and rounds < MAX_REDRAFT_ROUNDS and ctx.llm.available:
            rounds += 1
            feedback = {d.customer_unique_id: [f for f in _failures(d)] for d in failing}
            await ctx.emitter.emit(
                self.name, "info",
                f"Round {rounds}: {_emails(len(failing), 'AI-written email')} did not pass the fact check and "
                f"{'was' if len(failing) == 1 else 'were'} sent back to the writer with the reasons to fix.",
                {"issues": {d.facts.customer_ref: feedback[d.customer_unique_id][:3] for d in failing[:5]}},
            )
            await self.writer.write_llm(failing, ctx, feedback)
            failing = [d for d in failing if not self._check(d)]

        fell_back = 0
        for d in failing:
            self.writer.write_template(d)
            d.llm_feedback = _failures(d)
            fell_back += 1
            self._check(d)
        for d in drafts:
            d.status = "draft" if d.qa.get("passed") else "qa_failed"

        if ctx.session_factory is not None and drafts:
            rows = [
                {
                    "id": str(uuid.uuid4()), "run_id": ctx.run_id, "customer_unique_id": d.customer_unique_id,
                    "order_ids": d.order_ids, "first_name": d.first_name, "locale": d.facts.locale,
                    "subject": render(d.subject, d.allow), "body": render(d.body, d.allow),
                    "generator": d.generator, "model": d.model, "prompt_version": d.prompt_version,
                    "status": d.status, "qa": d.qa,
                    "facts": {"facts": d.facts.model_dump(), "allow": d.allow.model_dump(), "llm_feedback": d.llm_feedback},
                    "max_severity": d.max_severity, "version": 1, "attempts": max(1, d.attempts),
                }
                for d in drafts
            ]
            async with ctx.session_factory() as s:
                for start in range(0, len(rows), 500):
                    await s.execute(insert(Communication), rows[start : start + 500])
                await s.commit()

        passed = sum(1 for d in drafts if d.status == "draft")
        fixed_by_ai = initial_fail - fell_back
        await ctx.emitter.emit(
            self.name, "result",
            _summary(passed, len(drafts), initial_fail, fixed_by_ai, fell_back, ai_written),
            {"passed": passed, "total": len(drafts), "initial_llm_failures": initial_fail,
             "fixed_by_rewrite": fixed_by_ai, "template_fallbacks": fell_back},
        )
        state.metrics["communications"] = {
            "total": len(drafts),
            "llm": sum(1 for d in drafts if d.generator == "llm"),
            "template": sum(1 for d in drafts if d.generator == "template"),
            "qa_passed": passed,
            "qa_failed": len(drafts) - passed,
            "llm_initial_failures": initial_fail,
            "template_fallbacks": fell_back,
        }


def _emails(n: int, noun: str = "email") -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def _summary(passed: int, total: int, flagged: int, fixed: int, replaced: int, ai_written: int = 1) -> str:
    """Plain-language result: how many emails passed, and how every flagged AI-written email was resolved."""
    head = (f"All {_emails(total)} passed the fact check." if passed == total
            else f"{passed} of {_emails(total)} passed the fact check.")
    if ai_written == 0:
        return head + " All were written from verified templates (no AI drafting in this run)."
    if flagged == 0:
        return head + " Every AI-written email was correct on the first try."
    needed = f"{_emails(flagged, 'AI-written email')} needed a correction"
    if replaced == 0:
        return f"{head} {needed} and {'was' if flagged == 1 else 'all were'} fixed by the AI on rewrite."
    outcome = ([f"{fixed} fixed by the AI on rewrite"] if fixed else []) + [
        f"{replaced} replaced with a verified template because the rewrite still failed"]
    return f"{head} {needed}: {', '.join(outcome)}."


def _failures(d: EmailDraft) -> list[str]:
    return [f"{c['name']}: {c['detail']}" for c in d.qa.get("checks", []) if not c.get("passed")]
