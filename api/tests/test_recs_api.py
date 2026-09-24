import json
import urllib.parse
import urllib.request
import urllib.error
import pytest

BASE_URL = "http://localhost:8001"


def api_request(method, path, data=None):
    url = f"{BASE_URL}{path}"
    headers = {"Content-Type": "application/json"} if data else {}
    body = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            payload = json.loads(raw)
        except Exception:
            payload = raw
        return e.code, payload


# ── Recommendations API & Edge Cases (M7 / M8 / M9) ──────────────────────────

def test_recs_popular_default():
    status, body = api_request("GET", "/recs/popular?limit=5")
    assert status == 200
    assert body["count"] == 5
    assert len(body["items"]) == 5
    assert body["department"] == "All"
    assert body["strategy"] == "popularity_baseline"
    # Ensure bestselling items have ratings and positive price
    assert body["items"][0]["priceIdr"] > 0
    assert body["items"][0]["ratingCount"] > 0


def test_recs_popular_department_filter():
    status, body = api_request("GET", "/recs/popular?department=Women&limit=4")
    assert status == 200
    assert body["count"] == 4
    assert body["department"] == "Women"
    for item in body["items"]:
        assert item["department"] == "Women"


def test_recs_item_cf():
    # Levi's 501
    status, body = api_request("GET", "/recs/item/B000YXC2LI?limit=4")
    assert status == 200
    assert body["asin"] == "B000YXC2LI"
    assert body["strategy"] == "item_cf"
    assert len(body["items"]) == 4
    # Self-recommendation should be excluded
    asins = [it["asin"] for it in body["items"]]
    assert "B000YXC2LI" not in asins


def test_recs_item_nonexistent_fallback():
    # Unknown ASIN should gracefully fall back to category/popularity rather than error
    status, body = api_request("GET", "/recs/item/UNKNOWN_ASIN_999?limit=4")
    assert status == 200
    assert len(body["items"]) > 0
    assert body["strategy"] == "category_popularity_fallback"


def test_recs_session_cold_vs_warm():
    cold_session_id = f"test_session_cold_{urllib.parse.quote('abc123')}"
    # 1. Cold session
    status_cold, body_cold = api_request("GET", f"/recs/session?session_id={cold_session_id}&limit=4")
    assert status_cold == 200
    assert body_cold["strategy"] == "cold_popularity"
    assert len(body_cold["items"]) == 4

    # 2. Warm session: record a view_product event for Dickies Work Pant (B00028AVDG)
    warm_session_id = f"test_session_warm_{urllib.parse.quote('xyz789')}"
    status_evt, _ = api_request("POST", "/events", {
        "event_type": "view_product",
        "session_id": warm_session_id,
        "asin": "B00028AVDG",
    })
    assert status_evt == 201

    # Fetch recommendations for the warm session
    status_warm, body_warm = api_request("GET", f"/recs/session?session_id={warm_session_id}&limit=4")
    assert status_warm == 200
    assert body_warm["strategy"] == "session_cf"
    assert len(body_warm["items"]) == 4
    # The viewed product should be excluded from recommendations
    warm_asins = [it["asin"] for it in body_warm["items"]]
    assert "B00028AVDG" not in warm_asins


def test_recs_session_validation():
    # Empty session_id -> 422
    status_empty, _ = api_request("GET", "/recs/session?session_id=")
    assert status_empty == 422

    # session_id > 128 chars -> 422
    long_sess = "s" * 129
    status_long, _ = api_request("GET", f"/recs/session?session_id={long_sess}")
    assert status_long == 422
