"""Local multilingual sentence embeddings via fastembed (ONNX, CPU, no API key)."""

from __future__ import annotations

import threading
from functools import lru_cache

import numpy as np

from app.core.config import get_settings


class Embedder:
    def __init__(self, model_name: str, cache_dir: str):
        from fastembed import TextEmbedding  # imported lazily: heavy

        self.model_name = model_name
        self._model = TextEmbedding(model_name=model_name, cache_dir=cache_dir)
        self._lock = threading.Lock()

    def embed(self, texts: list[str], batch_size: int = 64) -> np.ndarray:
        if not texts:
            return np.zeros((0, get_settings().embed_dim), dtype=np.float32)
        with self._lock:
            vectors = list(self._model.embed(texts, batch_size=batch_size))
        return np.asarray(vectors, dtype=np.float32)


@lru_cache
def get_embedder() -> Embedder:
    s = get_settings()
    s.embed_cache_dir.mkdir(parents=True, exist_ok=True)
    return Embedder(s.embed_model, str(s.embed_cache_dir))


def to_pgvector(vec: np.ndarray) -> str:
    return "[" + ",".join(f"{float(x):.6f}" for x in vec) + "]"
