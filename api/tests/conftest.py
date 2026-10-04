"""Shared fixtures and markers for the API test suite."""

import os

import pytest


def pytest_collection_modifyitems(config, items):
    """Auto-skip tests marked @pytest.mark.llm when no LLM key is available."""
    has_key = bool(
        os.environ.get("OPENROUTER_API_KEY")
        or os.environ.get("GEMINI_API_KEY")
    )
    if has_key:
        return
    skip_llm = pytest.mark.skip(reason="No LLM key (OPENROUTER_API_KEY / GEMINI_API_KEY) — skipping LLM tests")
    for item in items:
        if "llm" in item.keywords:
            item.add_marker(skip_llm)
