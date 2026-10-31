"""
Centralised settings for Toko Marcell API.

One ``Settings`` instance, cached via ``get_settings()``.  Environment variables
override everything (pydantic-settings default), and the hand-written ``.env``
loader in ``agent.py`` is replaced by ``env_file`` pointing at ``../.env`` and
``.env`` (repo root first, then api/).

Every ``os.environ.get(...)`` that was scattered across main/agent/agent_tools/
search/reranker now reads from this object instead.
"""

import logging
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """All env-var knobs, with the same names the deployment already uses."""

    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Database ─────────────────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql://toko:toko@localhost:5432/toko"
    DB_POOL_MIN: int = 1
    DB_POOL_MAX: int = 5

    # ── CORS ─────────────────────────────────────────────────────────────────
    CORS_ORIGINS: str = "http://localhost:3000,https://toko-marcell.vercel.app"

    # ── Admin ────────────────────────────────────────────────────────────────
    ADMIN_TOKEN: str = ""
    TRUST_PROXY: bool = False

    # ── Admin authentication (Argon2id + dual JWT) ─────────────────────────
    JWT_SECRET_KEY: str = "toko-marcell-admin-secret-change-in-prod"  # noqa: S105
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # ── Guardrail ───────────────────────────────────────────────────────────
    GUARDRAIL_LLM_MODE: str = "always"

    # ── Unit economics (USD per 1M tokens) ──────────────────────────────────
    LLM_INPUT_COST_PER_MILLION: float = 0.15
    LLM_OUTPUT_COST_PER_MILLION: float = 0.60

    # ── LLM — OpenRouter / Mistral (preferred, OpenAI-compatible) ───────────
    OPENROUTER_API_KEY: str = ""
    # Direct Mistral AI access (https://api.mistral.ai/v1) — used when
    # LLM_BASE_URL points at Mistral or as a key fallback for OpenRouter.
    MISTRAL_API_KEY: str = ""
    LLM_MODEL: str = "openrouter/free"
    LLM_BASE_URL: str = "https://openrouter.ai/api/v1"
    LLM_FALLBACK_MODELS: str = ""
    LLM_SITE_URL: str = "https://toko-marcell.vercel.app"

    # ── LLM — Gemini (fallback) ─────────────────────────────────────────────
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-3.5-flash-lite"
    GOOGLE_API_KEY: str = ""  # alias supported by the old code

    # ── Feature flags (memory-constrained deployments) ──────────────────────
    ENABLE_RERANKER: bool = False
    # Dense retrieval uses a vocabulary-pruned multilingual ONNX model:
    # 113,070 Latin/Indonesian/English tokens instead of 250,002, a 124 MB
    # ONNX file instead of 225 MB, and ~380 MB RSS with one ONNX thread and
    # the CPU memory arena disabled. That fits Render's 512 MB free tier, so
    # hybrid dense + lexical search stays enabled by default. Set false only
    # for hosts where even the pruned model does not fit; BM25 + query
    # rewriting continue to work without embeddings.
    ENABLE_DENSE_SEARCH: bool = True

    # ── Text embedding model switch (multilingual ID/EN default) ────────────
    # 100% numerically equivalent to sentence-transformers/paraphrase-
    # multilingual-MiniLM-L12-v2; only the unused non-Latin vocabulary rows
    # (Cyrillic, Chinese, Arabic, Devanagari) were pruned. Hosted on Hugging
    # Face and loaded through fastembed's PooledEmbedding.
    TEXT_EMBED_MODEL: str = "Marcell-Kristianto/paraphrase-multilingual-MiniLM-L12-v2-id-en-onnx"
    TEXT_EMBED_COLUMN: str = "embedding_ml"

    # ── Model paths / caches ────────────────────────────────────────────────
    FASTEMBED_CACHE_DIR: str = "/tmp/fastembed_cache"
    HF_HUB_CACHE: str = "/tmp/hf_cache"

    # ── Rate limiting ───────────────────────────────────────────────────────
    COPILOT_RATE_LIMIT_PER_MIN: int = 10
    COPILOT_RATE_LIMIT_PER_DAY: int = 100

    # ── Logging ─────────────────────────────────────────────────────────────
    LOG_LEVEL: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the one shared ``Settings`` object (cached)."""
    return Settings()
