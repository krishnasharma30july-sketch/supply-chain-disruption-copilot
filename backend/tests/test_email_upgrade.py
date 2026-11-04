"""Background AI coverage: template emails become fact-checked LLM drafts without ever overriding a person's work."""

import asyncio
import json
import uuid
from datetime import datetime
from types import SimpleNamespace

from app.core.config import get_settings
from app.db.models import Communication
from app.llm.outputs import EmailBatchOut, EmailDraftOut, ToneBatchOut, ToneReviewOut
from app.llm.service import LLMUnavailable
from app.services.comms import build_customer_facts, render, template_email
from app.services.email_upgrade import FAILED, upgrade_batch
from tests.conftest import make_line, sp_spec
from tests.test_policy_and_qa import decide


class FakeSession:
    def __init__(self, on_refresh=None):
        self.added, self.commits, self.on_refresh = [], 0, on_refresh

    async def refresh(self, obj):
        if self.on_refresh:
            self.on_refresh(obj)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


class UpgradeLLM:
    available = True

    def __init__(self, corrupt=(), unavailable=False):
        self.corrupt = corrupt  # e.g. (1,) corrupts the first email call; ("all",) corrupts every call
        self.unavailable = unavailable
        self.prompts: list[str] = []

    async def generate(self, **kw):
        self.prompts.append(kw["prompt"])
        if self.unavailable:
            raise LLMUnavailable("quota")
        if kw["prompt"] == "tone":
            refs = [e["customer_ref"] for e in json.loads(kw["context"]["emails_json"])]
            parsed = ToneBatchOut(reviews=[ToneReviewOut(customer_ref=r, warmth=5, clarity=4, notes="None.") for r in refs])
        else:
            n = self.prompts.count("email")
            bad = n in self.corrupt or "all" in self.corrupt
            emails = []
            for f in json.loads(kw["context"]["facts_json"]):
                subject, body = TEMPLATES[f["customer_ref"]]
                emails.append(EmailDraftOut(customer_ref=f["customer_ref"], subject=subject,
                                            body=body + ("\n\nWe will credit you R$ 999.99." if bad else "\n\nThank you for your patience.")))
            parsed = EmailBatchOut(emails=emails)
        return SimpleNamespace(parsed=parsed, model="fake-model", prompt_version="email-v3", cached=False)


TEMPLATES: dict[str, tuple[str, str]] = {}


def make_comm(engine_ctx, policy_ctx_factory, n: int) -> Communication:
    line = make_line(order_id=str(n) * 32, customer_unique_id=str(n) * 32, handling_profile="BULKY",
                     billable_weight_kg=35.0, sla_ts=datetime(2018, 8, 8))
    imp, dec = decide(line, engine_ctx, policy_ctx_factory)
    facts, allow = build_customer_facts(imp.customer_unique_id, "Ana", [(imp, dec)], sp_spec(), "en", "run-1")
    subject, body = template_email(facts)
    TEMPLATES[facts.customer_ref] = (subject, body)
    return Communication(
        id=str(uuid.uuid4()), run_id="run-1", customer_unique_id=imp.customer_unique_id, order_ids=[imp.order_id],
        first_name="Ana", locale="en", subject=render(subject, allow), body=render(body, allow), generator="template",
        model=None, prompt_version="template-v1", status="draft", qa={"passed": True, "checks": [], "generator": "template"},
        facts={"facts": facts.model_dump(), "allow": allow.model_dump(), "llm_feedback": []},
        max_severity=70.0, version=1, attempts=1,
    )


def run(batch, llm, session=None, **settings):
    session = session or FakeSession()
    s = get_settings().model_copy(update=settings)
    return asyncio.run(upgrade_batch(session, llm, s, "run-1", batch)), session


def test_template_emails_become_checked_llm_drafts(engine_ctx, policy_ctx_factory):
    c = make_comm(engine_ctx, policy_ctx_factory, 1)
    llm = UpgradeLLM()
    (calls, upgraded, failed, error), session = run([c], llm)
    assert (upgraded, failed, error) == (1, 0, None) and calls == 2  # one email call and one tone-review call
    assert c.generator == "llm" and c.model == "fake-model" and c.status == "draft"
    assert c.version == 2 and c.attempts == 2
    assert c.qa["passed"] and c.qa["generator"] == "llm" and c.qa["tone"]["warmth"] == 5
    assert "[[" not in c.body and "https://shop.example.com" in c.body  # placeholders are rendered before storing
    assert [a.action for a in session.added] == ["communication.ai_upgrade"] and session.added[0].actor == "ai_upgrade"
    assert session.added[0].detail["before"]["generator"] == "template"


def test_a_draft_that_fails_the_fact_check_is_redrafted_once(engine_ctx, policy_ctx_factory):
    c = make_comm(engine_ctx, policy_ctx_factory, 1)
    llm = UpgradeLLM(corrupt=(1,))
    (calls, upgraded, failed, _), _ = run([c], llm)
    assert llm.prompts.count("email") == 2 and upgraded == 1 and failed == 0 and c.generator == "llm"


def test_an_email_that_keeps_failing_stays_a_template_and_is_not_retried(engine_ctx, policy_ctx_factory):
    c = make_comm(engine_ctx, policy_ctx_factory, 1)
    before = (c.subject, c.body)
    llm = UpgradeLLM(corrupt=("all",))
    (calls, upgraded, failed, error), _ = run([c], llm)
    assert llm.prompts.count("email") == 2 and (upgraded, failed, error) == (0, 1, None)  # bounded: one redraft
    assert c.generator == "template" and (c.subject, c.body) == before
    assert c.qa["ai_upgrade"] == FAILED  # the marker keeps it out of later passes
    assert "tone" not in llm.prompts  # nothing to review


def test_a_person_who_edits_meanwhile_is_never_overwritten(engine_ctx, policy_ctx_factory):
    c = make_comm(engine_ctx, policy_ctx_factory, 1)
    original = c.body

    def human_edits(obj):
        obj.generator, obj.version = "human", obj.version + 1  # arrives between drafting and saving

    (_, upgraded, failed, _), session = run([c], UpgradeLLM(), FakeSession(on_refresh=human_edits))
    assert upgraded == 0 and c.generator == "human" and c.body == original and session.added == []


def test_approved_emails_are_left_alone(engine_ctx, policy_ctx_factory):
    c = make_comm(engine_ctx, policy_ctx_factory, 1)

    def approved(obj):
        obj.status = "approved"

    (_, upgraded, _, _), _ = run([c], UpgradeLLM(), FakeSession(on_refresh=approved))
    assert upgraded == 0 and c.generator == "template"


def test_an_unavailable_llm_stops_without_marking_emails_as_failed(engine_ctx, policy_ctx_factory):
    c = make_comm(engine_ctx, policy_ctx_factory, 1)
    (calls, upgraded, failed, error), _ = run([c], UpgradeLLM(unavailable=True))
    assert isinstance(error, LLMUnavailable) and (calls, upgraded, failed) == (0, 0, 0)
    assert "ai_upgrade" not in c.qa and c.generator == "template"  # a later pass tries again


def test_tone_review_can_be_switched_off(engine_ctx, policy_ctx_factory):
    c = make_comm(engine_ctx, policy_ctx_factory, 1)
    llm = UpgradeLLM()
    (calls, upgraded, _, _), _ = run([c], llm, tone_review_enabled=False)
    assert upgraded == 1 and calls == 1 and "tone" not in llm.prompts and "tone" not in c.qa
