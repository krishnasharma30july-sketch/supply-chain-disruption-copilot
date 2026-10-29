# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    EMBED_CACHE_DIR=/models \
    KAGGLEHUB_CACHE=/data/.kagglehub \
    HOME=/tmp

WORKDIR /app

COPY requirements.txt requirements-dev.txt ./
RUN pip install -r requirements.txt -r requirements-dev.txt

# Bake the multilingual embedding model and the cross-encoder reranker into the image (offline at runtime).
RUN mkdir -p /models && python -c "from fastembed import TextEmbedding; \
from fastembed.rerank.cross_encoder import TextCrossEncoder; \
TextEmbedding(model_name='sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2', cache_dir='/models'); \
TextCrossEncoder(model_name='Xenova/ms-marco-MiniLM-L-6-v2', cache_dir='/models')"

# Unprivileged runtime user. Writable state lives in /data (a named volume); the code and models are read-only to it.
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --home-dir /app --no-create-home app \
    && mkdir -p /data /app/samples && chown app:app /data /app/samples && chmod -R a+rX /models
COPY . .
RUN chmod -R a+rX /app
USER app
# The models are baked in: never contact the Hugging Face Hub at runtime (works air-gapped, no write attempts to /models).
ENV HF_HUB_OFFLINE=1 \
    RUFF_CACHE_DIR=/tmp/.ruff_cache

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
