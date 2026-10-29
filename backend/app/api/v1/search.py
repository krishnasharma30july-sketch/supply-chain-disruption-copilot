from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_embedder_or_none, get_reranker_or_none
from app.api.schemas import SearchHitOut, SearchResponse
from app.core.config import get_settings
from app.db.session import get_session
from app.services.retrieval.embedder import Embedder
from app.services.retrieval.hybrid import SearchFilters, hybrid_search

router = APIRouter(tags=["search"])
DOC_TYPES = ["order", "product", "seller", "category", "policy"]


@router.get("/search", response_model=SearchResponse)
async def search(
    q: str = Query(min_length=2, max_length=300),
    doc_type: list[Literal["order", "product", "seller", "category", "policy"]] | None = Query(None),
    mode: Literal["hybrid", "dense", "sparse"] = "hybrid",
    rerank: bool = Query(True, description="Re-score hybrid candidates with the cross-encoder"),
    stage: str | None = None,
    origin_uf: str | None = None,
    dest_uf: str | None = None,
    product_class: str | None = None,
    handling_profile: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    embedder: Embedder | None = Depends(get_embedder_or_none),
):
    """Hybrid retrieval explorer: dense (pgvector) + sparse (full-text) fused with Reciprocal Rank Fusion, then
    re-ranked by a cross-encoder."""
    meta = {k: v for k, v in {
        "stage": stage, "origin_uf": origin_uf.upper() if origin_uf else None,
        "dest_uf": dest_uf.upper() if dest_uf else None, "product_class": product_class,
        "handling_profile": handling_profile,
    }.items() if v}
    effective = mode if (embedder is not None or mode == "sparse") else "sparse"
    reranker = await get_reranker_or_none() if (rerank and effective == "hybrid") else None
    hits = await hybrid_search(
        session, embedder, q, SearchFilters(doc_types=list(doc_type) if doc_type else None, meta_equals=meta),
        limit=limit, mode=effective, reranker=reranker, rerank_depth=get_settings().rerank_depth,  # type: ignore[arg-type]
    )
    return SearchResponse(
        query=q, mode=mode, effective_mode=effective + ("+rerank" if reranker is not None else ""),
        hits=[SearchHitOut(**h.__dict__) for h in hits],
    )
