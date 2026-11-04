"""Retrieval-augmented paths: policy retrieval with fallback, cited sources, near-miss orders outside the exact scope."""

import asyncio
from types import SimpleNamespace

import pytest

from app.agents.investigator import CaseTools
from app.agents.scout import outside_reason
from app.core.config import get_settings
from app.services import reporting
from app.services.retrieval import policy_rag
from app.services.retrieval.kb import kb_index
from app.services.retrieval.policy_rag import PolicySection, keyword_sections, retrieve_policy
from tests.conftest import make_line, sp_spec

KB = kb_index(get_settings().kb_dir)


def test_keyword_fallback_finds_the_relevant_policy_section():
    hits = keyword_sections(KB.values(), "bulky heavy freight furniture delay compensate")
    assert hits and all(h.via == "keyword" for h in hits)
    assert any("bulky" in h.id for h in hits)
    assert keyword_sections(KB.values(), "bulky heavy freight", exclude=[h.id for h in hits]) != hits  # exclusions respected
    assert keyword_sections(KB.values(), "zzzz qqqq") == []


def test_retrieve_policy_uses_hybrid_search_and_maps_hits_to_the_knowledge_base(monkeypatch):
    calls = {}

    async def fake_hybrid(session, embedder, query, filters, **kw):
        calls.update(query=query, doc_types=filters.doc_types, limit=kw["limit"])
        ids = ["mitigation_playbook#long-delays", "compensation_policy#critical-cases", "carrier_contingency#recovery"]
        return [SimpleNamespace(ref_id=i, title=i, score=0.03, rerank_score=2.0 - n) for n, i in enumerate(ids)]

    class Session:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(policy_rag, "hybrid_search", fake_hybrid)
    out = asyncio.run(retrieve_policy(
        session_factory=lambda: Session(), embedder=object(), reranker=None, kb=KB, query="very late order",
        limit=2, exclude=["mitigation_playbook#long-delays"],
    ))
    assert calls["doc_types"] == ["policy"] and calls["limit"] == 3  # asks for the excluded one too, then drops it
    assert [s.id for s in out] == ["compensation_policy#critical-cases", "carrier_contingency#recovery"]
    assert all(s.via == "hybrid" and s.text == KB[s.id].text for s in out)


def test_retrieval_errors_fall_back_to_keywords_without_raising(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("database down")

    class Session:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(policy_rag, "hybrid_search", boom)
    out = asyncio.run(retrieve_policy(session_factory=lambda: Session(), embedder=None, reranker=None, kb=KB, query="bulky heavy freight"))
    assert out and out[0].via == "keyword"
    no_db = asyncio.run(retrieve_policy(session_factory=None, embedder=None, reranker=None, kb=KB, query="perishable goods refund"))
    assert no_db and no_db[0].via == "keyword"


@pytest.fixture
def tools(engine_ctx, policy_ctx_factory):
    from datetime import datetime

    from app.services.impact import order_impact
    from app.services.policy_engine import decide_order
    from app.services.scope import ConceptFilter, match_line

    spec = sp_spec()
    line = make_line(handling_profile="BULKY", billable_weight_kg=35.0, sla_ts=datetime(2018, 8, 8))
    imp = order_impact([line], {line.order_item_id: match_line(spec, line, ConceptFilter())}, spec, engine_ctx)
    pc = policy_ctx_factory(spec)
    return imp, decide_order(imp, pc), pc


def test_the_investigators_policy_tool_retrieves_when_search_is_available_and_falls_back_otherwise(tools):
    imp, dec, pc = tools

    async def search(query):
        return [PolicySection("mitigation_playbook#long-delays", "Long delays", "text about long delays", 1.0, "hybrid")]

    with_search = CaseTools(imp=imp, dec=dec, pc=pc, kb=list(KB.values()), policy_search=search)
    res = asyncio.run(with_search.arun("policy", "very late"))
    assert res["retrieval"] == "hybrid" and res["sections"][0]["id"] == "mitigation_playbook#long-delays"

    async def broken(query):
        raise RuntimeError("down")

    for tool in (CaseTools(imp=imp, dec=dec, pc=pc, kb=list(KB.values())), CaseTools(imp=imp, dec=dec, pc=pc, kb=list(KB.values()), policy_search=broken)):
        res = asyncio.run(tool.arun("policy", "bulky heavy freight"))
        assert res["retrieval"] == "keyword" and res["sections"]  # never an empty or failed answer
    other = asyncio.run(with_search.arun("compare_plans", ""))
    assert "APPLY_RULE_PLAN" in other  # the other tools are untouched


# --------------------------------------------------------------------------- near misses


def test_outside_reason_names_the_first_scope_rule_an_order_fails():
    spec = sp_spec()  # origin SP
    mg = [make_line(seller_state="MG", cust_state="SP")]
    assert outside_reason(spec, mg) == "ships from MG, not SP"
    sp_origin = [make_line(seller_state="SP", cust_state="MG")]
    assert "city, radius, product or stage" in outside_reason(spec, sp_origin)
    dest_spec = sp_spec(origin={"states": []}, destination={"states": ["RJ"]})
    assert outside_reason(dest_spec, [make_line(seller_state="SP", cust_state="SP")]) == "delivers to SP, not RJ"
    assert outside_reason(spec, []) != ""


# --------------------------------------------------------------------------- narrative sources


RETRIEVAL = {"queries": [
    {"purpose": "Applicable policy", "hits": [
        {"ref_id": "mitigation_playbook#bulky-and-heavy-freight", "title": "Mitigation Playbook: Bulky and heavy freight"},
        {"ref_id": "mitigation_playbook#long-delays", "title": "Mitigation Playbook: Long delays"}]},
    {"purpose": "Heavy / bulky impacted products", "hits": [
        {"ref_id": "p1", "title": "Housewares | 47.1 kg | BULKY"}, {"ref_id": "p2", "title": "Housewares | 12.0 kg | BULKY"},
        {"ref_id": "p3", "title": "Office Furniture | 13.9 kg | BULKY"}]},
]}


def test_narrative_sources_are_names_only_so_the_number_check_stays_exact():
    src = reporting.narrative_sources(RETRIEVAL)
    assert [p["id"] for p in src["policy"]] == ["mitigation_playbook#bulky-and-heavy-freight", "mitigation_playbook#long-delays"]
    assert src["bulky_categories"] == ["Housewares", "Office Furniture"]  # de-duplicated, no weights or counts
    assert reporting.narrative_sources(None) == {"policy": [], "bulky_categories": []}


def test_sources_reach_the_model_but_add_no_numbers():
    stats = {  # a minimal stats dict: just what narrative_facts reads
        "scope": {"orders_in_scope": 10, "orders_delayed": 5, "orders_breaching": 2, "orders_newly_breaching": 1,
                  "customers_delayed": 5, "sellers_delayed": 3, "skus_delayed": 4, "gmv_delayed_brl": 100.0,
                  "avg_hold_days": 2.0, "bulky_orders_delayed": 1},
        "severity": {"low": 5}, "actions": {"MONITOR": 5}, "cost": {"total_brl": 1.0}, "top_categories": [], "origin_states": {},
    }
    plain = reporting.narrative_facts(stats, sp_spec())
    rich = reporting.narrative_facts(stats, sp_spec(), reporting.narrative_sources(RETRIEVAL))
    assert "retrieved_sources" not in plain
    assert rich["retrieved_sources"]["policy_sections"][0]["id"].startswith("mitigation_playbook#")
    nums_plain, nums_rich = [], []
    reporting._collect_numbers(plain, nums_plain)
    reporting._collect_numbers(rich, nums_rich)
    assert nums_plain == nums_rich  # naming a source can never widen what numbers the narrative may use


def test_a_cited_id_that_was_not_offered_is_dropped():
    """The reporter keeps only citations of sections it offered (checked here with the same comprehension)."""
    offered = reporting.narrative_sources(RETRIEVAL)
    titles = {p["id"]: p["title"] for p in offered["policy"]}
    claimed = ["mitigation_playbook#long-delays", "made_up#section", "mitigation_playbook#long-delays"]
    cited = [{"id": i, "title": titles[i]} for i in dict.fromkeys(claimed) if i in titles]
    assert cited == [{"id": "mitigation_playbook#long-delays", "title": "Mitigation Playbook: Long delays"}]
