"""Policy retrieval for the AI agents: the retrieval half of retrieval-augmented generation.

The policy knowledge base is indexed in the `documents` table (one document per section). An agent that needs policy
text asks a question in words, hybrid search (meaning + keywords, then a cross-encoder) returns the best sections, and
those sections are placed in the model's context. When search is unavailable (no database session, an error), a keyword
overlap over the same sections answers instead, so the AI step never loses its policy context.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

from app.core.logging import get_logger
from app.services.retrieval.hybrid import SearchFilters, hybrid_search
from app.services.retrieval.kb import KbChunk

log = get_logger(__name__)
_WORD = re.compile(r"[a-z]{3,}")


@dataclass(frozen=True)
class PolicySection:
    id: str  # "<file>#<heading-slug>", the same id the decisions cite
    heading: str
    text: str
    score: float
    via: str  # "hybrid" or "keyword"


PolicySearch = Callable[[str], Awaitable[list[PolicySection]]]


def keyword_sections(kb: Iterable[KbChunk], query: str, limit: int = 2, exclude: Iterable[str] = ()) -> list[PolicySection]:
    """Fallback: rank sections by how many query words they contain."""
    skip = set(exclude)
    words = set(_WORD.findall(query.lower())) or {"delay"}
    scored = sorted(
        ((len(words & set(_WORD.findall(f"{c.heading} {c.text}".lower()))), c) for c in kb if c.chunk_id not in skip),
        key=lambda t: -t[0],
    )
    return [PolicySection(c.chunk_id, c.heading, c.text, float(s), "keyword") for s, c in scored[:limit] if s > 0]


async def retrieve_policy(
    *, session_factory, embedder, reranker, kb: dict[str, KbChunk], query: str, limit: int = 2,
    rerank_depth: int = 10, exclude: Iterable[str] = (),
) -> list[PolicySection]:
    """Hybrid search over the policy documents; falls back to keyword overlap. Never raises."""
    skip = set(exclude)
    query = (query or "").strip() or "delay"
    if session_factory is not None:
        try:
            async with session_factory() as s:
                hits = await hybrid_search(
                    s, embedder, query, SearchFilters(doc_types=["policy"]), limit=limit + len(skip),
                    reranker=reranker, rerank_depth=max(rerank_depth, limit + len(skip)),
                )
            out = [
                PolicySection(h.ref_id, kb[h.ref_id].heading, kb[h.ref_id].text, h.rerank_score if h.rerank_score is not None else h.score, "hybrid")
                for h in hits if h.ref_id in kb and h.ref_id not in skip
            ][:limit]
            if out:
                return out
        except Exception:
            log.exception("policy_retrieval_failed")
    return keyword_sections(kb.values(), query, limit, skip)
