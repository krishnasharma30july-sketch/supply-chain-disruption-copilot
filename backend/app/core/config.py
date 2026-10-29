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

    llm_provider: Literal["gemini", "offline"] = "gemini"
    gemini_api_key: str | None = None
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

    max_llm_emails: int = Field(default=25, ge=0, le=500)
    max_bulk_drafts: int = Field(default=10, ge=1, le=200)  # per "Draft with AI" request (protects the daily quota)
    email_batch_size: int = Field(default=5, ge=1, le=10)
    max_adjudications: int = Field(default=20, ge=0, le=200)
    # Most severe flagged cases investigated by the tool-using agent (several LLM calls each; 0 disables).
    max_agent_cases: int = Field(default=2, ge=0, le=20)
    agent_max_steps: int = Field(default=3, ge=2, le=6)
    adjudication_batch_size: int = Field(default=8, ge=1, le=20)

    @property
    def llm_enabled(self) -> bool:
        return self.llm_provider == "gemini" and bool(self.gemini_api_key)

    def model_for(self, role: str) -> str:
        value = getattr(self, f"llm_model_{role}", None)
        return value or self.llm_model_default


@lru_cache
def get_settings() -> Settings:
    return Settings()
