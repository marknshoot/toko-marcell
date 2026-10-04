"""Copilot LLM provider chain (design §8).

Builds the chat model used by ``create_agent`` with a provider fallback chain:

    OpenRouter (primary)
      → optional generic OpenAI-compatible endpoint
        (LLM_FALLBACK_BASE_URL / LLM_FALLBACK_API_KEY / LLM_FALLBACK_MODEL)
      → Google Gemini

wired together with ``.with_fallbacks(...)`` so a 429/5xx on one provider rolls
over to the next. Tool calling is required, so every provider is a tool-calling
chat model.

CI / tests: OpenRouter's free tier is ~50 requests/day, so tests must NOT hit a
real provider. Set ``COPILOT_FAKE_LLM=1`` (or pass ``fake=True``) to get a
deterministic in-process fake tool-calling model — no network, no key.
"""

import logging
import os
from typing import Any

from config import get_settings

logger = logging.getLogger(__name__)

_LLM_LOGGED = False


def _openrouter_model(temperature: float):
    from langchain_openai import ChatOpenAI

    settings = get_settings()
    return ChatOpenAI(
        model=settings.LLM_MODEL,
        api_key=settings.OPENROUTER_API_KEY,
        base_url=settings.LLM_BASE_URL,
        temperature=temperature,
        extra_body={"models": _openrouter_routing_list()},
        default_headers={
            "HTTP-Referer": settings.LLM_SITE_URL,
            "X-Title": "Toko Marcell AI Copilot",
        },
    )


def _openrouter_routing_list() -> list[str]:
    settings = get_settings()
    primary = settings.LLM_MODEL
    raw = settings.LLM_FALLBACK_MODELS
    fallbacks = [m.strip() for m in raw.split(",") if m.strip()]
    return [primary, *[m for m in fallbacks if m != primary]]


def _generic_openai_model(temperature: float):
    """Optional generic OpenAI-compatible fallback (OpenCode Zen, local router…)."""
    from langchain_openai import ChatOpenAI

    base_url = os.environ.get("LLM_FALLBACK_BASE_URL")
    api_key = os.environ.get("LLM_FALLBACK_API_KEY")
    model = os.environ.get("LLM_FALLBACK_MODEL")
    if not (base_url and model):
        return None
    return ChatOpenAI(
        model=model,
        api_key=api_key or "not-needed",
        base_url=base_url,
        temperature=temperature,
    )


def _gemini_model(temperature: float):
    settings = get_settings()
    key = settings.GEMINI_API_KEY or settings.GOOGLE_API_KEY
    if not key:
        return None
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(
        model=settings.GEMINI_MODEL,
        api_key=key,
        temperature=temperature,
    )


def fake_tool_calling_model(responses: list[Any] | None = None):
    """Deterministic in-process model for CI/tests (no network, no key).

    ``GenericFakeChatModel`` does not implement ``bind_tools``, which
    ``create_agent`` requires, so we wrap it in a subclass whose ``bind_tools``
    is a no-op (returns self). By default it returns a plain assistant message
    (no tool calls) — enough for agent plumbing tests. Pass ``responses`` (a list
    of ``AIMessage``, optionally carrying ``tool_calls``) to script behaviour.
    """
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    class _FakeToolCallingModel(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):  # noqa: ARG002
            return self

    if responses is None:
        responses = [AIMessage(content="Halo kak! Ada yang bisa mimin bantu cari hari ini?")]
    return _FakeToolCallingModel(messages=iter(responses))


def llm_available() -> bool:
    """True if any real provider is configured (OpenRouter / generic / Gemini)."""
    settings = get_settings()
    return bool(
        settings.OPENROUTER_API_KEY
        or os.environ.get("LLM_FALLBACK_BASE_URL")
        or settings.GEMINI_API_KEY
        or settings.GOOGLE_API_KEY
    )


def build_chat_model(temperature: float = 0.2, fake: bool | None = None):
    """Build the chat model with the provider fallback chain.

    Raises nothing here; the router decides the 503 when no provider is set.
    Returns ``None`` if no provider is configured and fake is not requested.
    """
    global _LLM_LOGGED

    if fake is None:
        fake = os.environ.get("COPILOT_FAKE_LLM") == "1"
    if fake:
        return fake_tool_calling_model()

    settings = get_settings()
    providers = []
    if settings.OPENROUTER_API_KEY:
        providers.append(("openrouter", _openrouter_model(temperature)))
    generic = _generic_openai_model(temperature)
    if generic is not None:
        providers.append(("generic", generic))
    gemini = _gemini_model(temperature)
    if gemini is not None:
        providers.append(("gemini", gemini))

    if not providers:
        return None

    if not _LLM_LOGGED:
        _LLM_LOGGED = True
        logger.info("[LLM] provider chain: %s", " -> ".join(name for name, _ in providers))

    primary = providers[0][1]
    rest = [m for _, m in providers[1:]]
    if rest:
        return primary.with_fallbacks(rest)
    return primary
