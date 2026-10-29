"""Loads the snapshot (actionable order lines + statistics) into memory once per process."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.policy_config import AssumptionParams, PolicyConfig, get_assumptions, get_category_classes
from app.db.models import (
    AltSellerRow,
    Category,
    City,
    DatasetMeta,
    Document,
    LaneStatRow,
    Seller,
    SellerStatRow,
)
from app.domain.models import AltSeller, DisruptionSpec, LaneStat, LineRecord, SellerStat
from app.services.concepts import CategoryInfo, ConceptResolver
from app.services.eta import LaneBook
from app.services.geo import CityEntry, Gazetteer
from app.services.impact import EngineContext
from app.services.policy_engine import PolicyContext

log = get_logger(__name__)

LINES_SQL = text(
    """
    SELECT o.order_id, oi.order_item_id, oi.product_id, oi.seller_id, o.customer_id, c.customer_unique_id, o.stage,
           o.purchase_ts, o.approved_ts, o.carrier_ts, o.estimated_delivery_ts, oi.shipping_limit_ts,
           oi.price, oi.freight_value,
           s.city_norm AS seller_city, s.state AS seller_state, s.lat AS seller_lat, s.lng AS seller_lng,
           c.city_norm AS cust_city, c.state AS cust_state, c.lat AS cust_lat, c.lng AS cust_lng,
           p.category_pt, COALESCE(cat.name_en, 'unknown') AS category_en,
           COALESCE(cat.product_class, 'STANDARD') AS product_class,
           COALESCE(cat.class_confidence, 0.3) AS class_confidence,
           p.handling_profile, p.billable_weight_kg, p.weight_g, p.length_cm, p.height_cm, p.width_cm, p.imputed
    FROM orders o
    JOIN order_items oi ON oi.order_id = o.order_id
    JOIN customers c ON c.customer_id = o.customer_id
    JOIN sellers s ON s.seller_id = oi.seller_id
    JOIN products p ON p.product_id = oi.product_id
    LEFT JOIN categories cat ON cat.name_pt = p.category_pt
    WHERE o.stage <> 'delivered'
    ORDER BY o.order_id, oi.order_item_id
    """
)


@dataclass
class ReferenceData:
    as_of: datetime
    meta: dict
    lines: list[LineRecord]
    lines_by_order: dict[str, list[LineRecord]]
    lanes: LaneBook
    seller_stats: dict[str, SellerStat]
    seller_geo: dict[str, tuple[str, str, float | None, float | None]]
    alt_sellers: dict[str, list[AltSeller]]
    global_handling_days: float
    global_approval_days: float
    value_sorted: list[float]
    gazetteer: Gazetteer
    resolver: ConceptResolver
    params: AssumptionParams

    def engine_context(self) -> EngineContext:
        return EngineContext(
            as_of=self.as_of,
            lanes=self.lanes,
            seller_stats=self.seller_stats,
            global_handling_days=self.global_handling_days,
            global_approval_days=self.global_approval_days,
            params=self.params,
        )

    def policy_context(self, spec: DisruptionSpec, policy: PolicyConfig) -> PolicyContext:
        return PolicyContext(
            as_of=self.as_of,
            spec=spec,
            lanes=self.lanes,
            seller_stats=self.seller_stats,
            seller_geo=self.seller_geo,
            alt_sellers=self.alt_sellers,
            global_handling_days=self.global_handling_days,
            policy=policy,
            params=self.params,
        )


_cache: ReferenceData | None = None
_last_check = 0.0
_lock = asyncio.Lock()
FINGERPRINT_CHECK_S = 15.0


class DatasetNotReady(RuntimeError):
    pass


def _fingerprint(ref: ReferenceData | None) -> str | None:
    return ref.meta.get("build", {}).get("fingerprint") if ref is not None else None


async def get_reference(session: AsyncSession, embed_fn=None) -> ReferenceData:
    """Process-wide snapshot cache. Every FINGERPRINT_CHECK_S it checks the dataset fingerprint and reloads after an
    ETL rebuild, so running API/worker processes never keep serving a replaced snapshot."""
    global _cache, _last_check
    if _cache is not None and time.monotonic() - _last_check < FINGERPRINT_CHECK_S:
        return _cache
    async with _lock:
        if _cache is not None and time.monotonic() - _last_check < FINGERPRINT_CHECK_S:
            return _cache
        build = (await session.execute(select(DatasetMeta.value).where(DatasetMeta.key == "build"))).scalar()
        current = build.get("fingerprint") if build else None
        _last_check = time.monotonic()
        if _cache is not None and (current is None or current == _fingerprint(_cache)):
            return _cache  # unchanged (or an ETL rebuild is in progress: keep serving the previous snapshot)
        if _cache is not None:
            log.info("reference_reload", old=str(_fingerprint(_cache))[:12], new=str(current)[:12])
        _cache = await _load(session, embed_fn)
    return _cache


def reset_reference() -> None:
    global _cache, _last_check
    _cache = None
    _last_check = 0.0


async def _load(session: AsyncSession, embed_fn) -> ReferenceData:
    meta_rows = (await session.execute(select(DatasetMeta))).scalars().all()
    meta = {m.key: m.value for m in meta_rows}
    if "build" not in meta:
        raise DatasetNotReady("The dataset has not been loaded yet. Run the ETL (seed) first.")
    build = meta["build"]
    as_of = datetime.fromisoformat(build["as_of"])
    params = get_assumptions().params

    rows = (await session.execute(LINES_SQL)).mappings().all()
    lines: list[LineRecord] = []
    by_order: dict[str, list[LineRecord]] = {}
    for r in rows:
        rec = LineRecord(
            order_id=r["order_id"], order_item_id=r["order_item_id"], product_id=r["product_id"],
            seller_id=r["seller_id"], customer_id=r["customer_id"], customer_unique_id=r["customer_unique_id"],
            stage=r["stage"], purchase_ts=r["purchase_ts"], approved_ts=r["approved_ts"], carrier_ts=r["carrier_ts"],
            sla_ts=r["estimated_delivery_ts"], shipping_limit_ts=r["shipping_limit_ts"],
            price=float(r["price"]), freight=float(r["freight_value"]),
            seller_city=r["seller_city"], seller_state=r["seller_state"],
            seller_lat=r["seller_lat"], seller_lng=r["seller_lng"],
            cust_city=r["cust_city"], cust_state=r["cust_state"], cust_lat=r["cust_lat"], cust_lng=r["cust_lng"],
            category_pt=r["category_pt"], category_en=r["category_en"], product_class=r["product_class"],
            class_confidence=float(r["class_confidence"]), handling_profile=r["handling_profile"],
            billable_weight_kg=float(r["billable_weight_kg"]), weight_kg=float(r["weight_g"]) / 1000.0,
            length_cm=float(r["length_cm"]), height_cm=float(r["height_cm"]), width_cm=float(r["width_cm"]),
            imputed=bool(r["imputed"]),
        )
        lines.append(rec)
        by_order.setdefault(rec.order_id, []).append(rec)

    lane_rows = (await session.execute(select(LaneStatRow))).scalars().all()
    lanes = LaneBook(
        [
            LaneStat(origin=l.origin, dest=l.dest, n=l.n, mean=l.mean, p50=l.p50, p90=l.p90,
                     quantiles=list(l.quantiles), level=l.level)  # type: ignore[arg-type]
            for l in lane_rows
        ],
        params.lane_min_samples,
    )
    seller_stats = {
        s.seller_id: SellerStat(
            seller_id=s.seller_id, orders_n=s.orders_n, late_handover_rate=s.late_handover_rate,
            late_delivery_rate=s.late_delivery_rate, median_handling_days=s.median_handling_days,
        )
        for s in (await session.execute(select(SellerStatRow))).scalars().all()
    }
    seller_geo = {
        s.seller_id: (s.city_norm, s.state, s.lat, s.lng)
        for s in (await session.execute(select(Seller))).scalars().all()
    }
    alt: dict[str, list[AltSeller]] = {}
    for a in (await session.execute(select(AltSellerRow))).scalars().all():
        alt.setdefault(a.product_id, []).append(
            AltSeller(product_id=a.product_id, seller_id=a.seller_id, seller_state=a.seller_state,
                      seller_city=a.seller_city, last_sale_ts=a.last_sale_ts, avg_price=a.avg_price, sales_n=a.sales_n)
        )
    gazetteer = Gazetteer(
        [
            CityEntry(name=c.city_norm, state=c.state, label=c.label, lat=c.lat, lng=c.lng, weight=c.weight)
            for c in (await session.execute(select(City))).scalars().all()
        ]
    )
    cats = (await session.execute(select(Category).order_by(Category.name_pt))).scalars().all()
    infos = [CategoryInfo(name_pt=c.name_pt, name_en=c.name_en, product_class=c.product_class) for c in cats]
    vec_rows = (
        await session.execute(select(Document.ref_id, Document.embedding).where(Document.doc_type == "category"))
    ).all()
    vec_by_ref = {ref: np.asarray(emb, dtype=np.float32) for ref, emb in vec_rows}
    vectors = None
    if infos and all(c.name_pt in vec_by_ref for c in infos):
        vectors = np.stack([vec_by_ref[c.name_pt] for c in infos])
    resolver = ConceptResolver(infos, get_category_classes(), params.heavy_min_kg, vectors, embed_fn)

    order_values = sorted(round(sum(l.price + l.freight for l in ls), 2) for ls in by_order.values())
    return ReferenceData(
        as_of=as_of,
        meta=meta,
        lines=lines,
        lines_by_order=by_order,
        lanes=lanes,
        seller_stats=seller_stats,
        seller_geo=seller_geo,
        alt_sellers=alt,
        global_handling_days=float(build["global_handling_days"]),
        global_approval_days=float(build["global_approval_days"]),
        value_sorted=order_values,
        gazetteer=gazetteer,
        resolver=resolver,
        params=params,
    )
