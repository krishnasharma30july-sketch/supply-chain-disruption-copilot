"""Hybrid retrieval over pgvector (dense) + Postgres full-text (sparse), fused with Reciprocal Rank Fusion."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.geo import normalize
from app.services.retrieval.embedder import Embedder, to_pgvector
from app.services.retrieval.reranker import Reranker

RRF_K = 60
SPARSE_STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with", "by", "at", "from", "is", "are", "be",
    "all", "any", "e", "o", "os", "as", "de", "do", "da", "dos", "das", "em", "no", "na", "para", "com", "que",
}
_TOKEN = re.compile(r"^[a-z0-9]+$")

Mode = Literal["hybrid", "dense", "sparse"]


@dataclass
class SearchFilters:
    doc_types: list[str] | None = None
    ref_ids: list[str] | None = None
    meta_equals: dict[str, str] = field(default_factory=dict)  # metadata->>key = value


@dataclass
class SearchHit:
    doc_id: str
    doc_type: str
    ref_id: str
    title: str
    snippet: str
    metadata: dict
    score: float
    dense_rank: int | None
    sparse_rank: int | None
    rerank_score: float | None = None


def to_tsquery(query: str) -> str | None:
    """Build a safe OR-tsquery with prefix matching for longer tokens (input is sanitized to [a-z0-9])."""
    tokens: list[str] = []
    for tok in normalize(query).split():
        if len(tok) < 2 or tok in SPARSE_STOPWORDS or not _TOKEN.match(tok):
            continue
        term = f"{tok}:*" if len(tok) >= 4 else tok
        if term not in tokens:
            tokens.append(term)
    return " | ".join(tokens[:24]) if tokens else None


_META_KEY = re.compile(r"^[a-z_]{1,40}$")


def _where(filters: SearchFilters, params: dict) -> str:
    clauses = ["TRUE"]
    if filters.doc_types:
        clauses.append("doc_type = ANY(:doc_types)")
        params["doc_types"] = list(filters.doc_types)
    if filters.ref_ids is not None:
        clauses.append("ref_id = ANY(:ref_ids)")
        params["ref_ids"] = list(filters.ref_ids)
    for i, (key, value) in enumerate(sorted(filters.meta_equals.items())):
        if not _META_KEY.match(key):
            raise ValueError(f"invalid metadata key {key!r}")
        clauses.append(f"metadata->>'{key}' = :meta_{i}")
        params[f"meta_{i}"] = str(value)
    return " AND ".join(clauses)


async def hybrid_search(
    session: AsyncSession,
    embedder: Embedder | None,
    query: str,
    filters: SearchFilters | None = None,
    *,
    limit: int = 20,
    k: int = 200,
    mode: Mode = "hybrid",
    w_dense: float = 1.0,
    w_sparse: float = 1.0,
    reranker: Reranker | None = None,
    rerank_depth: int = 30,
) -> list[SearchHit]:
    """Dense + sparse candidates fused with RRF; in hybrid mode a cross-encoder (when given) re-scores the top
    `rerank_depth` fused candidates and the final order follows the cross-encoder."""
    filters = filters or SearchFilters()
    if filters.ref_ids is not None and not filters.ref_ids:
        return []
    rerank = reranker is not None and mode == "hybrid"
    fetch = max(limit, rerank_depth) if rerank else limit
    params: dict = {"k": k, "limit": fetch, "wd": w_dense, "ws": w_sparse}
    where = _where(filters, params)
    tsq = to_tsquery(query)
    use_dense = mode in ("hybrid", "dense") and embedder is not None
    use_sparse = mode in ("hybrid", "sparse") and tsq is not None
    if not use_dense and not use_sparse:
        return []

    ctes: list[str] = []
    if use_dense:
        vec = await asyncio.to_thread(embedder.embed, [query])  # type: ignore[union-attr]
        params["qvec"] = to_pgvector(vec[0])
        # Inner ORDER BY distance LIMIT k is served by the HNSW index; ranks are assigned afterwards.
        ctes.append(
            f"""dense AS (
                SELECT doc_id, row_number() OVER (ORDER BY dist, doc_id) AS rnk FROM (
                    SELECT doc_id, embedding <=> CAST(:qvec AS vector) AS dist
                    FROM documents WHERE {where}
                    ORDER BY embedding <=> CAST(:qvec AS vector) LIMIT :k) dense_raw)"""
        )
    if use_sparse:
        params["tsq"] = tsq
        ctes.append(
            f"""sparse AS (
                SELECT doc_id, row_number() OVER (ORDER BY rank DESC, doc_id) AS rnk FROM (
                    SELECT doc_id, ts_rank_cd(tsv, to_tsquery('simple', :tsq)) AS rank
                    FROM documents WHERE {where} AND tsv @@ to_tsquery('simple', :tsq)
                    ORDER BY rank DESC LIMIT :k) sparse_raw)"""
        )

    if use_dense and use_sparse:
        source = (
            "dense FULL OUTER JOIN sparse ON dense.doc_id = sparse.doc_id "
            "JOIN documents d ON d.doc_id = COALESCE(dense.doc_id, sparse.doc_id)"
        )
        dense_rank, sparse_rank = "dense.rnk", "sparse.rnk"
    elif use_dense:
        source = "dense JOIN documents d ON d.doc_id = dense.doc_id"
        dense_rank, sparse_rank = "dense.rnk", "NULL::bigint"
    else:
        source = "sparse JOIN documents d ON d.doc_id = sparse.doc_id"
        dense_rank, sparse_rank = "NULL::bigint", "sparse.rnk"

    sql = f"""
        WITH {", ".join(ctes)}
        SELECT d.doc_id, d.doc_type, d.ref_id, d.title, d.content, d.metadata,
               {dense_rank} AS dense_rank, {sparse_rank} AS sparse_rank,
               COALESCE(CAST(:wd AS float8) / ({RRF_K} + {dense_rank}), 0)
             + COALESCE(CAST(:ws AS float8) / ({RRF_K} + {sparse_rank}), 0) AS score
        FROM {source}
        ORDER BY score DESC, d.doc_id
        LIMIT :limit
    """
    if use_dense:
        await session.execute(text("SET LOCAL hnsw.ef_search = 200"))
        await session.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
    rows = (await session.execute(text(sql), params)).mappings().all()
    hits = [
        SearchHit(
            doc_id=r["doc_id"],
            doc_type=r["doc_type"],
            ref_id=r["ref_id"],
            title=r["title"],
            snippet=r["content"][:400],
            metadata=r["metadata"] or {},
            score=round(float(r["score"]), 6),
            dense_rank=r["dense_rank"],
            sparse_rank=r["sparse_rank"],
        )
        for r in rows
    ]
    if rerank and hits:
        scores = await asyncio.to_thread(reranker.scores, query, [f"{h.title}. {h.snippet}" for h in hits])  # type: ignore[union-attr]
        for h, s in zip(hits, scores, strict=True):
            h.rerank_score = round(s, 4)
        hits.sort(key=lambda h: -(h.rerank_score or 0.0))
    return hits[:limit]
