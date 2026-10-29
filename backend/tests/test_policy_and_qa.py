from datetime import datetime

import pytest

from app.core.config import get_settings
from app.domain.models import GeoScope
from app.llm.schema import to_gemini_schema
from app.services.comms import build_customer_facts, template_email
from app.services.fact_check import parse_amount
from app.services.impact import order_impact
from app.services.policy_engine import RULE_REFS, decide_order
from app.services.qa import check_email
from app.services.retrieval.kb import kb_index
from app.services.scope import ConceptFilter, match_line
from app.services.spec_builder import RawIntake
from tests.conftest import alt_seller, make_line, sp_spec


def decide(line, engine_ctx, policy_ctx_factory, spec=None, alt=None):
    spec = spec or sp_spec()
    m = match_line(spec, line, ConceptFilter())
    imp = order_impact([line], {line.order_item_id: m} if m else {}, spec, engine_ctx)
    return imp, decide_order(imp, policy_ctx_factory(spec, alt))


def test_bulky_is_delayed_with_tiered_compensation(engine_ctx, policy_ctx_factory):
    line = make_line(handling_profile="BULKY", billable_weight_kg=35.0, sla_ts=datetime(2018, 8, 8))
    imp, dec = decide(line, engine_ctx, policy_ctx_factory)
    assert imp.days_late_calendar == 4
    assert dec.action == "DELAY_WITH_COMPENSATION" and dec.rule_id == "R5"
    assert dec.compensation.tier_label == "Moderate delay"
    assert dec.compensation.freight_refund_brl == 20.0
    assert dec.compensation.voucher_brl == 10.0  # 10% of R$ 100, under the R$ 50 cap
    assert dec.compensation.refund_total_brl == 20.0
    assert dec.offer_cancellation is False
    assert "mitigation_playbook#bulky-and-heavy-freight" in dec.policy_refs


def test_perishable_is_cancelled_and_refunded(engine_ctx, policy_ctx_factory):
    line = make_line(product_class="PERISHABLE", category_en="food", sla_ts=datetime(2018, 8, 9))
    _, dec = decide(line, engine_ctx, policy_ctx_factory)
    assert dec.action == "CANCEL_REFUND" and dec.rule_id == "R2"
    assert dec.compensation.refund_total_brl == 120.0 and dec.compensation.voucher_brl == 0


def test_parcel_is_rerouted_via_alternate_route(engine_ctx, policy_ctx_factory):
    line = make_line(handling_profile="PARCEL", billable_weight_kg=2.0, sla_ts=datetime(2018, 8, 8))
    _, dec = decide(line, engine_ctx, policy_ctx_factory)
    assert dec.action == "REROUTE_ALT_ROUTE" and dec.rule_id == "R4"
    assert dec.resolution_eta == datetime(2018, 8, 6)  # depart now + p50 4d + 1 extra day
    assert dec.resolution_days_late == 0 and dec.compensation.freight_refund_brl == 0
    assert dec.est_cost_brl == pytest.approx(12.0)  # +60% of R$ 20 freight


def test_nationwide_blocks_alternate_route(engine_ctx, policy_ctx_factory):
    spec = sp_spec(nationwide=True, origin=GeoScope())
    line = make_line(handling_profile="PARCEL", billable_weight_kg=2.0, sla_ts=datetime(2018, 8, 8))
    _, dec = decide(line, engine_ctx, policy_ctx_factory, spec=spec)
    assert dec.action == "DELAY_WITH_COMPENSATION"
    assert any(a.kind == "alt_route" and not a.accepted for a in dec.alternatives)


def test_alternate_seller_outside_scope(engine_ctx, policy_ctx_factory):
    line = make_line(sla_ts=datetime(2018, 8, 8))
    _, dec = decide(line, engine_ctx, policy_ctx_factory, alt={"p" * 32: [alt_seller(105.0)]})
    assert dec.action == "REROUTE_ALT_SELLER" and dec.rule_id == "R3"
    assert dec.resolution_eta == datetime(2018, 8, 9)  # handover Aug 3 + national p50 6 days
    assert dec.resolution_days_late == 1
    assert dec.compensation.freight_refund_brl == 20.0 and dec.compensation.voucher_brl == 0
    assert dec.est_cost_brl == pytest.approx(25.0)  # freight refund + R$ 5 price difference


def test_alternate_seller_too_expensive_is_rejected(engine_ctx, policy_ctx_factory):
    line = make_line(sla_ts=datetime(2018, 8, 8))
    _, dec = decide(line, engine_ctx, policy_ctx_factory, alt={"p" * 32: [alt_seller(150.0)]})
    assert dec.action == "DELAY_WITH_COMPENSATION"
    assert dec.alternatives and not dec.alternatives[0].accepted


def test_on_time_shift_only_notifies(engine_ctx, policy_ctx_factory):
    _, dec = decide(make_line(sla_ts=datetime(2018, 8, 20)), engine_ctx, policy_ctx_factory)
    assert dec.action == "NOTIFY_ONLY" and dec.compensation.refund_total_brl == 0


def test_seller_outage_never_reroutes_via_alternate_carrier(engine_ctx, policy_ctx_factory):
    spec = sp_spec(disruption_type="supplier", legs=["first_mile"])
    line = make_line(handling_profile="PARCEL", billable_weight_kg=2.0, sla_ts=datetime(2018, 8, 8))
    _, dec = decide(line, engine_ctx, policy_ctx_factory, spec=spec)
    assert dec.action == "DELAY_WITH_COMPENSATION"
    assert any(a.kind == "alt_route" and not a.accepted and "seller" in a.reason for a in dec.alternatives)


def test_small_on_time_shift_is_monitored_silently(engine_ctx, policy_ctx_factory):
    spec = sp_spec(explicit_delay_days=1, recovery_days=0)
    _, dec = decide(make_line(sla_ts=datetime(2018, 8, 20)), engine_ctx, policy_ctx_factory, spec=spec)
    assert dec.action == "MONITOR"  # 1-day shift, still on time, no breach risk: no email


def test_pt_br_category_labels():
    from app.services.formatting import pretty_category_pt

    assert pretty_category_pt("moveis_decoracao") == "Móveis Decoração"
    assert pretty_category_pt("eletrodomesticos_2") == "Eletrodomésticos"
    assert pretty_category_pt("cama_mesa_banho") == "Cama Mesa Banho"
    assert pretty_category_pt("moveis_cozinha_area_de_servico_jantar_e_jardim").startswith("Móveis Cozinha Área de Serviço")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("We expect it by the 15th.", "Ordinal date"),
        ("It should arrive within five business days.", "Duration not in the facts"),
        ("Expect delivery next Tuesday.", "Relative date"),
        ("Chega amanhã.", "Relative date"),
    ],
)
def test_fact_checker_catches_indirect_dates_and_durations(text, expected):
    from app.services.fact_check import scan

    problems = scan(text, allowed_dates=[], allowed_money=[], allowed_percents=[], allowed_numbers=[1.0, 90.0])
    assert any(expected in p for p in problems), problems


def test_fact_checker_allows_grounded_durations_and_24_7():
    from app.services.fact_check import scan

    text = "The voucher is valid for ninety days. Support is available 24/7. It is one of your orders."
    assert scan(text, allowed_dates=[], allowed_money=[], allowed_percents=[], allowed_numbers=[1.0, 90.0]) == []
    assert scan("Valid for 90 days.", allowed_dates=[], allowed_money=[], allowed_percents=[], allowed_numbers=[90.0]) == []


def test_every_rule_cites_an_existing_kb_section():
    kb = kb_index(get_settings().kb_dir)
    missing = [ref for refs in RULE_REFS.values() for ref in refs if ref not in kb]
    assert missing == []


# --------------------------------------------------------------------------- QA critic


@pytest.fixture
def bulky_email(engine_ctx, policy_ctx_factory):
    line = make_line(handling_profile="BULKY", billable_weight_kg=35.0, sla_ts=datetime(2018, 8, 8))
    imp, dec = decide(line, engine_ctx, policy_ctx_factory)
    facts, allow = build_customer_facts(imp.customer_unique_id, "Ana", [(imp, dec)], sp_spec(), "en", "run-1")
    subject, body = template_email(facts)
    return facts, allow, subject, body


def test_template_email_passes_qa(bulky_email):
    facts, allow, subject, body = bulky_email
    report = check_email(subject, body, facts, allow)
    assert report.passed, report.failures()


def test_pt_br_template_passes_qa(engine_ctx, policy_ctx_factory):
    line = make_line(handling_profile="BULKY", billable_weight_kg=35.0, sla_ts=datetime(2018, 8, 8))
    imp, dec = decide(line, engine_ctx, policy_ctx_factory)
    facts, allow = build_customer_facts(imp.customer_unique_id, "Ana", [(imp, dec)], sp_spec(), "pt-BR", "run-1")
    subject, body = template_email(facts)
    report = check_email(subject, body, facts, allow)
    assert report.passed, report.failures()


@pytest.mark.parametrize(
    "mutation,failed_check",
    [
        (lambda b: b.replace("R$ 10.00", "R$ 99.99"), "facts_grounded"),
        (lambda b: b + "\nSee https://evil.example.com now.", "no_raw_links"),
        (lambda b: b + "\nYour severity score is high.", "no_internal_jargon"),
        (lambda b: b + "\nWe guarantee delivery tomorrow.", "no_guarantees"),
        (lambda b: b.replace("Aug 12, 2018", "Aug 30, 2018"), "facts_grounded"),
        (lambda b: b.replace("sorry", "writing"), "required_content"),
    ],
)
def test_qa_catches_ungrounded_or_noncompliant_text(bulky_email, mutation, failed_check):
    facts, allow, subject, body = bulky_email
    report = check_email(subject, mutation(body), facts, allow)
    assert not report.passed
    assert failed_check in {c.name for c in report.checks if not c.passed}


@pytest.mark.parametrize("locale,phrase", [("en", "already running behind"), ("pt-BR", "já estava atrasado")])
def test_email_acknowledges_orders_already_late_before_the_disruption(engine_ctx, policy_ctx_factory, locale, phrase):
    line = make_line(sla_ts=datetime(2018, 7, 5))  # promise missed weeks before the Aug 1 disruption
    imp, dec = decide(line, engine_ctx, policy_ctx_factory)
    facts, allow = build_customer_facts(imp.customer_unique_id, "Ana", [(imp, dec)], sp_spec(), locale, "run-1")
    assert facts.orders[0].already_late_before_disruption
    subject, body = template_email(facts)
    assert phrase in body
    assert check_email(subject, body, facts, allow).passed, check_email(subject, body, facts, allow).failures()


def test_email_for_on_time_order_blames_only_the_disruption(engine_ctx, policy_ctx_factory):
    imp, dec = decide(make_line(sla_ts=datetime(2018, 8, 8)), engine_ctx, policy_ctx_factory)
    facts, _ = build_customer_facts(imp.customer_unique_id, "Ana", [(imp, dec)], sp_spec(), "en", "run-1")
    assert not facts.orders[0].already_late_before_disruption
    assert "already running behind" not in template_email(facts)[1]


def test_voucher_mention_without_voucher_fails(engine_ctx, policy_ctx_factory):
    imp, dec = decide(make_line(sla_ts=datetime(2018, 8, 20)), engine_ctx, policy_ctx_factory)
    facts, allow = build_customer_facts(imp.customer_unique_id, "Ana", [(imp, dec)], sp_spec(), "en", "run-1")
    subject, body = template_email(facts)
    assert check_email(subject, body, facts, allow).passed
    report = check_email(subject, body + "\nHere is a voucher for you.", facts, allow)
    assert "policy_voucher" in {c.name for c in report.checks if not c.passed}


def test_numeric_category_suffix_is_never_shown_or_flagged(engine_ctx, policy_ctx_factory):
    from app.services.formatting import pretty_category

    assert pretty_category("home_appliances_2") == "Home Appliances"
    line = make_line(category_en="home_appliances_2", sla_ts=datetime(2018, 8, 20))
    imp, dec = decide(line, engine_ctx, policy_ctx_factory)
    facts, allow = build_customer_facts(imp.customer_unique_id, "Ana", [(imp, dec)], sp_spec(), "en", "run-1")
    subject, body = template_email(facts)
    assert "Appliances 2" not in body
    assert check_email(subject, body, facts, allow).passed


@pytest.mark.parametrize(
    "raw,value", [("1,234.56", 1234.56), ("1.234,56", 1234.56), ("42.10", 42.10), ("42,10", 42.10), ("1500", 1500.0)]
)
def test_parse_amount(raw, value):
    assert parse_amount(raw) == pytest.approx(value)


def test_gemini_schema_conversion():
    schema = to_gemini_schema(RawIntake)
    assert schema["type"] == "OBJECT"
    assert set(schema["required"]) == set(RawIntake.model_fields)
    legs = schema["properties"]["legs"]
    assert legs["type"] == "ARRAY" and set(legs["items"]["enum"]) == {"first_mile", "linehaul", "last_mile"}
    assert "weather" in schema["properties"]["disruption_type"]["enum"]


def test_qa_summary_is_plain_language():
    from app.agents.qa_critic import _summary

    assert _summary(241, 241, 0, 0, 0) == "All 241 emails passed the fact check. Every AI-written email was correct on the first try."
    assert _summary(241, 241, 1, 1, 0) == (
        "All 241 emails passed the fact check. 1 AI-written email needed a correction and was fixed by the AI on rewrite.")
    assert _summary(50, 50, 3, 2, 1) == (
        "All 50 emails passed the fact check. 3 AI-written emails needed a correction: 2 fixed by the AI on rewrite, "
        "1 replaced with a verified template because the rewrite still failed.")
    assert _summary(71, 71, 0, 0, 0, ai_written=0) == (
        "All 71 emails passed the fact check. All were written from verified templates (no AI drafting in this run).")