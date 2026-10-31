"""Unit tests for the copilot LLM provider chain (api/copilot_llm.py).

No network calls: ``ChatOpenAI`` instances are constructed (which is offline)
but never invoked, so these run in the keyless CI subset.

They pin the Mistral AI compatibility contract: Mistral's OpenAI-compatible
endpoint validates the request body strictly and answers HTTP 422 ("Extra
inputs are not permitted") when OpenRouter-only knobs such as ``extra_body``
or the attribution headers are sent. Those knobs must therefore only be
attached when ``LLM_BASE_URL`` is actually OpenRouter.
"""

import logging
import sys
from pathlib import Path

import pytest

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

from config import get_settings

MISTRAL_BASE_URL = "https://api.mistral.ai/v1"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
SITE_URL = "https://toko-marcell.vercel.app"


@pytest.fixture(autouse=True)
def _fresh_settings(monkeypatch):
    """Isolate every test from the process env and any local ``.env`` file.

    Env vars take precedence over pydantic-settings' ``env_file`` entries, so
    setting them to ``""`` neutralises developer machines/CI runners that have
    real keys in ``.env``. Individual tests opt back in via ``monkeypatch``.
    """
    for var in (
        "LLM_BASE_URL",
        "LLM_MODEL",
        "LLM_FALLBACK_MODELS",
        "LLM_SITE_URL",
        "LLM_FALLBACK_API_KEY",
        "LLM_FALLBACK_MODEL",
        "OPENROUTER_API_KEY",
        "MISTRAL_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "COPILOT_FAKE_LLM",
    ):
        monkeypatch.setenv(var, "")
    monkeypatch.delenv("LLM_FALLBACK_BASE_URL", raising=False)
    monkeypatch.setenv("LLM_BASE_URL", MISTRAL_BASE_URL)
    monkeypatch.setenv("LLM_MODEL", "mistral-large-latest")
    monkeypatch.setenv("LLM_SITE_URL", SITE_URL)

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _chat_openai_class():
    return pytest.importorskip("langchain_openai").ChatOpenAI


def test_copilot_llm_mistral_compatibility(monkeypatch):
    """Mistral gets a clean body; OpenRouter keeps its routing extras."""
    from copilot_llm import _openrouter_model

    ChatOpenAI = _chat_openai_class()

    # ── Mistral: no OpenRouter-only extras (they trigger HTTP 422) ──────────
    monkeypatch.setenv("LLM_BASE_URL", MISTRAL_BASE_URL)
    monkeypatch.setenv("MISTRAL_API_KEY", "test_key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    get_settings.cache_clear()

    model = _openrouter_model(0.2)
    assert isinstance(model, ChatOpenAI)
    assert model.extra_body is None
    assert model.default_headers is None
    assert model.openai_api_base == MISTRAL_BASE_URL
    assert model.openai_api_key.get_secret_value() == "test_key"

    # ── OpenRouter: routing list + attribution headers are attached ─────────
    monkeypatch.setenv("LLM_BASE_URL", OPENROUTER_BASE_URL)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-key")
    monkeypatch.setenv("MISTRAL_API_KEY", "")
    monkeypatch.setenv("LLM_MODEL", "openrouter/free")
    get_settings.cache_clear()

    model = _openrouter_model(0.2)
    assert isinstance(model, ChatOpenAI)
    assert model.extra_body == {"models": ["openrouter/free"]}
    assert model.default_headers is not None
    assert model.default_headers["HTTP-Referer"] == SITE_URL
    assert model.default_headers["X-Title"] == "Toko Marcell AI Copilot"


def test_openrouter_routing_list_includes_fallbacks(monkeypatch):
    from copilot_llm import _openrouter_routing_list

    monkeypatch.setenv("LLM_MODEL", "primary/model")
    monkeypatch.setenv("LLM_FALLBACK_MODELS", "fallback/a, fallback/b, primary/model")
    get_settings.cache_clear()

    assert _openrouter_routing_list() == ["primary/model", "fallback/a", "fallback/b"]


def test_mistral_key_falls_back_when_openrouter_key_absent(monkeypatch):
    """A bare MISTRAL_API_KEY must still configure the primary provider."""
    from copilot_llm import _openrouter_model

    monkeypatch.setenv("LLM_BASE_URL", OPENROUTER_BASE_URL)
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("MISTRAL_API_KEY", "test_key")
    get_settings.cache_clear()

    model = _openrouter_model(0.2)
    assert model.openai_api_key.get_secret_value() == "test_key"


def test_llm_available_with_only_mistral_key(monkeypatch):
    from copilot_llm import llm_available

    monkeypatch.setenv("MISTRAL_API_KEY", "test_key")
    get_settings.cache_clear()
    assert llm_available() is True

    monkeypatch.setenv("MISTRAL_API_KEY", "")
    get_settings.cache_clear()
    assert llm_available() is False


def test_build_chat_model_labels_mistral_provider(monkeypatch, caplog):
    import copilot_llm
    from copilot_llm import build_chat_model

    monkeypatch.setenv("LLM_BASE_URL", MISTRAL_BASE_URL)
    monkeypatch.setenv("MISTRAL_API_KEY", "test_key")
    get_settings.cache_clear()
    monkeypatch.setattr(copilot_llm, "_LLM_LOGGED", False)

    with caplog.at_level(logging.INFO, logger="copilot_llm"):
        model = build_chat_model(temperature=0.2, fake=False)

    assert model is not None
    assert model.extra_body is None
    assert "provider chain: mistral" in caplog.text


def test_build_chat_model_returns_none_without_keys(monkeypatch):
    from copilot_llm import build_chat_model

    get_settings.cache_clear()
    assert build_chat_model(fake=False) is None
