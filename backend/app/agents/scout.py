"""Scout agent: exact scope resolution (structured filters) plus hybrid retrieval evidence (RAG)."""

from __future__ import annotations

import asyncio

from app.agents.base import AgentContext, RunState
from app.core.logging import get_logger
from app.services.analysis import resolve_scope
from app.services.retrieval.hybrid import SearchFilters, SearchHit, hybrid_search

log = get_logger(__name__)


async def semantic_scope_check(session, embedder, query: str, candidate_ids: list[str], scope_ids: set[str]) -> dict:
    """Retrieve-then-verify: let hybrid search alone propose the N most relevant in-flight orders (N = size of the
    exact scope, max 200) and verify them against the exact structured scope. Recall shows how much semantic search
    alone would find, which is the measured reason scope is decided by exact filters."""
    n = min(len(scope_ids), 200)
    if n == 0 or not candidate_ids:
        return {"n": 0, "recall": None, "precision": None}
    hits = await hybrid_search(session, embedder, query, SearchFilters(doc_types=["order"], ref_ids=candidate_ids),
                               limit=n, k=max(200, n))
    found = {h.ref_id for h in hits}
    verified = found & scope_ids
    return {
        "n": n,
        "retrieved": len(found),
        "verified": len(verified),
        "recall": round(len(verified) / n, 3),
        "precision": round(len(verified) / len(found), 3) if found else None,
    }


def _hit(h: SearchHit) -> dict:
    return {
        "doc_type": h.doc_type, "ref_id": h.ref_id, "title": h.title, "snippet": h.snippet[:240],
        "score": h.score, "dense_rank": h.dense_rank, "sparse_rank": h.sparse_rank, "rerank_score": h.rerank_score,
    }


class ScoutAgent:
    name = "scout"
    label = "Finding impacted sellers, orders and SKUs"

    async def run(self, state: RunState, ctx: AgentContext) -> None:
        spec = state.spec
        assert spec is not None
        scope = await asyncio.to_thread(resolve_scope, spec, ctx.ref)
        state.scope = scope
        for w in scope.warnings:
            await ctx.emitter.emit(self.name, "warning", w, level="warning")
        for res in scope.resolutions:
            labels = ", ".join(f"{m.label} ({m.via})" for m in res.matches) or "no match"
            await ctx.emitter.emit(self.name, "info", f"Product term '{res.term}' resolved to: {labels}.")

        matched = scope.matched
        order_ids = list(matched)
        seller_ids = sorted({m.line.seller_id for ms in matched.values() for m in ms})
        product_ids = sorted({m.line.product_id for ms in matched.values() for m in ms})
        await ctx.emitter.emit(
            self.name, "result",
            f"{len(order_ids)} active orders, {len(seller_ids)} sellers and {len(product_ids)} SKUs fall inside the "
            "disruption's scope (exact structured filters).",
            {"orders": len(order_ids), "sellers": len(seller_ids), "skus": len(product_ids)},
        )

        terms = " ".join(spec.category_terms)
        queries = [
            ("Most relevant impacted orders", f"{spec.title} {terms}".strip(), ["order"], order_ids),
            ("Heavy / bulky impacted products",
             f"heavy bulky oversized large items furniture home appliances {terms}".strip(), ["product"], product_ids),
            ("Most exposed impacted sellers", f"{spec.title} {terms}".strip(), ["seller"], seller_ids),
            ("Applicable policy", f"{spec.disruption_type} disruption delay mitigation reroute compensation {terms}".strip(),
             ["policy"], None),
        ]
        evidence: list[dict] = []
        if ctx.session_factory is not None:
            for purpose, query, types, ref_ids in queries:
                try:
                    async with ctx.session_factory() as s:
                        hits = await hybrid_search(
                            s, ctx.embedder, query, SearchFilters(doc_types=types, ref_ids=ref_ids), limit=5,
                            reranker=ctx.reranker, rerank_depth=ctx.settings.rerank_depth,
                        )
                    evidence.append({"purpose": purpose, "query": query, "doc_types": types,
                                     "restricted_to_scope": ref_ids is not None, "hits": [_hit(h) for h in hits]})
                except Exception as e:  # retrieval evidence must not fail the run
                    log.exception("retrieval_evidence_failed", purpose=purpose)
                    await ctx.emitter.emit(self.name, "warning", f"Retrieval for '{purpose}' failed: {e}", level="warning")
        scope_check: dict = {}
        if ctx.session_factory is not None and order_ids:
            try:
                async with ctx.session_factory() as s:
                    scope_check = await semantic_scope_check(
                        s, ctx.embedder, state.raw_query or spec.title, list(ctx.ref.lines_by_order), set(order_ids)
                    )
                if scope_check.get("recall") is not None:
                    await ctx.emitter.emit(
                        self.name, "info",
                        f"Retrieve-then-verify: semantic search alone recovers {scope_check['recall']:.0%} of the "
                        f"{scope_check['n']} in-scope orders in its top {scope_check['n']} (precision "
                        f"{scope_check['precision']:.0%}); exact filters guarantee the full scope.",
                        scope_check,
                    )
            except Exception:
                log.exception("semantic_scope_check_failed")
        state.retrieval = {
            "concepts": [r.model_dump() for r in scope.resolutions],
            "scope_check": scope_check,
            "queries": evidence,
            "mode": ("hybrid" if ctx.embedder is not None else "sparse") + ("+rerank" if ctx.reranker is not None else ""),
        }
        if evidence:
            await ctx.emitter.emit(
                self.name, "info",
                f"Hybrid retrieval (dense + full-text, RRF) ranked evidence for {len(evidence)} questions.",
                {"queries": [e["purpose"] for e in evidence]},
            )
