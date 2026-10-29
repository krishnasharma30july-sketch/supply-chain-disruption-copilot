"""ETL: raw Olist CSVs -> validated, time-masked snapshot + statistics + retrieval index in Postgres.

Idempotent: a fingerprint of inputs (file hashes, AS_OF, corpus size, embedding model, configs, ETL version)
is stored in dataset_meta and the build is skipped when nothing changed (unless --force).
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import polars as pl
from sqlalchemy import delete, insert, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.policy_config import get_assumptions, get_category_classes
from app.db import models as m
from app.db.session import get_sync_engine, init_db
from app.etl.documents import (
    category_document,
    order_document,
    policy_document,
    product_document,
    seller_document,
)
from app.etl.download import RAW_FILES, ensure_raw, file_hashes
from app.etl.names import synthetic_first_name
from app.services.eta import QUANTILE_PS
from app.services.geo import normalize_city, title_city
from app.services.retrieval.kb import load_kb

log = get_logger(__name__)
ETL_VERSION = "etl-v3"  # bump whenever ETL code changes what is loaded (code is not part of the fingerprint)
TS_FMT = "%Y-%m-%d %H:%M:%S"
BRAZIL_BBOX = {"lat": (-34.0, 5.5), "lng": (-74.0, -34.0)}


# Field-weighted full-text index: derived tags ("heavy bulky oversized", "perishable", ...) carry the meaning and rank
# highest (A), then the title (B), then the body (D). Without weights, place names repeated in the body ("Sao Paulo")
# outrank the concepts the user is searching for.
TSV_UPDATE = text(
    """
    UPDATE documents SET tsv =
        setweight(to_tsvector('simple', unaccent(coalesce(substring(content from 'Tags: ([^\n]*)'), ''))), 'A')
     || setweight(to_tsvector('simple', unaccent(title)), 'B')
     || setweight(to_tsvector('simple', unaccent(content)), 'D')
    """
)


class ValidationFailed(RuntimeError):
    pass


# --------------------------------------------------------------------------- read & validate


def _read(raw: Path, key: str, overrides: dict | None = None) -> pl.DataFrame:
    df = pl.read_csv(raw / RAW_FILES[key], schema_overrides=overrides or {}, infer_schema_length=20000)
    return df.rename({c: c.lstrip("﻿").strip() for c in df.columns})


def _require(df: pl.DataFrame, name: str, cols: list[str]) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValidationFailed(f"{name}: missing columns {missing}")


def _fk(child: pl.DataFrame, parent: pl.DataFrame, key: str, name: str) -> None:
    orphans = child.select(key).unique().join(parent.select(key).unique(), on=key, how="anti").height
    if orphans:
        raise ValidationFailed(f"{name}: {orphans} {key} values have no parent row")


def _ts(cols: list[str]) -> list[pl.Expr]:
    return [pl.col(c).str.to_datetime(TS_FMT, strict=False) for c in cols]


def load_raw(raw: Path) -> dict[str, pl.DataFrame]:
    zip_str = {"seller_zip_code_prefix": pl.Utf8}
    orders = _read(raw, "orders")
    items = _read(raw, "items")
    products = _read(raw, "products")
    sellers = _read(raw, "sellers", zip_str)
    customers = _read(raw, "customers", {"customer_zip_code_prefix": pl.Utf8})
    translation = _read(raw, "translation")
    geo = _read(raw, "geolocation", {"geolocation_zip_code_prefix": pl.Utf8})

    _require(orders, "orders", ["order_id", "customer_id", "order_status", "order_purchase_timestamp", "order_approved_at",
                                "order_delivered_carrier_date", "order_delivered_customer_date", "order_estimated_delivery_date"])
    _require(items, "order_items", ["order_id", "order_item_id", "product_id", "seller_id", "shipping_limit_date", "price", "freight_value"])
    _require(products, "products", ["product_id", "product_category_name", "product_weight_g", "product_length_cm",
                                    "product_height_cm", "product_width_cm"])
    _require(sellers, "sellers", ["seller_id", "seller_zip_code_prefix", "seller_city", "seller_state"])
    _require(customers, "customers", ["customer_id", "customer_unique_id", "customer_zip_code_prefix", "customer_city", "customer_state"])
    _require(translation, "translation", ["product_category_name", "product_category_name_english"])
    _require(geo, "geolocation", ["geolocation_zip_code_prefix", "geolocation_lat", "geolocation_lng"])

    orders = orders.with_columns(_ts([
        "order_purchase_timestamp", "order_approved_at", "order_delivered_carrier_date",
        "order_delivered_customer_date", "order_estimated_delivery_date",
    ]))
    items = items.with_columns(_ts(["shipping_limit_date"]))
    if orders["order_purchase_timestamp"].null_count() or orders["order_estimated_delivery_date"].null_count():
        raise ValidationFailed("orders: purchase or estimated delivery timestamps failed to parse")
    if orders["order_id"].n_unique() != orders.height:
        raise ValidationFailed("orders: duplicate order_id")
    if (items["price"] < 0).any() or (items["freight_value"] < 0).any():
        raise ValidationFailed("order_items: negative price or freight")

    _fk(items, orders, "order_id", "order_items->orders")
    _fk(items, products, "product_id", "order_items->products")
    _fk(items, sellers, "seller_id", "order_items->sellers")
    _fk(orders, customers, "customer_id", "orders->customers")

    sellers = sellers.with_columns(pl.col("seller_zip_code_prefix").str.zfill(5))
    customers = customers.with_columns(pl.col("customer_zip_code_prefix").str.zfill(5))
    geo = geo.with_columns(pl.col("geolocation_zip_code_prefix").str.zfill(5))
    return {"orders": orders, "items": items, "products": products, "sellers": sellers,
            "customers": customers, "translation": translation, "geo": geo}


def _norm_city_column(df: pl.DataFrame, src: str, dst: str) -> pl.DataFrame:
    uniq = [c for c in df[src].unique().to_list() if c is not None]
    mapping = pl.DataFrame({src: uniq, dst: [normalize_city(c) for c in uniq]}, schema={src: pl.Utf8, dst: pl.Utf8})
    return df.join(mapping, on=src, how="left").with_columns(pl.col(dst).fill_null(""))


# --------------------------------------------------------------------------- transforms


def build_frames(raw: dict[str, pl.DataFrame], as_of: datetime, corpus_size: int) -> dict:
    p = get_assumptions().params
    classes_cfg = get_category_classes()
    orders, items, products = raw["orders"], raw["items"], raw["products"]
    T = as_of

    # Zip centroids (Brazil bounding box only).
    zipc = (
        raw["geo"]
        .filter(
            pl.col("geolocation_lat").is_between(*BRAZIL_BBOX["lat"])
            & pl.col("geolocation_lng").is_between(*BRAZIL_BBOX["lng"])
        )
        .group_by("geolocation_zip_code_prefix")
        .agg(pl.col("geolocation_lat").mean().alias("lat"), pl.col("geolocation_lng").mean().alias("lng"))
        .rename({"geolocation_zip_code_prefix": "zip_prefix"})
    )

    sellers = (
        _norm_city_column(raw["sellers"], "seller_city", "city_norm")
        .rename({"seller_zip_code_prefix": "zip_prefix", "seller_city": "city", "seller_state": "state"})
        .with_columns(pl.col("state").str.to_uppercase())
        .join(zipc, on="zip_prefix", how="left")
    )
    customers_all = (
        _norm_city_column(raw["customers"], "customer_city", "city_norm")
        .rename({"customer_zip_code_prefix": "zip_prefix", "customer_city": "city", "customer_state": "state"})
        .with_columns(pl.col("state").str.to_uppercase())
        .join(zipc, on="zip_prefix", how="left")
    )

    # Categories (translation + manual additions + class map, assumption A11).
    tr = dict(zip(raw["translation"]["product_category_name"].to_list(), raw["translation"]["product_category_name_english"].to_list(), strict=True))
    tr.update(classes_cfg.manual_translations)
    cat_names = sorted({c for c in products["product_category_name"].to_list() if c} | set(tr))
    categories = []
    for c in cat_names:
        cls, conf = classes_cfg.class_of(c)
        categories.append({"name_pt": c, "name_en": tr.get(c, c), "product_class": cls, "class_confidence": conf})
    cat_df = pl.DataFrame(categories)

    # Products: fix column typos, impute missing dims by category median, billable weight & profile (A9, A10).
    prod = products.rename({
        k: v for k, v in {"product_name_lenght": "product_name_length",
                          "product_description_lenght": "product_description_length"}.items()
        if k in products.columns
    }).rename({"product_category_name": "category_pt", "product_weight_g": "weight_g", "product_length_cm": "length_cm",
               "product_height_cm": "height_cm", "product_width_cm": "width_cm"})
    dims = ["weight_g", "length_cm", "height_cm", "width_cm"]
    prod = prod.with_columns([pl.col(c).cast(pl.Float64) for c in dims])
    prod = prod.with_columns(pl.any_horizontal([pl.col(c).is_null() for c in dims]).alias("imputed"))
    cat_med = prod.group_by("category_pt").agg([pl.col(c).median().alias(f"{c}_cm") for c in dims])
    glob = {c: float(prod[c].median()) for c in dims}
    prod = prod.join(cat_med, on="category_pt", how="left").with_columns(
        [pl.coalesce(pl.col(c), pl.col(f"{c}_cm"), pl.lit(glob[c])).alias(c) for c in dims]
    ).drop([f"{c}_cm" for c in dims])
    prod = prod.with_columns(
        pl.max_horizontal(
            pl.col("weight_g") / 1000.0,
            pl.col("length_cm") * pl.col("height_cm") * pl.col("width_cm") / p.volumetric_divisor,
        ).alias("billable_weight_kg")
    )
    maxdim = pl.max_horizontal("length_cm", "height_cm", "width_cm")
    sumdim = pl.col("length_cm") + pl.col("height_cm") + pl.col("width_cm")
    prod = prod.with_columns(
        pl.when((pl.col("billable_weight_kg") > p.medium_max_kg) | (maxdim > p.bulky_dim_cm) | (sumdim > p.bulky_sum_dims_cm))
        .then(pl.lit("BULKY"))
        .when((pl.col("billable_weight_kg") <= p.parcel_max_kg) & (maxdim <= p.parcel_max_dim_cm))
        .then(pl.lit("PARCEL"))
        .otherwise(pl.lit("MEDIUM"))
        .alias("handling_profile")
    )

    o = orders.rename({
        "order_purchase_timestamp": "purchase_ts", "order_approved_at": "approved_ts",
        "order_delivered_carrier_date": "carrier_ts", "order_delivered_customer_date": "delivered_ts",
        "order_estimated_delivery_date": "estimated_delivery_ts", "order_status": "status_raw",
    })
    it = items.rename({"shipping_limit_date": "shipping_limit_ts"})

    # Historical statistics use only information available at T (no leakage).
    first_item = it.filter(pl.col("order_item_id") == 1).select("order_id", "seller_id")
    hist = (
        o.filter(
            pl.col("carrier_ts").is_not_null() & pl.col("delivered_ts").is_not_null() & (pl.col("delivered_ts") <= T)
        )
        .join(first_item, on="order_id")
        .join(sellers.select("seller_id", pl.col("state").alias("origin")), on="seller_id")
        .join(customers_all.select("customer_id", pl.col("state").alias("dest")), on="customer_id")
        .with_columns(((pl.col("delivered_ts") - pl.col("carrier_ts")).dt.total_seconds() / 86400.0).alias("transit"))
        .filter(pl.col("transit").is_between(0, 120))
    )
    qexprs = [pl.col("transit").quantile(q, interpolation="linear").alias(f"q{i}") for i, q in enumerate(QUANTILE_PS)]
    base_aggs = [pl.len().alias("n"), pl.col("transit").mean().alias("mean"), *qexprs]

    def lane_rows(df: pl.DataFrame, level: str) -> list[dict]:
        rows = []
        for r in df.to_dicts():
            qs = [round(float(r[f"q{i}"]), 4) for i in range(len(QUANTILE_PS))]
            rows.append({"origin": r["origin"], "dest": r["dest"], "level": level, "n": int(r["n"]),
                         "mean": round(float(r["mean"]), 4), "p50": qs[10], "p90": qs[18], "quantiles": qs})
        return rows

    lanes = lane_rows(hist.group_by("origin", "dest").agg(base_aggs), "lane")
    lanes += lane_rows(hist.group_by("dest").agg(base_aggs).with_columns(pl.lit("*").alias("origin")), "destination")
    lanes += lane_rows(
        hist.select(pl.lit("*").alias("origin"), pl.lit("*").alias("dest"), "transit").group_by("origin", "dest").agg(base_aggs),
        "national",
    )

    si = it.join(o.select("order_id", "purchase_ts", "approved_ts", "carrier_ts", "delivered_ts", "estimated_delivery_ts"),
                 on="order_id").filter(pl.col("purchase_ts") <= T)
    si_u = si.unique(subset=["order_id", "seller_id"])
    handled = si_u.filter(
        pl.col("carrier_ts").is_not_null() & (pl.col("carrier_ts") <= T) & pl.col("approved_ts").is_not_null()
    ).with_columns(
        (((pl.col("carrier_ts") - pl.col("approved_ts")).dt.total_seconds() / 86400.0).clip(lower_bound=0)).alias("handling_days"),
        (pl.col("carrier_ts") > pl.col("shipping_limit_ts")).alias("late_handover"),
    )
    delivered = si_u.filter(pl.col("delivered_ts").is_not_null() & (pl.col("delivered_ts") <= T)).with_columns(
        (pl.col("delivered_ts") > pl.col("estimated_delivery_ts")).alias("late_delivery")
    )
    seller_stats = (
        si_u.group_by("seller_id").agg(pl.col("order_id").n_unique().alias("orders_n"))
        .join(handled.group_by("seller_id").agg(
            pl.col("handling_days").median().alias("median_handling_days"),
            pl.col("late_handover").mean().alias("late_handover_rate"),
        ), on="seller_id", how="left")
        .join(delivered.group_by("seller_id").agg(pl.col("late_delivery").mean().alias("late_delivery_rate")),
              on="seller_id", how="left")
    )
    global_handling = float(handled["handling_days"].median() or 2.0)
    approved_hist = o.filter(pl.col("approved_ts").is_not_null() & (pl.col("approved_ts") <= T)).with_columns(
        ((pl.col("approved_ts") - pl.col("purchase_ts")).dt.total_seconds() / 86400.0).alias("lag")
    ).filter(pl.col("lag") >= 0)
    global_approval = float(approved_hist["lag"].median() or 0.5)

    # Snapshot at T (A1, A3, A19): mask the future, derive the stage, keep the most recent N orders.
    has_items = it.select("order_id").unique()
    snap = (
        o.filter((pl.col("purchase_ts") <= T) & ~pl.col("status_raw").is_in(["canceled", "unavailable"]))
        .join(has_items, on="order_id", how="semi")
        .with_columns([pl.when(pl.col(c) <= T).then(pl.col(c)).otherwise(None).alias(c)
                       for c in ("approved_ts", "carrier_ts", "delivered_ts")])
        .with_columns(
            pl.when(pl.col("delivered_ts").is_not_null()).then(pl.lit("delivered"))
            .when(pl.col("carrier_ts").is_not_null()).then(pl.lit("in_transit"))
            .when(pl.col("approved_ts").is_not_null()).then(pl.lit("awaiting_pickup"))
            .otherwise(pl.lit("pending_approval"))
            .alias("stage")
        )
        .sort(["purchase_ts", "order_id"], descending=[True, False])
        .head(corpus_size)
    )
    # Known limitation (A3): orders whose FINAL status is canceled/unavailable are excluded, which uses later knowledge
    # for any that were still active at T. Measure how many recent orders that affects so it is reported, not hidden.
    excluded_canceled_30d = o.filter(
        (pl.col("purchase_ts") <= T)
        & (pl.col("purchase_ts") >= T - timedelta(days=30))
        & pl.col("status_raw").is_in(["canceled", "unavailable"])
    ).height
    recent_30d = o.filter((pl.col("purchase_ts") <= T) & (pl.col("purchase_ts") >= T - timedelta(days=30))).height
    corpus_ids = snap.select("order_id")
    actuals = o.join(corpus_ids, on="order_id", how="semi").select("order_id", "carrier_ts", "delivered_ts")
    corpus_items = it.join(corpus_ids, on="order_id", how="semi")
    corpus_products = prod.join(corpus_items.select("product_id").unique(), on="product_id", how="semi")
    corpus_customers = customers_all.join(snap.select("customer_id"), on="customer_id", how="semi")

    # Alternate sellers (A12): same product sold by another seller in [T - 90d, T].
    recent = (
        it.join(o.select("order_id", "purchase_ts", "status_raw"), on="order_id")
        .filter(
            (pl.col("purchase_ts") <= T)
            & (pl.col("purchase_ts") >= T - timedelta(days=p.alt_seller_recency_days))
            & ~pl.col("status_raw").is_in(["canceled", "unavailable"])
        )
        .join(corpus_products.select("product_id"), on="product_id", how="semi")
        .group_by("product_id", "seller_id")
        .agg(pl.col("purchase_ts").max().alias("last_sale_ts"), pl.col("price").mean().alias("avg_price"),
             pl.len().alias("sales_n"))
    )
    multi = recent.group_by("product_id").agg(pl.len().alias("k")).filter(pl.col("k") >= 2).select("product_id")
    alt = (
        recent.join(multi, on="product_id", how="semi")
        .join(sellers.select("seller_id", pl.col("state").alias("seller_state"), pl.col("city_norm").alias("seller_city")),
              on="seller_id")
    )

    # City gazetteer: every seller/customer city with an entity-weighted centroid.
    pts = pl.concat([
        sellers.select("city_norm", "state", "lat", "lng"),
        customers_all.select("city_norm", "state", "lat", "lng"),
    ]).filter(pl.col("city_norm") != "")
    cities = pts.group_by("city_norm", "state").agg(
        pl.len().alias("weight"), pl.col("lat").mean().alias("lat"), pl.col("lng").mean().alias("lng")
    )

    return {
        "sellers": sellers, "customers": corpus_customers, "categories": cat_df, "products": corpus_products,
        "orders": snap, "items": corpus_items, "actuals": actuals, "lanes": lanes, "seller_stats": seller_stats,
        "alt": alt, "cities": cities, "global_handling_days": global_handling, "global_approval_days": global_approval,
        "hist_deliveries": hist.height, "excluded_canceled_30d": excluded_canceled_30d, "recent_orders_30d": recent_30d,
    }


# --------------------------------------------------------------------------- load


def _chunks(rows: list[dict], n: int = 2000):
    for i in range(0, len(rows), n):
        yield rows[i : i + n]


def _clean(v):
    if isinstance(v, float) and v != v:  # NaN
        return None
    return v


def _rows(df: pl.DataFrame, cols: dict[str, str]) -> list[dict]:
    """Select/rename columns (src -> dst) and convert to dicts."""
    sel = df.select([pl.col(src).alias(dst) for src, dst in cols.items()])
    return [{k: _clean(v) for k, v in r.items()} for r in sel.to_dicts()]


ETL_CONFIG_FILES = ("assumptions.yaml", "category_classes.yaml")  # policy/scoring do not affect the snapshot


def fingerprint(settings: Settings, hashes: dict[str, str]) -> str:
    cfg_hash = hashlib.sha256()
    for f in [settings.config_dir / n for n in ETL_CONFIG_FILES] + sorted(settings.kb_dir.glob("*.md")):
        cfg_hash.update(f.name.encode())
        cfg_hash.update(f.read_bytes())
    payload = {
        "files": hashes, "as_of": settings.as_of.isoformat(), "corpus": settings.corpus_size,
        "embed": settings.embed_model, "etl": ETL_VERSION, "cfg": cfg_hash.hexdigest(),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def embed_with_cache(engine, embedder, model_name: str, texts: list[str], batch: int = 512) -> list[list[float]]:
    """Embed texts, reusing vectors from `embedding_cache` (keyed by text hash and model): a rebuild after a config
    or AS_OF change only embeds documents whose text actually changed."""
    hashes = [hashlib.sha256(t.encode("utf-8")).hexdigest() for t in texts]
    cached: dict[str, list[float]] = {}
    with engine.connect() as conn:
        unique = sorted(set(hashes))
        for i in range(0, len(unique), 5000):
            rows = conn.execute(
                select(m.EmbeddingCache.text_hash, m.EmbeddingCache.embedding)
                .where(m.EmbeddingCache.model == model_name, m.EmbeddingCache.text_hash.in_(unique[i : i + 5000]))
            )
            cached.update({h: list(map(float, v)) for h, v in rows})
    todo = sorted({h: t for h, t in zip(hashes, texts, strict=True) if h not in cached}.items())
    log.info("etl_embedding_cache", documents=len(texts), cached=len(texts) - sum(h not in cached for h in hashes),
             to_embed=len(todo))
    for i in range(0, len(todo), batch):
        part = todo[i : i + batch]
        vecs = embedder.embed([t for _, t in part]).tolist()
        new = [{"text_hash": h, "model": model_name, "embedding": v} for (h, _), v in zip(part, vecs, strict=True)]
        with engine.begin() as conn:
            conn.execute(pg_insert(m.EmbeddingCache).values(new).on_conflict_do_nothing())
        cached.update({r["text_hash"]: r["embedding"] for r in new})
        log.info("etl_embedding_progress", done=min(i + batch, len(todo)), total=len(todo))
    return [cached[h] for h in hashes]


def run_etl(force: bool = False, as_of: datetime | None = None) -> dict:
    settings = get_settings()
    if as_of is not None:
        settings = settings.model_copy(update={"as_of": as_of})
    t0 = time.monotonic()
    engine = get_sync_engine()
    init_db(engine)
    raw_dir = ensure_raw(settings.data_dir)
    hashes = file_hashes(raw_dir)
    fp = fingerprint(settings, hashes)
    with Session(engine) as s:
        current = s.execute(select(m.DatasetMeta.value).where(m.DatasetMeta.key == "build")).scalar()
    if current and current.get("fingerprint") == fp and not force:
        log.info("etl_skipped_up_to_date", fingerprint=fp[:12])
        return {"skipped": True, **current}

    log.info("etl_start", as_of=settings.as_of.isoformat(), corpus_size=settings.corpus_size)
    raw = load_raw(raw_dir)
    f = build_frames(raw, settings.as_of, settings.corpus_size)
    log.info("etl_frames_built", orders=f["orders"].height, items=f["items"].height, products=f["products"].height,
             lanes=len(f["lanes"]), seconds=round(time.monotonic() - t0, 1))

    cat_rows = f["categories"].to_dicts()
    cat_by_pt = {c["name_pt"]: c for c in cat_rows}
    seller_rows = _rows(f["sellers"], {"seller_id": "seller_id", "zip_prefix": "zip_prefix", "city": "city",
                                       "city_norm": "city_norm", "state": "state", "lat": "lat", "lng": "lng"})
    customer_rows = _rows(f["customers"], {"customer_id": "customer_id", "customer_unique_id": "customer_unique_id",
                                           "zip_prefix": "zip_prefix", "city": "city", "city_norm": "city_norm",
                                           "state": "state", "lat": "lat", "lng": "lng"})
    for c in customer_rows:
        c["first_name"] = synthetic_first_name(c["customer_unique_id"])
    product_rows = _rows(f["products"], {"product_id": "product_id", "category_pt": "category_pt", "weight_g": "weight_g",
                                         "length_cm": "length_cm", "height_cm": "height_cm", "width_cm": "width_cm",
                                         "billable_weight_kg": "billable_weight_kg", "handling_profile": "handling_profile",
                                         "imputed": "imputed"})
    order_rows = _rows(f["orders"], {"order_id": "order_id", "customer_id": "customer_id", "status_raw": "status_raw",
                                     "stage": "stage", "purchase_ts": "purchase_ts", "approved_ts": "approved_ts",
                                     "carrier_ts": "carrier_ts", "delivered_ts": "delivered_ts",
                                     "estimated_delivery_ts": "estimated_delivery_ts"})
    item_rows = _rows(f["items"], {"order_id": "order_id", "order_item_id": "order_item_id", "product_id": "product_id",
                                   "seller_id": "seller_id", "shipping_limit_ts": "shipping_limit_ts", "price": "price",
                                   "freight_value": "freight_value"})
    actual_rows = _rows(f["actuals"], {"order_id": "order_id", "carrier_ts": "carrier_ts", "delivered_ts": "delivered_ts"})
    sstat_rows = _rows(f["seller_stats"], {"seller_id": "seller_id", "orders_n": "orders_n",
                                           "late_handover_rate": "late_handover_rate",
                                           "late_delivery_rate": "late_delivery_rate",
                                           "median_handling_days": "median_handling_days"})
    alt_rows = _rows(f["alt"], {"product_id": "product_id", "seller_id": "seller_id", "seller_state": "seller_state",
                                "seller_city": "seller_city", "last_sale_ts": "last_sale_ts", "avg_price": "avg_price",
                                "sales_n": "sales_n"})
    city_rows = _rows(f["cities"], {"city_norm": "city_norm", "state": "state", "lat": "lat", "lng": "lng", "weight": "weight"})
    for c in city_rows:
        c["label"] = f"{title_city(c['city_norm'])}, {c['state']}"

    # ---------------------------------------------------------------- documents
    from app.services.eta import LaneBook  # local import keeps module import cheap
    from app.domain.models import LaneStat

    lane_book = LaneBook([LaneStat(**{k: v for k, v in r.items()}) for r in f["lanes"]], get_assumptions().params.lane_min_samples)
    seller_by_id = {r["seller_id"]: r for r in seller_rows}
    customer_by_id = {r["customer_id"]: r for r in customer_rows}
    product_by_id = {}
    for r in product_rows:
        cat = cat_by_pt.get(r["category_pt"]) if r["category_pt"] else None
        product_by_id[r["product_id"]] = {
            **r,
            "category_en": cat["name_en"] if cat else "unknown",
            "product_class": cat["product_class"] if cat else get_category_classes().default_class,
        }
    lines_by_order: dict[str, list[dict]] = {}
    for it in item_rows:
        prod = product_by_id[it["product_id"]]
        lines_by_order.setdefault(it["order_id"], []).append({**it, **prod})
    sellers_offering: dict[str, int] = {}
    for a in alt_rows:
        sellers_offering[a["product_id"]] = sellers_offering.get(a["product_id"], 0) + 1
    sstats = {r["seller_id"]: r for r in sstat_rows}

    docs: list[dict] = []
    for o in order_rows:
        lines = sorted(lines_by_order[o["order_id"]], key=lambda l: l["order_item_id"])
        cust = customer_by_id[o["customer_id"]]
        lane = lane_book.get(seller_by_id[lines[0]["seller_id"]]["state"], cust["state"])
        docs.append(order_document(o, lines, seller_by_id, cust, lane.p50))
    for pid, prod in product_by_id.items():
        docs.append(product_document(prod, max(1, sellers_offering.get(pid, 1))))
    active_by_seller: dict[str, int] = {}
    cats_by_seller: dict[str, dict[str, int]] = {}
    classes_by_seller: dict[str, set[str]] = {}
    stage_by_order = {o["order_id"]: o["stage"] for o in order_rows}
    for it in item_rows:
        sid = it["seller_id"]
        prod = product_by_id[it["product_id"]]
        if stage_by_order[it["order_id"]] != "delivered":
            active_by_seller[sid] = active_by_seller.get(sid, 0) + 1
        cats_by_seller.setdefault(sid, {})
        cats_by_seller[sid][prod["category_en"]] = cats_by_seller[sid].get(prod["category_en"], 0) + 1
        classes_by_seller.setdefault(sid, set()).add(prod["product_class"])
    for sid, cats in cats_by_seller.items():
        top = [c for c, _ in sorted(cats.items(), key=lambda kv: -kv[1])[:5]]
        docs.append(seller_document(seller_by_id[sid], sstats.get(sid), top, sorted(classes_by_seller[sid]), active_by_seller.get(sid, 0)))
    for c in cat_rows:
        docs.append(category_document(c["name_pt"], c["name_en"], c["product_class"]))
    for ch in load_kb(settings.kb_dir):
        docs.append(policy_document(ch.chunk_id, ch.doc_title, ch.heading, ch.text))

    log.info("etl_embedding_start", documents=len(docs))
    from app.services.retrieval.embedder import get_embedder

    texts = [f"{d['title']}\n{d['content']}" for d in docs]
    vectors = embed_with_cache(engine, get_embedder(), settings.embed_model, texts)
    doc_rows = [
        {"doc_id": d["doc_id"], "doc_type": d["doc_type"], "ref_id": d["ref_id"], "title": d["title"],
         "content": d["content"], "meta": d["meta"], "embedding": v}
        for d, v in zip(docs, vectors, strict=True)
    ]

    # ---------------------------------------------------------------- write (meta last: readers wait for it)
    counts = {
        "orders": len(order_rows), "order_items": len(item_rows), "products": len(product_rows),
        "sellers": len(seller_rows), "customers": len(customer_rows), "categories": len(cat_rows),
        "lanes": len(f["lanes"]), "alt_sellers": len(alt_rows), "cities": len(city_rows), "documents": len(doc_rows),
        "actionable_orders": sum(1 for o in order_rows if o["stage"] != "delivered"),
        "stages": {st: sum(1 for o in order_rows if o["stage"] == st)
                   for st in ("pending_approval", "awaiting_pickup", "in_transit", "delivered")},
        "historical_deliveries_for_lanes": f["hist_deliveries"],
        "excluded_canceled_last_30d": f["excluded_canceled_30d"],
        "orders_last_30d": f["recent_orders_30d"],
    }
    with Session(engine) as s:
        s.execute(delete(m.DatasetMeta))
        for table in m.REFERENCE_TABLES:
            s.execute(text(f"TRUNCATE TABLE {table}"))
        for model, rows in (
            (m.Seller, seller_rows), (m.Customer, customer_rows), (m.Category, cat_rows), (m.Product, product_rows),
            (m.Order, order_rows), (m.OrderItem, item_rows), (m.OrderActual, actual_rows), (m.LaneStatRow, f["lanes"]),
            (m.SellerStatRow, sstat_rows), (m.AltSellerRow, alt_rows), (m.City, city_rows),
        ):
            for chunk in _chunks(rows):
                s.execute(insert(model), chunk)
        for chunk in _chunks(doc_rows, 500):
            s.execute(insert(m.Document), chunk)
        s.execute(TSV_UPDATE)
        build = {
            "fingerprint": fp, "etl_version": ETL_VERSION, "as_of": settings.as_of.isoformat(),
            "corpus_size": settings.corpus_size, "embed_model": settings.embed_model, "files": hashes,
            "counts": counts, "global_handling_days": round(f["global_handling_days"], 4),
            "global_approval_days": round(f["global_approval_days"], 4),
            "built_at": datetime.now(timezone.utc).isoformat(), "seconds": round(time.monotonic() - t0, 1),
        }
        s.add(m.DatasetMeta(key="build", value=build))
        s.commit()
    with engine.connect() as conn:
        conn.execution_options(isolation_level="AUTOCOMMIT").execute(text("ANALYZE"))
    log.info("etl_done", **{k: v for k, v in build.items() if k in ("seconds", "as_of")}, counts=counts)
    return build
