"""API tests for the deterministic sizing endpoints.

Runs against the live API (localhost:8001) and the seeded catalog.
"""

import json
import urllib.error
import urllib.request

BASE_URL = "http://localhost:8001"

SHOES_ASIN = "B002PHLVKE"       # Women / Shoes
LUGGAGE_ASIN = "B00DVIF8DW"     # Other / Luggage
LINGERIE_ASIN = "B000FOX9XQ"    # Women / Lingerie
TOPS_ASIN = "B00078ZTLU"        # Men / Shirts
ONE_SIZE_ASIN = "B005QG53AK"    # Women / Sunglasses


def api_request(method, path, data=None):
    body = json.dumps(data).encode("utf-8") if data is not None else None
    headers = {"Content-Type": "application/json"} if body else {}
    req = urllib.request.Request(f"{BASE_URL}{path}", data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            payload = json.loads(raw)
        except Exception:
            payload = raw
        return e.code, payload


def test_get_shoes_explains_foot_length_input():
    status, body = api_request("GET", f"/sizing/{SHOES_ASIN}")
    assert status == 200
    assert body["asin"] == SHOES_ASIN
    assert body["size_kind"] == "footwear"
    assert body["applicable"] is True
    assert body["required_inputs"] == ["foot_length_cm"]


def test_get_luggage_is_not_a_body_size_question():
    status, body = api_request("GET", f"/sizing/{LUGGAGE_ASIN}")
    assert status == 200
    assert body["size_kind"] == "dimensions"
    assert body["applicable"] is False
    assert body["required_inputs"] == []
    assert "dimensi" in body["reason"].lower()


def test_get_lingerie_is_honestly_unsupported():
    status, body = api_request("GET", f"/sizing/{LINGERIE_ASIN}")
    assert status == 200
    assert body["size_kind"] == "unsupported"
    assert body["applicable"] is False
    assert body["reason"]


def test_get_one_size_has_no_inputs():
    status, body = api_request("GET", f"/sizing/{ONE_SIZE_ASIN}")
    assert status == 200
    assert body["size_kind"] == "one_size"
    assert body["applicable"] is True
    assert body["required_inputs"] == []


def test_get_unknown_asin_404():
    status, _ = api_request("GET", "/sizing/NOPE-NOT-REAL")
    assert status == 404


def test_post_tops_recommendation():
    status, body = api_request(
        "POST",
        "/sizing/recommend",
        {"asin": TOPS_ASIN, "height_cm": 170, "weight_kg": 65},
    )
    assert status == 200
    assert body["applicable"] is True
    assert body["size_kind"] == "tops"
    assert body["suggested"]["size"] == "M"
    assert body["source"] == "store_estimate"
    assert body["disclaimer"]


def test_post_shoes_needs_only_foot_length():
    status, body = api_request(
        "POST",
        "/sizing/recommend",
        {"asin": SHOES_ASIN, "foot_length_cm": 24.0},
    )
    assert status == 200
    assert body["applicable"] is True
    assert body["size_kind"] == "footwear"
    assert body["suggested"]["eu"] > 0


def test_post_missing_required_inputs_returns_required_inputs():
    status, body = api_request("POST", "/sizing/recommend", {"asin": TOPS_ASIN})
    assert status == 200
    assert body["applicable"] is False
    assert body["required_inputs"] == ["height_cm", "weight_kg"]
    assert body["suggested"] is None


def test_post_luggage_does_not_guess():
    status, body = api_request(
        "POST",
        "/sizing/recommend",
        {"asin": LUGGAGE_ASIN, "height_cm": 170, "weight_kg": 65},
    )
    assert status == 200
    assert body["applicable"] is False
    assert body["size_kind"] == "dimensions"


def test_post_rejects_extra_fields():
    status, _ = api_request(
        "POST",
        "/sizing/recommend",
        {"asin": TOPS_ASIN, "height_cm": 170, "weight_kg": 65, "shoe_size": 42},
    )
    assert status == 422


def test_post_rejects_implausible_bounds():
    status, _ = api_request(
        "POST",
        "/sizing/recommend",
        {"asin": TOPS_ASIN, "height_cm": 10, "weight_kg": 65},
    )
    assert status == 422


def test_post_unknown_asin_404():
    status, _ = api_request(
        "POST",
        "/sizing/recommend",
        {"asin": "NOPE-NOT-REAL", "height_cm": 170, "weight_kg": 65},
    )
    assert status == 404
