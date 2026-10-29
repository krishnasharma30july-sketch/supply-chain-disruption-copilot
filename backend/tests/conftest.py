from datetime import datetime

import pytest

from app.core.policy_config import get_assumptions, get_policy, get_scoring
from app.domain.models import AltSeller, DisruptionSpec, GeoScope, LaneStat, LineRecord, SellerStat
from app.services.eta import LaneBook
from app.services.geo import CityEntry, Gazetteer
from app.services.impact import EngineContext
from app.services.policy_engine import PolicyContext

AS_OF = datetime(2018, 8, 1)


def linear_quantiles(hi: float) -> list[float]:
    """Uniform(0, hi) transit distribution: quantile p = p * hi."""
    return [round(hi * i / 20, 4) for i in range(21)]


@pytest.fixture
def lanes() -> LaneBook:
    # SP->MG ~ Uniform(0, 8): p50 = 4.0, p90 = 7.2.  National ~ Uniform(0, 12): p50 = 6.0, p90 = 10.8.
    return LaneBook(
        [
            LaneStat("SP", "MG", 500, 4.0, 4.0, 7.2, linear_quantiles(8.0), "lane"),
            LaneStat("*", "*", 10000, 6.0, 6.0, 10.8, linear_quantiles(12.0), "national"),
        ],
        min_samples=30,
    )


@pytest.fixture
def params():
    return get_assumptions().params


@pytest.fixture
def engine_ctx(lanes, params) -> EngineContext:
    return EngineContext(
        as_of=AS_OF,
        lanes=lanes,
        seller_stats={"s1": SellerStat("s1", 100, 0.1, 0.1, 2.0), "alt1": SellerStat("alt1", 50, 0.05, 0.05, 2.0)},
        global_handling_days=2.0,
        global_approval_days=0.5,
        params=params,
    )


def make_line(**kw) -> LineRecord:
    base = dict(
        order_id="o" * 32, order_item_id=1, product_id="p" * 32, seller_id="s1", customer_id="c1",
        customer_unique_id="u" * 32, stage="awaiting_pickup", purchase_ts=datetime(2018, 7, 28),
        approved_ts=datetime(2018, 7, 28, 12), carrier_ts=None, sla_ts=datetime(2018, 8, 14),
        shipping_limit_ts=datetime(2018, 8, 2), price=100.0, freight=20.0, seller_city="sao paulo", seller_state="SP",
        seller_lat=-23.55, seller_lng=-46.63, cust_city="belo horizonte", cust_state="MG", cust_lat=-19.92,
        cust_lng=-43.94, category_pt="moveis_decoracao", category_en="furniture_decor", product_class="FURNITURE_HOME",
        class_confidence=1.0, handling_profile="MEDIUM", billable_weight_kg=12.0, weight_kg=10.0, length_cm=50.0,
        height_cm=40.0, width_cm=30.0,
    )
    base.update(kw)
    return LineRecord(**base)


def sp_spec(**kw) -> DisruptionSpec:
    base = dict(
        title="Heavy rains leaving Sao Paulo", disruption_type="weather", legs=["first_mile", "linehaul"],
        origin=GeoScope(states=["SP"]), start_at=AS_OF, duration_days=5, recovery_days=2, source="user",
    )
    base.update(kw)
    return DisruptionSpec(**base)


@pytest.fixture
def policy_ctx_factory(lanes, params):
    def factory(spec: DisruptionSpec, alt: dict | None = None) -> PolicyContext:
        return PolicyContext(
            as_of=AS_OF, spec=spec, lanes=lanes,
            seller_stats={"s1": SellerStat("s1", 100, 0.1, 0.1, 2.0), "alt1": SellerStat("alt1", 50, 0.05, 0.05, 2.0)},
            seller_geo={"s1": ("sao paulo", "SP", -23.55, -46.63), "alt1": ("rio de janeiro", "RJ", -22.9, -43.2)},
            alt_sellers=alt or {}, global_handling_days=2.0, policy=get_policy(), params=params,
        )

    return factory


@pytest.fixture
def scoring():
    return get_scoring()


@pytest.fixture
def gazetteer() -> Gazetteer:
    return Gazetteer([
        CityEntry("sao paulo", "SP", "Sao Paulo, SP", -23.55, -46.63, 15000),
        CityEntry("rio de janeiro", "RJ", "Rio de Janeiro, RJ", -22.9, -43.2, 7000),
        CityEntry("campinas", "SP", "Campinas, SP", -22.9, -47.06, 1500),
        CityEntry("porto alegre", "RS", "Porto Alegre, RS", -30.03, -51.23, 1400),
        CityEntry("belo horizonte", "MG", "Belo Horizonte, MG", -19.92, -43.94, 2800),
        CityEntry("curitiba", "PR", "Curitiba, PR", -25.43, -49.27, 1600),
        CityEntry("bom jesus", "PI", "Bom Jesus, PI", -9.07, -44.36, 5),
    ])


def alt_seller(price: float = 105.0) -> AltSeller:
    return AltSeller("p" * 32, "alt1", "RJ", "rio de janeiro", datetime(2018, 7, 20), price, 4)
