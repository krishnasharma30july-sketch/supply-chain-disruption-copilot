"""The assumptions are configuration: they must load, be complete, and a bad value must be refused when loaded."""

import copy

import pytest
from pydantic import ValidationError

from app.core.config import get_settings
from app.core.policy_config import (
    AssumptionsConfig,
    CategoryClassesConfig,
    PolicyConfig,
    ScoringConfig,
    _load_yaml,
    get_assumptions,
    get_category_classes,
    get_scoring,
)


def raw(name: str) -> dict:
    return copy.deepcopy(_load_yaml(get_settings().config_dir / name))


def test_the_register_is_complete_and_sequential():
    ids = [i.id for i in get_assumptions().items]
    assert ids == [f"A{n}" for n in range(1, 27)]
    assert all(i.statement.strip() and i.impact.strip() for i in get_assumptions().items)


def test_duplicate_assumption_ids_are_refused():
    cfg = raw("assumptions.yaml")
    cfg["items"].append(copy.deepcopy(cfg["items"][0]))
    with pytest.raises(ValidationError, match="unique"):
        AssumptionsConfig.model_validate(cfg)


@pytest.mark.parametrize("field,value,message", [
    ("volumetric_divisor", 0, "greater than 0"),
    ("lane_min_samples", 0, "greater than 0"),
    ("recovery_factor", 1.5, "share"),
    ("origin_dwell_days", -1, "negative"),
    ("parcel_max_kg", 40, "below medium_max_kg"),
])
def test_assumption_parameters_are_checked(field, value, message):
    cfg = raw("assumptions.yaml")
    cfg["params"][field] = value
    with pytest.raises(ValidationError, match=message):
        AssumptionsConfig.model_validate(cfg)


@pytest.mark.parametrize("edit,message", [
    (lambda c: c.update(breach_days_cap=0), "greater than 0"),
    (lambda c: c.update(seller_late_rate_cap=0), "greater than 0"),
    (lambda c: c.update(default_seller_late_rate=1.5), "between 0 and 1"),
    (lambda c: c["weight_tiers"].reverse(), "ascending"),
    (lambda c: c["weight_tiers"][0].update(value=2), "between 0 and 1"),
    (lambda c: c["tiers"].update(high=90), "critical > high > medium"),
    (lambda c: c.update(complexity={"multi_seller": 1.4}), "complexity"),
    (lambda c: c["weights"].update(sla_breach=40), "sum to 100"),
])
def test_scoring_values_are_checked(edit, message):
    cfg = raw("scoring.yaml")
    edit(cfg)
    with pytest.raises(ValidationError, match=message):
        ScoringConfig.model_validate(cfg)


@pytest.mark.parametrize("edit,message", [
    (lambda c: c["compensation_tiers"][1].update(min_days=5), "contiguous"),
    (lambda c: c["compensation_tiers"][-1].update(max_days=30), "open-ended"),
    (lambda c: c["compensation_tiers"][0].update(min_days=0), "start at 1"),
    (lambda c: c["compensation_tiers"][1].update(voucher_pct=150), "voucher_pct"),
    (lambda c: c["compensation_tiers"][1].update(voucher_cap_brl=-1), "voucher_pct"),
    (lambda c: c.update(monitor_max_p_breach=1.5), "between 0 and 1"),
    (lambda c: c["alt_route"].update(freight_uplift=-0.1), "negative"),
    (lambda c: c.update(general_offer_cancel_days_late=-1), "negative"),
])
def test_policy_values_are_checked(edit, message):
    cfg = raw("policy.yaml")
    edit(cfg)
    with pytest.raises(ValidationError, match=message):
        PolicyConfig.model_validate(cfg)


def test_values_that_used_to_be_hard_coded_are_now_configuration():
    sc = get_scoring()
    assert sc.default_seller_late_rate == 0.08
    assert (sc.complexity.multi_seller, sc.complexity.mixed_impact, sc.complexity.multi_item, sc.complexity.single_item) == (1.0, 0.7, 0.4, 0.0)
    cc = get_category_classes()
    assert cc.confidence.missing_category == 0.3 and cc.confidence.unlisted_category == 0.9
    assert cc.class_of(None) == (cc.default_class, 0.3)
    assert cc.class_of("a_category_nobody_listed")[1] == 0.9


def test_class_confidence_comes_from_the_config():
    cfg = raw("category_classes.yaml")
    cfg["confidence"] = {"missing_category": 0.1, "unlisted_category": 0.5}
    cc = CategoryClassesConfig.model_validate(cfg)
    assert cc.class_of(None)[1] == 0.1 and cc.class_of("a_category_nobody_listed")[1] == 0.5
