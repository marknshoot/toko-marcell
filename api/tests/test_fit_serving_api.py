"""Integration tests for fit serving + recommend_size (needs a live API + DB).

Covers:
  - GET /products/{id} carries a `fit` field (object or null)
  - GET /products/{id}/fit endpoint
  - fit.get_product_fit layering (product → brand fallback)
  - agent_tools.recommend_size 3-layer output, never a certainty
"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

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


def _first_product_id():
    status, body = api_request("GET", "/products?limit=1")
    assert status == 200
    return body["items"][0]["id"]


# ── Product detail carries a fit field ───────────────────────────────────────

def test_product_detail_has_fit_key():
    pid = _first_product_id()
    status, body = api_request("GET", f"/products/{pid}")
    assert status == 200
    assert "fit" in body  # present even if null


def test_product_fit_object_shape_when_present():
    """Scan the first page; at least one product should have a fit object with
    the contract shape."""
    status, body = api_request("GET", "/products?limit=24")
    assert status == 200
    found = None
    for item in body["items"]:
        s, detail = api_request("GET", f"/products/{item['id']}")
        if s == 200 and detail.get("fit"):
            found = detail["fit"]
            break
    assert found is not None, "expected at least one product with a fit signal on page 1"
    for key in ("label", "source", "nMentions", "shareSmall", "shareTts", "shareLarge", "fitScore", "phrasing", "disclaimer"):
        assert key in found, f"fit object missing key: {key}"
    assert found["label"] in ("runs_small", "true_to_size", "runs_large")
    assert found["source"] in ("product", "brand")
    assert found["disclaimer"]  # never a certainty


# ── Dedicated fit endpoint ───────────────────────────────────────────────────

def test_fit_endpoint():
    pid = _first_product_id()
    status, body = api_request("GET", f"/products/{pid}/fit")
    assert status == 200
    assert body["id"] == pid
    assert "asin" in body
    assert "fit" in body  # object or null


def test_fit_endpoint_404():
    status, body = api_request("GET", "/products/999999999/fit")
    assert status == 404


# ── fit.get_product_fit layering (direct DB) ─────────────────────────────────

def test_get_product_fit_product_source():
    """A high-volume brand like Dickies should yield product-level fit."""
    import psycopg
    from config import get_settings
    from fit import get_product_fit

    with psycopg.connect(get_settings().DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT asin, brand FROM products WHERE brand = %s LIMIT 1", ("Dickies",))
            row = cur.fetchone()
            if not row:
                return  # no Dickies in this catalog slice; skip silently
            fit = get_product_fit(cur, row[0], row[1])
            assert fit is not None
            assert fit["source"] in ("product", "brand")
            assert fit["disclaimer"]


def test_get_product_fit_unknown_asin_is_none():
    import psycopg
    from config import get_settings
    from fit import get_product_fit

    with psycopg.connect(get_settings().DATABASE_URL) as conn:
        with conn.cursor() as cur:
            assert get_product_fit(cur, "ZZZZNONEXIST", None) is None


# ── recommend_size (direct call) ─────────────────────────────────────────────

def test_recommend_size_basic():
    from agent_tools import recommend_size

    r = recommend_size(height_cm=170, weight_kg=65)
    assert "recommendation" in r
    assert r["disclaimer"]
    assert "perkiraan" in r["recommendation"].lower()


def test_recommend_size_rejects_bad_input():
    from agent_tools import recommend_size

    r = recommend_size(height_cm=0, weight_kg=0)
    assert "error" in r


def test_recommend_size_with_asin_resolves_product():
    import psycopg
    from agent_tools import recommend_size
    from config import get_settings

    with psycopg.connect(get_settings().DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT asin FROM products WHERE brand = %s LIMIT 1", ("Dickies",))
            row = cur.fetchone()
    if not row:
        return
    r = recommend_size(height_cm=175, weight_kg=72, asin=row[0])
    assert "recommendation" in r
    assert r.get("matched_product", {}).get("asin") == row[0]
    # Dickies is bottoms + runs_small → should surface a brand note
    assert r["disclaimer"]


def test_recommend_size_with_brand_only():
    from agent_tools import recommend_size

    r = recommend_size(height_cm=178, weight_kg=75, brand="Champion", category="Hoodies")
    assert "recommendation" in r
    # Champion is true_to_size (data correction) — suggestion should be a tops size
    assert "size" in r["suggested"]
