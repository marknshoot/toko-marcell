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

    # ── LLM — OpenRouter (preferred) ────────────────────────────────────────
    OPENROUTER_API_KEY: str = ""
    LLM_MODEL: str = "openrouter/free"
    LLM_BASE_URL: str = "https://openrouter.ai/api/v1"
    LLM_FALLBACK_MODELS: str = ""
    LLM_SITE_URL: str = "https://toko-marcell.vercel.app"

    # ── LLM — Gemini (fallback) ─────────────────────────────────────────────
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-3.5-flash-lite"
    GOOGLE_API_KEY: str = ""  # alias supported by the old code

    # ── Feature flags (memory-constrained deployments) ──────────────────────
    ENABLE_TRIMODAL: bool = False
    ENABLE_RERANKER: bool = False

    # ── Model paths / caches ────────────────────────────────────────────────
    FASTEMBED_CACHE_DIR: str = "/tmp/fastembed_cache"
    HF_HUB_CACHE: str = "/tmp/hf_cache"
    HF_MODEL_REPO: str = "Marcell-Kristianto/toko-marcell-clip"
    CLIP_MODEL_DIR: str = ""
    CLIP_VISION_ONNX_PATH: str = ""
    CLIP_VISION_ONNX_PREFER: str = "int8"
    CLIP_TEXT_ONNX_PATH: str = ""

    # ── Image fetch (SSRF protection) ───────────────────────────────────────
    IMAGE_FETCH_ALLOWED_HOSTS: str = (
        "images-na.ssl-images-amazon.com,m.media-amazon.com"
    )

    # ── Rate limiting ───────────────────────────────────────────────────────
    COPILOT_RATE_LIMIT_PER_MIN: int = 10
    COPILOT_RATE_LIMIT_PER_DAY: int = 100

    # ── Logging ─────────────────────────────────────────────────────────────
    LOG_LEVEL: str = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the one shared ``Settings`` object (cached)."""
    return Settings()
