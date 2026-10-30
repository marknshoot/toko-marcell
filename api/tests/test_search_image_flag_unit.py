"""Unit tests for the ENABLE_IMAGE_SEARCH feature flag.

The CLIP vision encoder (``Qdrant/clip-ViT-B-32-vision``) needs ~340 MB of ONNX
weights. On Render's 512 MB free tier loading it triggers an immediate OOM
SIGKILL, which surfaces to the browser as a dropped connection / "Failed to
fetch". The endpoint must therefore short-circuit with a clear 503 *before*
touching the upload or any model whenever the flag is disabled.

These tests are offline: no database, no fastembed download, no HTTP server.
"""

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

from config import Settings
from routers import search as search_router

DISABLED_DETAIL = "Visual search is disabled on the 512MB free-tier demo to protect server SLA."


class _UntouchableUpload:
    """Upload double that fails if the endpoint accesses it before the flag check."""

    def __getattr__(self, name):
        raise AssertionError(f"upload attribute {name!r} was read before the feature-flag check")


def _settings(enabled: bool) -> Settings:
    # Init kwargs outrank env vars, so an ambient ENABLE_IMAGE_SEARCH cannot leak in.
    return Settings(ENABLE_IMAGE_SEARCH=enabled)


def _call_search_by_image(monkeypatch, *, enabled: bool, file) -> None:
    monkeypatch.setattr(search_router, "get_settings", lambda: _settings(enabled))
    asyncio.run(search_router.search_by_image(file=file))


def test_search_by_image_raises_503_when_disabled(monkeypatch):
    with pytest.raises(HTTPException) as excinfo:
        _call_search_by_image(monkeypatch, enabled=False, file=_UntouchableUpload())

    assert excinfo.value.status_code == 503


def test_disabled_detail_is_clean_and_explains_free_tier(monkeypatch):
    with pytest.raises(HTTPException) as excinfo:
        _call_search_by_image(monkeypatch, enabled=False, file=_UntouchableUpload())

    detail = excinfo.value.detail
    assert detail == DISABLED_DETAIL
    assert "512MB" in detail
    assert "free-tier" in detail


def test_flag_defaults_to_enabled():
    assert Settings.model_fields["ENABLE_IMAGE_SEARCH"].default is True


def test_flag_enabled_still_validates_content_type(monkeypatch):
    """With the flag on, the guard is a no-op and normal validation continues."""

    class _TextUpload:
        content_type = "text/plain"

    with pytest.raises(HTTPException) as excinfo:
        _call_search_by_image(monkeypatch, enabled=True, file=_TextUpload())

    assert excinfo.value.status_code == 400
