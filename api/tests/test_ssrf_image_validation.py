"""
Tests for SSRF-safe image input validation in agent_tools.py.

These are unit tests that do NOT require a running database or API server.
"""

import base64
import io
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Add api/ to path
API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

from agent_tools import _is_private_ip, validate_image_input

# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_tiny_png() -> bytes:
    """Create a minimal valid PNG image (1x1 red pixel)."""
    from PIL import Image

    buf = io.BytesIO()
    img = Image.new("RGB", (1, 1), color=(255, 0, 0))
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_data_url(image_bytes: bytes, mime: str = "png") -> str:
    b64 = base64.b64encode(image_bytes).decode()
    return f"data:image/{mime};base64,{b64}"


# ── File path rejection ──────────────────────────────────────────────────────

def test_file_path_rejected():
    """Local file paths must be rejected — no local file read."""
    with pytest.raises(ValueError, match="Invalid image reference"):
        validate_image_input(image_url_or_ref="/etc/passwd")

    with pytest.raises(ValueError, match="Invalid image reference"):
        validate_image_input(image_url_or_ref="./some/local/image.jpg")

    with pytest.raises(ValueError, match="Invalid image reference"):
        validate_image_input(image_url_or_ref="../../../etc/shadow")


# ── Metadata service IP rejection ────────────────────────────────────────────

def test_metadata_ip_rejected():
    """http://169.254.169.254 (AWS/GCP metadata) must be rejected."""
    with pytest.raises(ValueError, match="http://"):
        validate_image_input(image_url_or_ref="http://169.254.169.254/latest/meta-data/")


def test_private_ips_detected():
    """_is_private_ip must return True for private/loopback/link-local addresses."""
    assert _is_private_ip("127.0.0.1") is True
    assert _is_private_ip("10.0.0.1") is True
    assert _is_private_ip("192.168.1.1") is True
    assert _is_private_ip("169.254.169.254") is True
    assert _is_private_ip("::1") is True
    assert _is_private_ip("fe80::1") is True


# ── Non-allowlisted host rejection ──────────────────────────────────────────

def test_non_allowlisted_host_rejected():
    """HTTPS URLs to hosts not in the allowlist must be rejected."""
    with pytest.raises(ValueError, match="not in the image fetch allowlist"):
        validate_image_input(image_url_or_ref="https://evil.example.com/image.png")


def test_http_scheme_rejected():
    """Plain http:// must be rejected even for allowlisted hosts."""
    with pytest.raises(ValueError, match="Only https://"):
        validate_image_input(image_url_or_ref="http://images-na.ssl-images-amazon.com/img.jpg")


# ── Valid data URL accepted ──────────────────────────────────────────────────

def test_valid_data_url_accepted():
    """A valid data:image/png;base64,... URL with real image data must be accepted."""
    png_bytes = _make_tiny_png()
    data_url = _make_data_url(png_bytes, "png")

    result = validate_image_input(image_url_or_ref=data_url)
    assert isinstance(result, bytes)
    assert len(result) > 0
    assert result == png_bytes


def test_valid_raw_bytes_accepted():
    """Raw image bytes passed directly must be accepted."""
    png_bytes = _make_tiny_png()
    result = validate_image_input(image_bytes=png_bytes)
    assert result == png_bytes


def test_data_url_with_invalid_image_rejected():
    """A data URL whose decoded content is not a valid image must be rejected."""
    fake = base64.b64encode(b"this is not an image").decode()
    data_url = f"data:image/png;base64,{fake}"
    with pytest.raises(ValueError, match="not a valid image"):
        validate_image_input(image_url_or_ref=data_url)


def test_oversized_image_rejected():
    """Images exceeding 10 MB must be rejected."""
    big = b"\x00" * (10 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match="10 MB size limit"):
        validate_image_input(image_bytes=big)


def test_no_input_raises():
    """Calling with no image input must raise."""
    with pytest.raises(ValueError, match="No image data"):
        validate_image_input()


# ── DNS resolution with private IP (mocked) ─────────────────────────────────

def test_allowlisted_host_with_private_ip_rejected():
    """Even an allowlisted host must be rejected if it resolves to a private IP."""
    fake_resolve = [(2, 1, 6, "", ("127.0.0.1", 443))]
    with patch("agent_tools.socket.getaddrinfo", return_value=fake_resolve), \
         patch.dict(os.environ, {"IMAGE_FETCH_ALLOWED_HOSTS": "evil.test"}):
        with pytest.raises(ValueError, match="private/reserved IP"):
            validate_image_input(image_url_or_ref="https://evil.test/image.png")
