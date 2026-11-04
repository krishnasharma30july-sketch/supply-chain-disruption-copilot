"""Tone and empathy review: advisory scoring, improvement-gated rewrites, and safe behaviour when the LLM is unavailable."""

import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.agents.base import EmailDraft
from app.agents.customer_success import CustomerSuccessAgent
from app.agents.qa_critic import QACriticAgent
from app.core.config import get_settings
from app.llm.outputs import EmailBatchOut, EmailDraftOut, ToneBatchOut, ToneReviewOut
from app.llm.service import LLMUnavailable
from app.orchestrator.events import EventEmitter
from app.services.comms import build_customer_facts, template_email
from tests.conftest import make_line, sp_spec
from tests.test_policy_and_qa import decide


class FakeLLM:
    """Scripted reviewer and writer. `scores` maps call number -> (warmth, clarity); `rewrite` builds replacement text."""

    available = True

    def __init__(self, scores, rewrite=None, fail_review=False):
        self.scores = scores
        self.rewrite = rewrite
        self.fail_review = fail_review
        self.prompts: list[str] = []

    async def generate(self, **kw):
        self.prompts.append(kw["prompt"])
        if kw["prompt"] == "tone":
            if self.fail_review:
                raise LLMUnavailable("quota")
            n = self.prompts.count("tone")
            refs = [e["customer_ref"] for e in json.loads(kw["context"]["emails_json"])]
            w, c = self.scores(n)
            parsed = ToneBatchOut(reviews=[ToneReviewOut(customer_ref=r, warmth=w, clarity=c, notes="Open with the apology.") for r in refs])
        else:
            facts = json.loads(kw["context"]["facts_json"])
            subject, body = template_email_for(facts[0])
            parsed = EmailBatchOut(emails=[EmailDraftOut(customer_ref=f["customer_ref"], subject=subject, body=self.rewrite(body)) for f in facts])
        return SimpleNamespace(parsed=parsed, model="fake-model", prompt_version="v", cached=False)


_TEMPLATES: dict[str, tuple[str, str]] = {}


def template_email_for(fact: dict):
    return _TEMPLATES[fact["customer_ref"]]


def make_draft(engine_ctx, policy_ctx_factory, generator="llm") -> EmailDraft:
    imp, dec = decide(make_line(handling_profile="BULKY", billable_weight_kg=35.0, sla_ts=datetime(2018, 8, 8)), engine_ctx, policy_ctx_factory)
    facts, allow = build_customer_facts(imp.customer_unique_id, "Ana", [(imp, dec)], sp_spec(), "en", "run-1")
    subject, body = template_email(facts)
    _TEMPLATES[facts.customer_ref] = (subject, body)
    d = EmailDraft(customer_unique_id=imp.customer_unique_id, first_name="Ana", order_ids=[imp.order_id],
                   max_severity=70.0, facts=facts, allow=allow, subject=subject, body=body, generator=generator)
    return d


def run(draft, llm, **settings):
    ctx = SimpleNamespace(
        settings=get_settings().model_copy(update=settings), llm=llm, emitter=EventEmitter("r", None), use_llm=True,
        run_id="r", session_factory=None,
    )
    agent = QACriticAgent(CustomerSuccessAgent())
    assert agent._check(draft)  # the fact check has already passed when the tone review runs
    import asyncio

    summary = asyncio.run(agent._tone_review([draft], ctx))
    return summary, ctx


def test_strong_emails_are_rated_and_left_alone(engine_ctx, policy_ctx_factory):
    d = make_draft(engine_ctx, policy_ctx_factory)
    before = d.body
    llm = FakeLLM(lambda n: (5, 4))
    summary, ctx = run(d, llm)
    assert d.body == before and llm.prompts == ["tone"]  # one review call, no rewrite
    assert d.qa["tone"] == {"warmth": 5, "clarity": 4, "notes": "Open with the apology.", "rewritten": False}
    assert summary["reviewed"] == 1 and summary["weak"] == 0 and summary["avg_warmth"] == 5
    assert d.qa["passed"]  # advisory only: the fact-check result is untouched


def test_a_weak_email_is_rewritten_and_kept_only_when_it_scores_higher(engine_ctx, policy_ctx_factory):
    d = make_draft(engine_ctx, policy_ctx_factory)
    original = d.body
    # First review: warmth 3 (weak). Second review of the rewrite: 5 and 5.
    llm = FakeLLM(lambda n: (3, 4) if n == 1 else (5, 5), rewrite=lambda b: b + "\n\nWe truly appreciate your patience.")
    summary, _ = run(d, llm)
    assert llm.prompts == ["tone", "email", "tone"]
    assert d.body != original and "truly appreciate" in d.body
    assert d.qa["passed"] and d.qa["tone"]["rewritten"] is True
    assert d.qa["tone"]["before"] == {"warmth": 3, "clarity": 4}
    assert (d.qa["tone"]["warmth"], d.qa["tone"]["clarity"]) == (5, 5)
    assert summary["rewrites_kept"] == 1


def test_a_rewrite_that_does_not_read_better_is_discarded(engine_ctx, policy_ctx_factory):
    d = make_draft(engine_ctx, policy_ctx_factory)
    original = d.body
    llm = FakeLLM(lambda n: (3, 4), rewrite=lambda b: b + "\n\nThanks for waiting.")  # re-review gives the same 3 and 4
    summary, _ = run(d, llm)
    assert d.body == original and d.qa["tone"]["rewritten"] is False and summary["rewrites_kept"] == 0


def test_a_rewrite_that_breaks_the_fact_check_is_never_used(engine_ctx, policy_ctx_factory):
    d = make_draft(engine_ctx, policy_ctx_factory)
    original = d.body
    llm = FakeLLM(lambda n: (2, 2) if n == 1 else (5, 5), rewrite=lambda b: b + "\n\nWe will also credit you R$ 99.99.")
    summary, _ = run(d, llm)
    assert d.body == original and d.qa["passed"] and d.qa["tone"]["rewritten"] is False
    assert llm.prompts == ["tone", "email"]  # no second review for a rewrite that already failed the fact check
    assert summary["rewrites_kept"] == 0


def test_an_unavailable_reviewer_skips_the_step_without_blocking(engine_ctx, policy_ctx_factory):
    d = make_draft(engine_ctx, policy_ctx_factory)
    summary, _ = run(d, FakeLLM(lambda n: (5, 5), fail_review=True))
    assert summary is None and "tone" not in d.qa and d.qa["passed"]


def test_template_emails_and_disabled_reviews_are_not_reviewed(engine_ctx, policy_ctx_factory):
    template = make_draft(engine_ctx, policy_ctx_factory, generator="template")
    llm = FakeLLM(lambda n: (5, 5))
    summary, _ = run(template, llm)
    assert summary is None and llm.prompts == []

    ai = make_draft(engine_ctx, policy_ctx_factory)
    llm2 = FakeLLM(lambda n: (5, 5))
    summary2, _ = run(ai, llm2, tone_review_enabled=False)
    assert summary2 is None and llm2.prompts == []


@pytest.mark.parametrize("raw,expected", [(9, 5), (0, 1), (-3, 1), (4, 4)])
def test_scores_are_clamped_to_the_scale(raw, expected):
    from app.services.tone import _clamp

    assert _clamp(raw) == expected
