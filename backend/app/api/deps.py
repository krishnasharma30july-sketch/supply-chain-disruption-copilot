from __future__ import annotations

import asyncio

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainError
from app.core.logging import get_logger
from app.db.session import get_session
from app.llm.service import LLMService
from app.services.reference import DatasetNotReady, ReferenceData, get_reference
from app.services.retrieval.embedder import Embedder, get_embedder

log = get_logger(__name__)
_embedder_failed = False


def get_llm(request: Request) -> LLMService:
    return request.app.state.llm


def get_arq(request: Request):
    return request.app.state.arq


async def get_embedder_or_none() -> Embedder | None:
    """The embedding model loads lazily on first use; when it fails, retrieval degrades to full-text only."""
    global _embedder_failed
    if _embedder_failed:
        return None
    try:
        return await asyncio.to_thread(get_embedder)
    except Exception:
        _embedder_failed = True
        log.exception("embedder_unavailable")
        return None


_reranker_failed = False


async def get_reranker_or_none():
    """Cross-encoder reranker (lazy); when unavailable, hybrid search falls back to RRF order."""
    global _reranker_failed
    from app.core.config import get_settings
    from app.services.retrieval.reranker import get_reranker

    if _reranker_failed or not get_settings().rerank_enabled:
        return None
    try:
        return await asyncio.to_thread(get_reranker)
    except Exception:
        _reranker_failed = True
        log.exception("reranker_unavailable")
        return None


async def get_ref(
    session: AsyncSession = Depends(get_session),
    embedder: Embedder | None = Depends(get_embedder_or_none),
) -> ReferenceData:
    try:
        return await get_reference(session, embedder.embed if embedder is not None else None)
    except DatasetNotReady as e:
        raise DomainError(str(e), title="Dataset not ready", status_code=503) from e
