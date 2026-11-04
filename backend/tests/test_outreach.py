"""Outreach: call briefs and seller notices are grounded, checked, and fall back to templates."""

import asyncio
import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.agents.base import EmailDraft, RunState
from app.agents.outreach import OutreachAgent, critical_drafts
from app.core.config import get_settings
from app.domain.models import RunOptions
from app.llm.outputs import CallBriefBatchOut, SellerNoticeBatchOut
from app.orchestrator.events import EventEmitter
from app.services.comms import CustomerFacts, build_customer_facts
from app.services.impact import order_impact
from app.services.outreach import (
    SellerFacts,
    brief_body,
    build_seller_facts,
    check_seller_notice,
    template_brief,
    template_seller_notice,
)
from app.services.qa import check_brief
from app.services.scope import ConceptFilter, match_line
from tests.conftest import make_line, sp_spec
from tests.test_policy_and_qa import decide


def impact_for(engine_ctx, **kw):
    line = make_line(**kw)
    m = match_line(sp_spec(), line, ConceptFilter())
    return order_impact([line], {line.order_item_id: m}, sp_spec(), engine_ctx)


def customer(engine_ctx, policy_ctx_factory, n: int, locale="en", **kw):
    line = make_line(order_id=str(n) * 32, customer_unique_id=str(n) * 32, handling_profile="BULKY",
                     billable_weight_kg=35.0, sla_ts=datetime(2018, 8, 8), **kw)
    imp, dec = decide(line, engine_ctx, policy_ctx_factory)
    facts, allow = build_customer_facts(imp.customer_unique_id, "Ana", [(imp, dec)], sp_spec(), locale, "run-1")
    return imp, EmailDraft(customer_unique_id=imp.customer_unique_id, first_name="Ana", order_ids=[imp.order_id],
                           max_severity=60.0 + n, facts=facts, allow=allow)


# --------------------------------------------------------------------------- call briefs


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
def test_template_call_brief_passes_its_own_checks(engine_ctx, policy_ctx_factory, locale):
    _, d = customer(engine_ctx, policy_ctx_factory, 1, locale)
    brief = template_brief(d.facts)
    report = check_brief(brief_body(brief), d.facts, d.allow)
    assert report.passed, report.failures()
    assert d.facts.orders[0].order_ref in brief_body(brief) and d.facts.orders[0].new_window_start in brief_body(brief)


@pytest.mark.parametrize(
    "mutation,failed_check",
    [
        (lambda t: t + "\nOffer a credit of R$ 999.99.", "facts_grounded"),
        (lambda t: t + "\nSee https://example.com/x for details.", "no_raw_links"),
        (lambda t: t + "\nUse code [[VOUCHER_CODE]].", "no_placeholders"),
        (lambda t: t + "\nWe guarantee delivery.", "no_guarantees"),
        (lambda t: t + "\nThe severity is high.", "no_internal_jargon"),
    ],
)
def test_call_brief_checks_catch_bad_content(engine_ctx, policy_ctx_factory, mutation, failed_check):
    _, d = customer(engine_ctx, policy_ctx_factory, 1)
    report = check_brief(mutation(brief_body(template_brief(d.facts))), d.facts, d.allow)
    assert failed_check in {c.name for c in report.checks if not c.passed}


def test_the_avoid_list_may_name_forbidden_words_but_the_rest_may_not(engine_ctx, policy_ctx_factory):
    """Real model output tells the agent "do not guarantee the date" and "do not mention SLA"; that is the point of the
    section, so the word rules skip it. The same words anywhere else, and wrong figures anywhere, still fail."""
    from app.services.outreach import check_call_brief

    _, d = customer(engine_ctx, policy_ctx_factory, 1)
    brief = template_brief(d.facts)
    brief.avoid = ["Do not guarantee a delivery date.", "Do not mention the SLA, the linehaul leg or any severity score."]
    assert check_call_brief(brief, d.facts, d.allow).passed

    in_points = brief.model_copy(update={"talking_points": [*brief.talking_points, "We guarantee delivery despite the SLA."]})
    failed = {c.name for c in check_call_brief(in_points, d.facts, d.allow).checks if not c.passed}
    assert failed == {"no_guarantees", "no_internal_jargon"}

    wrong_date = brief.model_copy(update={"avoid": ["Do not promise delivery before August 15."]})
    assert "facts_grounded" in {c.name for c in check_call_brief(wrong_date, d.facts, d.allow).checks if not c.passed}


def test_call_brief_must_identify_every_order(engine_ctx, policy_ctx_factory):
    _, d = customer(engine_ctx, policy_ctx_factory, 1)
    text = brief_body(template_brief(d.facts)).replace(d.facts.orders[0].order_ref, "THE ORDER")
    assert "orders_identified" in {c.name for c in check_brief(text, d.facts, d.allow).checks if not c.passed}


def test_only_critical_customers_get_briefs(engine_ctx, policy_ctx_factory):
    imps, drafts = [], []
    for n in (1, 2, 3):
        imp, d = customer(engine_ctx, policy_ctx_factory, n)
        imp.severity_tier = "critical" if n != 2 else "high"
        imps.append(imp)
        drafts.append(d)
    state = RunState(raw_query=None, spec=sp_spec(), options=RunOptions())
    state.impacts, state.drafts = imps, drafts
    picked = critical_drafts(state, 10)
    assert [d.customer_unique_id for d in picked] == ["3" * 32, "1" * 32]  # severity order, the "high" one excluded
    assert len(critical_drafts(state, 1)) == 1 and critical_drafts(state, 0) == []


# --------------------------------------------------------------------------- seller notices


def test_sellers_are_ranked_by_affected_orders_with_exact_counts(engine_ctx):
    impacts = [
        impact_for(engine_ctx, order_id="a" * 32, seller_id="s1", seller_city="sao paulo"),
        impact_for(engine_ctx, order_id="b" * 32, seller_id="s1", seller_city="sao paulo"),
        impact_for(engine_ctx, order_id="c" * 32, seller_id="alt1", seller_city="campinas"),
    ]
    assert all(i.affected for i in impacts)
    out = build_seller_facts(impacts, sp_spec(), "en", limit=5)
    assert [f.seller_ref for f, _ in out] == ["s1", "alt1"]  # two affected orders outrank one
    top, allow = out[0]
    assert (top.orders_affected, top.awaiting_pickup, top.in_transit) == (2, 2, 0)
    assert top.value == "R$ 240.00" and top.location == "Sao Paulo/SP"  # (price 100 + freight 20) x 2 orders, as in the report
    assert top.window_start == "Aug 1, 2018" and 240.0 in allow.money and 2.0 in allow.numbers
    assert len(build_seller_facts(impacts, sp_spec(), "en", limit=1)) == 1


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
def test_template_seller_notice_passes_its_own_checks(engine_ctx, locale):
    impacts = [impact_for(engine_ctx, order_id=c * 32, seller_id="s1") for c in "ab"]
    facts, allow = build_seller_facts(impacts, sp_spec(), locale, 1)[0]
    notice = template_seller_notice(facts)
    report = check_seller_notice(notice.subject, notice.body, facts, allow)
    assert report.passed, report.failures()


@pytest.mark.parametrize(
    "mutation,failed_check",
    [
        (lambda b: b + "\nWe will also reimburse you R$ 50.00.", "no_compensation_promises"),
        (lambda b: b + "\nExpect a penalty if you miss it.", "no_compensation_promises"),
        (lambda b: b.replace("2 orders", "7 orders"), "facts_grounded"),
        (lambda b: b + "\nSee https://portal.example.com.", "no_raw_links"),
        (lambda b: b + "\nWe guarantee pickup.", "no_guarantees"),
        (lambda b: "Too short.", "length"),
    ],
)
def test_seller_notice_checks_catch_bad_content(engine_ctx, mutation, failed_check):
    impacts = [impact_for(engine_ctx, order_id=c * 32, seller_id="s1") for c in "ab"]
    facts, allow = build_seller_facts(impacts, sp_spec(), "en", 1)[0]
    notice = template_seller_notice(facts)
    report = check_seller_notice(notice.subject, mutation(notice.body), facts, allow)
    assert failed_check in {c.name for c in report.checks if not c.passed}


# --------------------------------------------------------------------------- the agent end to end


class OutreachLLM:
    """Writes valid drafts (the templates) unless told to corrupt them; counts calls per prompt."""

    def __init__(self, available=True, corrupt_calls=()):
        self.available = available
        self.corrupt_calls = set(corrupt_calls)  # e.g. {("call_brief", 1)}: corrupt the first call_brief response
        self.calls: dict[str, int] = {}

    async def generate(self, **kw):
        prompt = kw["prompt"]
        self.calls[prompt] = n = self.calls.get(prompt, 0) + 1
        bad = (prompt, n) in self.corrupt_calls or (prompt, "always") in self.corrupt_calls
        facts = json.loads(kw["context"]["facts_json"])
        if prompt == "call_brief":
            briefs = [template_brief(CustomerFacts.model_validate({**f, "allowed_placeholders": []})) for f in facts]
            for b in briefs:
                if bad:
                    b.situation += " We will credit you R$ 999.99."
            parsed = CallBriefBatchOut(briefs=briefs)
        else:
            notices = [template_seller_notice(SellerFacts.model_validate({**f, "locale": "en"})) for f in facts]
            for nt in notices:
                if bad:
                    nt.body += " We will reimburse any loss."
            parsed = SellerNoticeBatchOut(notices=notices)
        return SimpleNamespace(parsed=parsed, model="fake-model", prompt_version="v1", cached=False)


@pytest.fixture
def outreach_state(engine_ctx, policy_ctx_factory):
    state = RunState(raw_query=None, spec=sp_spec(), options=RunOptions())
    for n in (1, 2):
        imp, d = customer(engine_ctx, policy_ctx_factory, n)
        imp.severity_tier = "critical"
        state.impacts.append(imp)
        state.drafts.append(d)
    return state


def run_agent(state, llm, **settings):
    ctx = SimpleNamespace(
        settings=get_settings().model_copy(update=settings), llm=llm, emitter=EventEmitter("r", None), use_llm=True,
        run_id="r", session_factory=None,
    )
    asyncio.run(OutreachAgent().run(state, ctx))
    return state.metrics["outreach"], ctx.emitter.events


def test_valid_llm_drafts_are_used(outreach_state):
    llm = OutreachLLM()
    metrics, events = run_agent(outreach_state, llm)
    assert metrics["call_briefs"] == 2 and metrics["call_briefs_llm"] == 2
    assert metrics["seller_notices"] == 1 and metrics["seller_notices_llm"] == 1 and metrics["qa_failed"] == 0
    assert llm.calls == {"call_brief": 1, "seller_notice": 1}
    assert "Nothing is sent automatically" in events[-1]["message"]


def test_a_draft_that_fails_the_check_gets_one_redraft(outreach_state):
    llm = OutreachLLM(corrupt_calls={("call_brief", 1), ("seller_notice", 1)})
    metrics, _ = run_agent(outreach_state, llm)
    assert llm.calls == {"call_brief": 2, "seller_notice": 2}  # first draft rejected, the redraft accepted
    assert metrics["call_briefs_llm"] == 2 and metrics["seller_notices_llm"] == 1 and metrics["qa_failed"] == 0


def test_drafts_that_keep_failing_fall_back_to_verified_templates(outreach_state):
    llm = OutreachLLM(corrupt_calls={("call_brief", "always"), ("seller_notice", "always")})
    metrics, _ = run_agent(outreach_state, llm)
    assert llm.calls == {"call_brief": 2, "seller_notice": 2}  # bounded: one redraft, then the template
    assert metrics["call_briefs"] == 2 and metrics["call_briefs_llm"] == 0 and metrics["seller_notices_llm"] == 0
    assert metrics["qa_failed"] == 0  # the templates themselves pass the same checks


def test_without_the_llm_everything_comes_from_templates(outreach_state):
    llm = OutreachLLM(available=False)
    metrics, _ = run_agent(outreach_state, llm)
    assert llm.calls == {} and metrics["call_briefs"] == 2 and metrics["call_briefs_llm"] == 0
    assert metrics["seller_notices"] == 1 and metrics["qa_failed"] == 0


def test_limits_can_switch_the_drafts_off(outreach_state):
    metrics, events = run_agent(outreach_state, OutreachLLM(), max_call_briefs=0, max_seller_notices=0)
    assert metrics == {"call_briefs": 0, "seller_notices": 0}
    assert "No call briefs or seller notices" in events[-1]["message"]
