from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Typed runtime settings. Values come from environment variables (and a local .env file)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: str = "local"
    log_level: str = "INFO"

    database_url: str = "postgresql+psycopg://sc:sc@localhost:5432/sc"
    redis_url: str = "redis://localhost:6379/0"
    api_key: str | None = None  # service credential: X-API-Key grants the admin role (scripts, CI)
    cors_origins: list[str] = ["http://localhost:8080", "http://localhost:5173"]

    # Login: JWT bearer tokens, roles analyst (run, edit, draft) < approver (approve/reject/send) < admin.
    auth_enabled: bool = True
    jwt_secret: str | None = None  # when unset, a random secret is generated once and kept in DATA_DIR
    jwt_ttl_minutes: int = Field(default=720, ge=5, le=10_080)
    demo_users: bool = False  # seed the demo analyst / approver / admin accounts when they do not exist
    # Self-service sign-up creates analyst accounts only (approver/admin are granted by an admin). Off by default.
    allow_signup: bool = False
    signup_allowed_domains: list[str] = []  # e.g. ["company.com"]; empty = any domain

    # Outbound email for approved communications (Mailpit in the local stack). Empty host = do not send.
    smtp_host: str | None = None
    smtp_port: int = 1025
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_starttls: bool = False
    smtp_from: str = "Olist Customer Care <care@olist.example>"
    smtp_timeout_s: float = Field(default=10, gt=0)

    metrics_enabled: bool = True
    worker_metrics_port: int = Field(default=9100, ge=0, le=65535)  # 0 disables the worker's /metrics server

    data_dir: Path = BACKEND_ROOT.parent / "data"
    import_dir: Path | None = None  # optional read-only folder with the Olist CSVs (used before downloading)
    config_dir: Path = BACKEND_ROOT / "config"
    kb_dir: Path = BACKEND_ROOT / "knowledge_base"
    prompts_dir: Path = BACKEND_ROOT / "app" / "llm" / "prompts"

    as_of: datetime = datetime(2018, 8, 1)
    corpus_size: int = Field(default=10_000, ge=1_000, le=100_000)

    embed_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embed_dim: int = 384
    embed_cache_dir: Path = BACKEND_ROOT.parent / ".models"
    rerank_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    rerank_enabled: bool = True
    rerank_depth: int = Field(default=30, ge=5, le=200)  # fused candidates re-scored by the cross-encoder

    # Primary provider. The other one (when its key is set) is tried after the primary's models fail or run out of quota.
    llm_provider: Literal["gemini", "groq", "local", "offline"] = "gemini"
    gemini_api_key: str | None = None
    groq_api_key: str | None = None
    groq_base_url: str = "https://api.groq.com/openai/v1"
    # Groq models in preference order (OpenAI-compatible chat API, JSON mode). Each model has its own limits.
    groq_models: list[str] = ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]
    groq_rpm: int = Field(default=25, ge=1)
    # Self-hosted last resort: Qwen3-4B (4-bit AWQ) served by vLLM (PagedAttention, KV cache, prefix caching, continuous
    # batching) from the optional `local-llm` compose profile. No API key. It is tried only after every API model failed.
    local_llm_enabled: bool = False
    local_llm_base_url: str = "http://vllm:8000/v1"
    local_llm_model: str = "qwen3-4b"  # the --served-model-name of the vLLM container
    local_llm_api_key: str = "local"
    local_llm_rpm: int = Field(default=600, ge=1)
    local_llm_timeout_s: float = Field(default=180, gt=0)  # a 6 GB GPU generates ~30 tokens/s, so replies take a while
    local_llm_max_output_tokens: int = Field(default=3072, ge=256, le=8192)  # prompt + reply must fit the 8k context
    llm_model_default: str = "gemini-3.5-flash"
    llm_model_intake: str | None = None
    llm_model_email: str | None = None
    llm_model_summary: str | None = None
    llm_model_adjudicator: str | None = None
    # Tried in order when a role's model fails or its (free-tier) daily quota is exhausted. Each model has its own quota.
    llm_fallback_models: list[str] = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
    llm_request_timeout_s: float = Field(default=60, gt=0)
    llm_max_concurrency: int = Field(default=2, ge=1, le=32)
    llm_rpm: int = Field(default=10, ge=1)
    llm_max_retries: int = Field(default=3, ge=0, le=6)
    llm_temperature: float = Field(default=0.2, ge=0, le=1)
    llm_breaker_threshold: int = Field(default=3, ge=1)
    llm_breaker_cooldown_s: float = Field(default=120, gt=0)  # an open breaker retries after this long

    max_llm_emails: int = Field(default=25, ge=0, le=500)  # written by the LLM inside the run (the rest follow below)
    max_bulk_drafts: int = Field(default=10, ge=1, le=200)  # per "Draft with AI" request (protects the daily quota)
    email_batch_size: int = Field(default=5, ge=1, le=10)
    max_adjudications: int = Field(default=20, ge=0, le=200)
    # Most severe flagged cases investigated by the tool-using agent (up to agent_max_steps LLM calls each; 0 disables).
    max_agent_cases: int = Field(default=6, ge=0, le=20)
    agent_max_steps: int = Field(default=3, ge=2, le=6)
    adjudication_batch_size: int = Field(default=8, ge=1, le=20)

    # AI coverage for every customer: after the run, template emails are upgraded to LLM drafts in the background
    # (severity order, fact-checked, never touching approved/edited emails) until this many LLM calls are spent.
    ai_emails_for_everyone: bool = True
    email_upgrade_max_calls: int = Field(default=150, ge=0, le=5000)
    email_upgrade_chunk_batches: int = Field(default=6, ge=1, le=50)  # batches per worker job, so runs can interleave
    # Tone and empathy review of AI-written emails (advisory: it never blocks an email, weak drafts get one rewrite).
    tone_review_enabled: bool = True
    tone_batch_size: int = Field(default=8, ge=1, le=20)
    tone_min_score: int = Field(default=4, ge=1, le=5)  # a draft scoring below this on warmth or clarity is rewritten once
    # "Most exposed impacted sellers" evidence: share of the order that comes from exact exposure (affected orders and value)
    # against search relevance. 0 = search only.
    evidence_exposure_weight: float = Field(default=0.95, ge=0, le=1)
    # Outreach drafts for people: call briefs for the care team and notices for the most exposed sellers.
    max_call_briefs: int = Field(default=10, ge=0, le=100)
    max_seller_notices: int = Field(default=5, ge=0, le=50)

    @property
    def llm_enabled(self) -> bool:
        return self.llm_provider != "offline" and bool(
            self.gemini_api_key or self.groq_api_key or self.local_llm_enabled
        )

    def model_for(self, role: str) -> str:
        value = getattr(self, f"llm_model_{role}", None)
        return value or self.llm_model_default

    def llm_chain(self, role: str) -> list[tuple[str, str]]:
        """(provider, model) pairs in the order they are tried. A provider without a key is left out; the local model is always last."""
        by_provider = {
            "gemini": [("gemini", m) for m in [self.model_for(role), *self.llm_fallback_models]] if self.gemini_api_key else [],
            "groq": [("groq", m) for m in self.groq_models] if self.groq_api_key else [],
            "local": [("local", self.local_llm_model)] if self.local_llm_enabled else [],
        }
        # The primary provider first, then the others in a fixed order.
        order = [self.llm_provider] if self.llm_provider in by_provider else []
        order += [p for p in ("gemini", "groq", "local") if p not in order]
        ordered = [pair for p in order for pair in by_provider[p]]
        seen: set[tuple[str, str]] = set()
        out: list[tuple[str, str]] = []
        for pair in ordered:
            if pair[1] and pair not in seen:
                seen.add(pair)
                out.append(pair)
        return out


@lru_cache
def get_settings() -> Settings:
    return Settings()
