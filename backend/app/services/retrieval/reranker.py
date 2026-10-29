"""Cross-encoder reranking of the fused (RRF) candidates.

Measured on the labelled category queries: hybrid + rerank reaches Recall@5 0.95 / MRR 0.94 (hybrid alone 0.91 / 0.92,
dense alone 0.94 / 0.95), and lifts "heavy bulky furniture" from 2 to 7 bulky orders in the top 10.
"""

from __future__ import annotations

import threading
from functools import lru_cache

from app.core.config import get_settings


class Reranker:
    def __init__(self, model_name: str, cache_dir: str):
        from fastembed.rerank.cross_encoder import TextCrossEncoder  # lazy: heavy import

        self.model_name = model_name
        self._model = TextCrossEncoder(model_name=model_name, cache_dir=cache_dir)
        self._lock = threading.Lock()

    def scores(self, query: str, documents: list[str]) -> list[float]:
        if not documents:
            return []
        with self._lock:
            return [float(s) for s in self._model.rerank(query, documents)]


@lru_cache
def get_reranker() -> Reranker:
    s = get_settings()
    return Reranker(s.rerank_model, str(s.embed_cache_dir))
