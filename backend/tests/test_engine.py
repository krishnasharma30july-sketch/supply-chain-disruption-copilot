from datetime import datetime

import pytest

from app.domain.models import GeoScope
from app.services.eta import cdf, prob_exceeds
from app.services.impact import order_impact
from app.services.scope import ConceptFilter, match_line
from app.services.scoring import score_order
from tests.conftest import AS_OF, make_line, sp_spec


def impact_for(line, spec, ctx):
    m = match_line(spec, line, ConceptFilter())
    return order_impact([line], {line.order_item_id: m} if m else {}, spec, ctx)


def test_cdf_is_monotonic_and_bounded(lanes):
    lane = lanes.get("SP", "MG")
    values = [cdf(lane, x / 2) for x in range(-2, 22)]
    assert values == sorted(values)
    assert values[0] == 0.0 and values[-1] == 1.0
    assert cdf(lane, 4.0) == pytest.approx(0.5)
    assert prob_exceeds(lane, 0) == 1.0 and prob_exceeds(lane, 100) == 0.0


def test_lane_fallback_to_national(lanes):
    assert lanes.get("RJ", "AM").level == "national"


def test_first_mile_hold_with_recovery(engine_ctx):
    imp = impact_for(make_line(), sp_spec(), engine_ctx)
    li = imp.lines[0]
    assert li.handover_expected == AS_OF  # approved + handling is already past -> hand over now
    assert li.baseline_eta == datetime(2018, 8, 5)
    assert {h.leg for h in li.legs} == {"first_mile", "linehaul"}
    assert li.hold_days == pytest.approx(7.0)  # 5 days stuck at origin + 2 recovery
    assert imp.projected_eta == datetime(2018, 8, 12)
    assert imp.days_late_calendar == 0  # promise is Aug 14
    assert li.p_breach == pytest.approx(0.25)  # P(transit > 13 - 7 = 6 days) on Uniform(0, 8)
    assert li.p_breach_baseline == 0.0


def test_handover_before_window_is_not_affected(engine_ctx):
    spec = sp_spec(legs=["first_mile"], start_at=datetime(2018, 8, 3))
    imp = impact_for(make_line(), spec, engine_ctx)
    assert not imp.affected and imp.hold_days == 0


def test_in_transit_within_dwell_is_held(engine_ctx):
    line = make_line(stage="in_transit", carrier_ts=datetime(2018, 7, 31, 12))
    imp = impact_for(line, sp_spec(), engine_ctx)
    assert imp.lines[0].legs[0].leg == "first_mile"
    assert imp.hold_days == pytest.approx(7.0)
    assert imp.projected_eta == datetime(2018, 8, 11, 12)


def test_in_transit_already_left_region_is_not_held(engine_ctx):
    line = make_line(stage="in_transit", carrier_ts=datetime(2018, 7, 29))
    imp = impact_for(line, sp_spec(), engine_ctx)
    assert not imp.affected


def test_regional_disruption_does_not_hold_freight_that_already_left(engine_ctx):
    # Handed over 1.5 days before the rain: past the 1-day dwell, so it is outside Sao Paulo (review fix #1).
    line = make_line(stage="in_transit", carrier_ts=datetime(2018, 7, 30, 12))
    imp = impact_for(line, sp_spec(), engine_ctx)
    assert not imp.affected


def test_corridor_disruption_holds_the_whole_linehaul(engine_ctx):
    line = make_line(stage="in_transit", carrier_ts=datetime(2018, 7, 30, 12))
    spec = sp_spec(nationwide=True, origin=GeoScope())
    imp = impact_for(line, spec, engine_ctx)
    li = imp.lines[0]
    assert [h.leg for h in li.legs] == ["linehaul"]
    assert li.legs[0].hold_days == pytest.approx(1.5)  # on the road Aug 1 -> Aug 2 12:00 inside the window


def test_destination_side_linehaul_exposure(engine_ctx):
    spec = sp_spec(legs=["linehaul", "last_mile"], origin=GeoScope(), destination=GeoScope(states=["MG"]))
    imp = impact_for(make_line(), spec, engine_ctx)
    legs = {h.leg: h.hold_days for h in imp.lines[0].legs}
    assert legs["linehaul"] == pytest.approx(3.0)  # enters MG on Aug 3, waits until Aug 6
    assert legs["last_mile"] == pytest.approx(2.0)
    assert imp.projected_eta == datetime(2018, 8, 10)  # max hold 3 + 2 recovery


def test_partial_impact_factor_scales_the_hold(engine_ctx):
    from dataclasses import replace

    ctx = replace(engine_ctx, params=engine_ctx.params.model_copy(update={"partial_impact_factor": 0.5}))
    imp = impact_for(make_line(), sp_spec(), ctx)
    assert imp.hold_days == pytest.approx(4.5)  # 5 * 0.5 + 2 recovery


def test_breach_probability_conditions_on_time_already_in_transit(lanes):
    from app.services.eta import prob_exceeds_given

    lane = lanes.get("SP", "MG")  # Uniform(0, 8)
    assert prob_exceeds_given(lane, 6, 0) == pytest.approx(0.25)
    assert prob_exceeds_given(lane, 6, 4) == pytest.approx(0.5)  # (1 - 0.75) / (1 - 0.5)
    assert prob_exceeds_given(lane, 3, 4) == 1.0  # time ran out while still travelling


def test_last_mile_hold(engine_ctx):
    spec = sp_spec(legs=["last_mile"], origin=GeoScope(), destination=GeoScope(states=["MG"]))
    imp = impact_for(make_line(), spec, engine_ctx)
    assert imp.lines[0].legs[0].leg == "last_mile"
    assert imp.hold_days == pytest.approx(4.0)  # waits Aug 4 -> Aug 6, + 2 recovery
    assert imp.projected_eta == datetime(2018, 8, 9)


def test_explicit_delay_overrides_window(engine_ctx):
    imp = impact_for(make_line(), sp_spec(explicit_delay_days=3, recovery_days=0), engine_ctx)
    assert imp.hold_days == pytest.approx(3.0)
    assert imp.projected_eta == datetime(2018, 8, 8)


def test_out_of_scope_line_does_not_match(engine_ctx):
    line = make_line(seller_state="RJ", seller_city="rio de janeiro")
    assert match_line(sp_spec(), line, ConceptFilter()) is None


def test_concept_filter_heavy_bulky():
    flt = ConceptFilter(heavy_bulky=True, heavy_min_kg=15)
    assert flt.match(make_line(handling_profile="BULKY", billable_weight_kg=3)) == "HEAVY_BULKY"
    assert flt.match(make_line(billable_weight_kg=16)) == "HEAVY_BULKY"
    assert flt.match(make_line(billable_weight_kg=2, handling_profile="PARCEL")) is None


def test_lane_mode_requires_both_ends(engine_ctx):
    spec = sp_spec(match_mode="lane", destination=GeoScope(states=["BA"]), legs=["first_mile", "linehaul", "last_mile"])
    assert match_line(spec, make_line(), ConceptFilter()) is None  # SP -> MG is not the SP -> BA lane
    assert match_line(spec, make_line(cust_state="BA", cust_city="salvador"), ConceptFilter()) is not None


def test_severity_increases_with_lateness(engine_ctx, scoring):
    on_time = impact_for(make_line(sla_ts=datetime(2018, 8, 20)), sp_spec(), engine_ctx)
    late = impact_for(make_line(sla_ts=datetime(2018, 8, 8)), sp_spec(), engine_ctx)
    s1, _, _ = score_order(on_time, [50.0, 120.0, 500.0], engine_ctx.seller_stats, scoring)
    s2, tier2, comps = score_order(late, [50.0, 120.0, 500.0], engine_ctx.seller_stats, scoring)
    assert 0 <= s1 < s2 <= 100
    assert abs(sum(c.points for c in comps) - s2) < 0.2
    assert tier2 in ("critical", "high", "medium", "low")


def test_unaffected_order_scores_zero(engine_ctx, scoring):
    imp = impact_for(make_line(seller_state="RJ"), sp_spec(), engine_ctx)
    score, tier, _ = score_order(imp, [100.0], engine_ctx.seller_stats, scoring)
    assert score == 0 and tier == "low"
