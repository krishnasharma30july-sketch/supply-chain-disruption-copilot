"""Seller evidence: scenario-aware query and the exposure-blended ranking."""

from datetime import datetime
from types import SimpleNamespace

from app.domain.models import CityRef, DisruptionSpec, GeoScope
from app.services.retrieval.hybrid import SearchHit
from app.services.retrieval.seller_evidence import blend_sellers, exposure_by_seller, seller_query


def spec(**kw) -> DisruptionSpec:
    base = dict(title="Disruption", legs=["last_mile"], start_at=datetime(2018, 8, 1), duration_days=3)
    return DisruptionSpec(**{**base, **kw})


def hit(ref: str) -> SearchHit:
    return SearchHit(doc_id=f"seller:{ref}", doc_type="seller", ref_id=ref, title=ref, snippet="", metadata={},
                     score=1.0, dense_rank=1, sparse_rank=1)


def test_destination_side_scenario_asks_where_parcels_go():
    q = seller_query(spec(destination=GeoScope(states=["RJ"])))
    assert q == "Disruption. Parcels in the pipeline now go to Rio de Janeiro (RJ)"


def test_origin_side_scenario_asks_where_the_seller_is_and_product_terms_are_kept():
    q = seller_query(spec(legs=["first_mile", "linehaul"], origin=GeoScope(states=["SP"]), category_terms=["furniture"]))
    assert q == "Disruption. Sells furniture"  # the title already names the origin; adding words made search worse


def test_lane_scenario_uses_both_ends_and_cities_use_their_label():
    q = seller_query(spec(legs=["linehaul"], match_mode="lane", origin=GeoScope(states=["SP"]),
                          destination=GeoScope(cities=[CityRef(name="salvador", state="BA", label="Salvador, BA")])))
    assert "Parcels in the pipeline now go to Salvador, BA" in q and "Seller located in Sao Paulo (SP)" in q


def test_query_falls_back_to_the_title():
    assert seller_query(spec(nationwide=True, legs=["linehaul"])) == "Disruption. Sellers shipping parcels all over Brazil"
    assert seller_query(spec(legs=["linehaul"], seller_ids=["s1"], title="Supplier outage")) == "Supplier outage"  # no geography: just the title


def _matched():
    line = lambda order, seller, price, freight: SimpleNamespace(line=SimpleNamespace(  # noqa: E731
        order_id=order, seller_id=seller, price=price, freight=freight))
    return {"o1": [SimpleNamespace(**vars(line("o1", "A", 100, 10)))], "o2": [SimpleNamespace(**vars(line("o2", "A", 50, 5)))],
            "o3": [SimpleNamespace(**vars(line("o3", "B", 500, 20)))]}


def test_exposure_counts_distinct_orders_items_and_value():
    ex = exposure_by_seller(_matched())
    assert (ex["A"].orders, ex["A"].items, ex["A"].value_brl) == (2, 2, 165.0)
    assert (ex["B"].orders, ex["B"].value_brl) == (1, 520.0)


def test_blend_weight_zero_keeps_search_order_and_weight_one_orders_by_exposure():
    ex = exposure_by_seller(_matched())
    hits = [hit("C"), hit("B"), hit("A")]  # search prefers C (not exposed at all), then B, then A
    assert [h.ref_id for h, _, _ in blend_sellers(hits, ex, 0.0, 3)] == ["C", "B", "A"]
    assert [h.ref_id for h, _, _ in blend_sellers(hits, ex, 1.0, 3)] == ["A", "B", "C"]  # most affected orders first
    mixed = [h.ref_id for h, _, _ in blend_sellers(hits, ex, 0.6, 2)]
    assert mixed[0] in ("A", "B") and "C" not in mixed  # an unexposed seller does not outrank exposed ones
    assert blend_sellers([], ex, 0.6, 5) == []


def test_the_candidate_pool_covers_every_impacted_seller():
    """Regression: with the default search depth (200) a scope of 491 sellers lost about half of its candidates."""
    import asyncio
    from unittest.mock import patch

    from app.services.retrieval import seller_evidence

    seen = {}

    async def fake(session, embedder, query, filters, **kw):
        seen.update(kw, ids=filters.ref_ids)
        return []

    ids = [f"s{i}" for i in range(491)]
    with patch.object(seller_evidence, "hybrid_search", fake):
        asyncio.run(seller_evidence.retrieve_sellers(None, None, "q", ids))
    assert seen["limit"] == 491 and seen["k"] == 491 and len(seen["ids"]) == 491
    assert asyncio.run(seller_evidence.retrieve_sellers(None, None, "q", [])) == []
