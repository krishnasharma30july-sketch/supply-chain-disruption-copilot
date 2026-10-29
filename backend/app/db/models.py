"""SQLAlchemy ORM models: reference data (loaded by ETL), retrieval documents and run records."""

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMBED_DIM = 384


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------- reference data


class Seller(Base):
    __tablename__ = "sellers"
    seller_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    zip_prefix: Mapped[str] = mapped_column(String(5))
    city: Mapped[str] = mapped_column(String(80))
    city_norm: Mapped[str] = mapped_column(String(80), index=True)
    state: Mapped[str] = mapped_column(String(2), index=True)
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)


class Customer(Base):
    __tablename__ = "customers"
    customer_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    customer_unique_id: Mapped[str] = mapped_column(String(32), index=True)
    zip_prefix: Mapped[str] = mapped_column(String(5))
    city: Mapped[str] = mapped_column(String(80))
    city_norm: Mapped[str] = mapped_column(String(80), index=True)
    state: Mapped[str] = mapped_column(String(2), index=True)
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    first_name: Mapped[str] = mapped_column(String(40))


class Category(Base):
    __tablename__ = "categories"
    name_pt: Mapped[str] = mapped_column(String(80), primary_key=True)
    name_en: Mapped[str] = mapped_column(String(80))
    product_class: Mapped[str] = mapped_column(String(32))
    class_confidence: Mapped[float] = mapped_column(Float)


class Product(Base):
    __tablename__ = "products"
    product_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    category_pt: Mapped[str | None] = mapped_column(String(80), index=True)
    weight_g: Mapped[float] = mapped_column(Float)
    length_cm: Mapped[float] = mapped_column(Float)
    height_cm: Mapped[float] = mapped_column(Float)
    width_cm: Mapped[float] = mapped_column(Float)
    billable_weight_kg: Mapped[float] = mapped_column(Float)
    handling_profile: Mapped[str] = mapped_column(String(10))
    imputed: Mapped[bool] = mapped_column(Boolean, default=False)


class Order(Base):
    __tablename__ = "orders"
    order_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    customer_id: Mapped[str] = mapped_column(String(32), index=True)
    status_raw: Mapped[str] = mapped_column(String(20))
    stage: Mapped[str] = mapped_column(String(20), index=True)
    purchase_ts: Mapped[datetime] = mapped_column(DateTime)
    approved_ts: Mapped[datetime | None] = mapped_column(DateTime)
    carrier_ts: Mapped[datetime | None] = mapped_column(DateTime)
    delivered_ts: Mapped[datetime | None] = mapped_column(DateTime)
    estimated_delivery_ts: Mapped[datetime] = mapped_column(DateTime)


class OrderItem(Base):
    __tablename__ = "order_items"
    order_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    order_item_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[str] = mapped_column(String(32), index=True)
    seller_id: Mapped[str] = mapped_column(String(32), index=True)
    shipping_limit_ts: Mapped[datetime | None] = mapped_column(DateTime)
    price: Mapped[float] = mapped_column(Float)
    freight_value: Mapped[float] = mapped_column(Float)


class OrderActual(Base):
    """Unmasked actual timestamps. Read ONLY by evaluation/backtest code (enforced by tests)."""

    __tablename__ = "order_actuals"
    order_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    carrier_ts: Mapped[datetime | None] = mapped_column(DateTime)
    delivered_ts: Mapped[datetime | None] = mapped_column(DateTime)


class LaneStatRow(Base):
    __tablename__ = "lane_stats"
    origin: Mapped[str] = mapped_column(String(3), primary_key=True)
    dest: Mapped[str] = mapped_column(String(3), primary_key=True)
    level: Mapped[str] = mapped_column(String(12), primary_key=True)
    n: Mapped[int] = mapped_column(Integer)
    mean: Mapped[float] = mapped_column(Float)
    p50: Mapped[float] = mapped_column(Float)
    p90: Mapped[float] = mapped_column(Float)
    quantiles: Mapped[list] = mapped_column(JSONB)


class SellerStatRow(Base):
    __tablename__ = "seller_stats"
    seller_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    orders_n: Mapped[int] = mapped_column(Integer)
    late_handover_rate: Mapped[float | None] = mapped_column(Float)
    late_delivery_rate: Mapped[float | None] = mapped_column(Float)
    median_handling_days: Mapped[float | None] = mapped_column(Float)


class AltSellerRow(Base):
    __tablename__ = "alt_sellers"
    product_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    seller_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    seller_state: Mapped[str] = mapped_column(String(2))
    seller_city: Mapped[str] = mapped_column(String(80))
    last_sale_ts: Mapped[datetime] = mapped_column(DateTime)
    avg_price: Mapped[float] = mapped_column(Float)
    sales_n: Mapped[int] = mapped_column(Integer)


class City(Base):
    __tablename__ = "cities"
    city_norm: Mapped[str] = mapped_column(String(80), primary_key=True)
    state: Mapped[str] = mapped_column(String(2), primary_key=True)
    label: Mapped[str] = mapped_column(String(100))
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    weight: Mapped[int] = mapped_column(Integer)


class DatasetMeta(Base):
    __tablename__ = "dataset_meta"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB)


# --------------------------------------------------------------------------- retrieval


class Document(Base):
    __tablename__ = "documents"
    doc_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    doc_type: Mapped[str] = mapped_column(String(16), index=True)
    ref_id: Mapped[str] = mapped_column(String(120), index=True)
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column("metadata", JSONB)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBED_DIM))
    tsv: Mapped[str | None] = mapped_column(TSVECTOR)

    __table_args__ = (
        Index(
            "ix_documents_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_documents_tsv", "tsv", postgresql_using="gin"),
        Index("ix_documents_meta", "metadata", postgresql_using="gin", postgresql_ops={"metadata": "jsonb_path_ops"}),
    )


# --------------------------------------------------------------------------- runs


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    raw_query: Mapped[str | None] = mapped_column(Text)
    spec: Mapped[dict | None] = mapped_column(JSONB)
    spec_source: Mapped[str | None] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), index=True)
    options: Mapped[dict] = mapped_column(JSONB)
    config_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    llm_mode: Mapped[str] = mapped_column(String(10))
    metrics: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[dict | None] = mapped_column(JSONB)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Updated periodically while a worker executes the run; a stale heartbeat means the worker died.
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RunEvent(Base):
    __tablename__ = "run_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    stage: Mapped[str] = mapped_column(String(32))
    type: Mapped[str] = mapped_column(String(32))
    level: Mapped[str] = mapped_column(String(10))
    message: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_run_events_seq"),)


class OrderOutcome(Base):
    """Per-run, per-order impact + decision. Key columns support filtering; `detail` holds the full trace."""

    __tablename__ = "order_outcomes"
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True)
    order_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    customer_unique_id: Mapped[str] = mapped_column(String(32), index=True)
    stage: Mapped[str] = mapped_column(String(20))
    origin_states: Mapped[list[str]] = mapped_column(ARRAY(String(2)))
    dest_state: Mapped[str] = mapped_column(String(2))
    categories: Mapped[list[str]] = mapped_column(ARRAY(String(80)))
    product_classes: Mapped[list[str]] = mapped_column(ARRAY(String(32)))
    handling_profile: Mapped[str] = mapped_column(String(10))
    value_brl: Mapped[float] = mapped_column(Float)
    billable_weight_kg: Mapped[float] = mapped_column(Float)
    affected: Mapped[bool] = mapped_column(Boolean, index=True)
    hold_days: Mapped[float] = mapped_column(Float)
    days_late: Mapped[float] = mapped_column(Float)
    resolution_days_late: Mapped[int] = mapped_column(Integer)
    p_breach: Mapped[float] = mapped_column(Float)
    severity_score: Mapped[float] = mapped_column(Float, index=True)
    severity_tier: Mapped[str] = mapped_column(String(10), index=True)
    action: Mapped[str] = mapped_column(String(32), index=True)
    offer_cancellation: Mapped[bool] = mapped_column(Boolean)
    needs_review: Mapped[bool] = mapped_column(Boolean)
    decided_by: Mapped[str] = mapped_column(String(20))
    est_cost_brl: Mapped[float] = mapped_column(Float)
    sla_ts: Mapped[datetime] = mapped_column(DateTime)
    projected_eta: Mapped[datetime] = mapped_column(DateTime)
    resolution_eta: Mapped[datetime] = mapped_column(DateTime)
    detail: Mapped[dict] = mapped_column(JSONB)


class Communication(Base):
    __tablename__ = "communications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    customer_unique_id: Mapped[str] = mapped_column(String(32))
    order_ids: Mapped[list[str]] = mapped_column(ARRAY(String(32)))
    first_name: Mapped[str] = mapped_column(String(40))
    locale: Mapped[str] = mapped_column(String(8))
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    generator: Mapped[str] = mapped_column(String(10))  # llm | template | human
    model: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(20), index=True)  # draft | qa_failed | approved | sent | rejected
    qa: Mapped[dict] = mapped_column(JSONB)
    facts: Mapped[dict] = mapped_column(JSONB)
    max_severity: Mapped[float] = mapped_column(Float)
    version: Mapped[int] = mapped_column(Integer, default=1)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    # Delivery: approved emails are sent over SMTP (Mailpit in the local stack); failures keep status "approved".
    email_to: Mapped[str | None] = mapped_column(String(160))
    approved_by: Mapped[str | None] = mapped_column(String(120))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message_id: Mapped[str | None] = mapped_column(String(200))
    send_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class RunSummary(Base):
    __tablename__ = "run_summaries"
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True)
    stats: Mapped[dict] = mapped_column(JSONB)
    retrieval: Mapped[dict] = mapped_column(JSONB, default=dict)
    headline: Mapped[str] = mapped_column(Text)
    narrative: Mapped[str] = mapped_column(Text)
    recommendations: Mapped[list] = mapped_column(JSONB)
    number_check: Mapped[dict] = mapped_column(JSONB)
    generator: Mapped[str] = mapped_column(String(10))


class LlmCall(Base):
    __tablename__ = "llm_calls"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str | None] = mapped_column(String(36), index=True)
    agent: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(32))
    request_hash: Mapped[str] = mapped_column(String(64))
    cached: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16))
    finish_reason: Mapped[str | None] = mapped_column(String(32))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    error: Mapped[str | None] = mapped_column(Text)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LlmCache(Base):
    __tablename__ = "llm_cache"
    request_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    model: Mapped[str] = mapped_column(String(64))
    response_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EmbeddingCache(Base):
    """Embeddings keyed by text hash and model, so ETL rebuilds only embed documents whose text changed."""

    __tablename__ = "embedding_cache"
    text_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    model: Mapped[str] = mapped_column(String(120), primary_key=True)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBED_DIM))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(120), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(16))  # analyst | approver | admin
    password_hash: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    entity: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


REFERENCE_TABLES = [
    "documents",
    "order_actuals",
    "order_items",
    "orders",
    "products",
    "categories",
    "customers",
    "sellers",
    "lane_stats",
    "seller_stats",
    "alt_sellers",
    "cities",
]
