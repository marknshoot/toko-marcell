"""
Tests for the copilot rate limiter.

Uses tiny limits to verify blocking behaviour without waiting real minutes.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

from rate_limiter import SlidingWindowLimiter

BASE_URL = "http://localhost:8001"


# ── Unit tests (no server needed) ───────────────────────────────────────────

def test_sliding_window_allows_under_limit():
    lim = SlidingWindowLimiter(window_seconds=60, max_requests=3)
    for _ in range(3):
        allowed, remaining, retry = lim.is_allowed("testip")
        assert allowed is True
    # 4th should be blocked
    allowed, remaining, retry = lim.is_allowed("testip")
    assert allowed is False
    assert retry > 0


def test_sliding_window_isolates_keys():
    lim = SlidingWindowLimiter(window_seconds=60, max_requests=1)
    assert lim.is_allowed("ip1")[0] is True
    assert lim.is_allowed("ip2")[0] is True
    assert lim.is_allowed("ip1")[0] is False  # ip1 already hit limit


# ── Integration test (needs running API) ─────────────────────────────────────

def _copilot_request():
    """Send a minimal copilot chat request."""
    url = f"{BASE_URL}/copilot/chat"
    data = json.dumps({
        "session_id": "test_rate_limit_sess",
        "messages": [{"role": "user", "content": "Halo"}],
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


@pytest.mark.skipif(
    os.environ.get("COPILOT_RATE_LIMIT_PER_MIN", "10") != "2",
    reason="Rate limit integration test requires COPILOT_RATE_LIMIT_PER_MIN=2",
)
def test_copilot_rate_limit_429():
    """With COPILOT_RATE_LIMIT_PER_MIN=2, the 3rd request should get 429."""
    statuses = []
    for _ in range(3):
        statuses.append(_copilot_request())
    assert 429 in statuses, f"Expected a 429 in {statuses}"
