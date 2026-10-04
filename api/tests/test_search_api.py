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


# ── Search API & Edge Cases ──────────────────────────────────────────────────

def test_search_basic_query():
    # Default search mode is hybrid
    status, body = api_request("GET", "/search?q=jeans&limit=5")
    assert status == 200
    assert body["mode"] == "hybrid"
    assert body["deduped"] is True
    assert body["total"] > 0
    assert len(body["items"]) == 5
    assert "score" in body["items"][0]
    assert body["items"][0]["score"] > 0


def test_search_modes():
    # Explicit bm25 mode
    status_bm25, body_bm25 = api_request("GET", "/search?q=jeans&limit=5&mode=bm25")
    assert status_bm25 == 200
    assert body_bm25["mode"] == "bm25"
    assert body_bm25["total"] > 0

    # Explicit vector mode
    status_vec, body_vec = api_request("GET", "/search?q=jeans&limit=5&mode=vector")
    assert status_vec == 200
    assert body_vec["mode"] == "vector"
    assert body_vec["total"] > 0

    # Explicit trimodal mode (BM25 + MiniLM + CLIP text-to-image RRF)
    status_tri, body_tri = api_request("GET", "/search?q=jeans&limit=5&mode=trimodal")
    assert status_tri == 200
    assert body_tri["mode"] == "trimodal"
    assert body_tri["total"] > 0


def test_search_semantic_query():
    # Query with semantic intent that pure keywords might miss or score poorly
    status, body = api_request("GET", "/search?q=raincoat+for+winter+storm&limit=5&mode=hybrid")
    assert status == 200
    assert body["total"] > 0
    # Top result should be outerwear / jacket / raincoat
    top_title = body["items"][0]["title"].lower()
    top_cat = (body["items"][0]["category"] or "").lower()
    assert any(term in top_title or term in top_cat for term in ["rain", "jacket", "coat", "parka", "storm"])


def test_search_sku_query():
    # Specific SKU: Levi's 501
    status, body = api_request("GET", "/search?q=levis+501&limit=5")
    assert status == 200
    assert body["total"] > 0
    # Top result should mention Levi's 501
    top_title = body["items"][0]["title"].lower()
    assert "501" in top_title or "levi" in top_title


def test_search_case_insensitivity():
    status_lower, body_lower = api_request("GET", "/search?q=sneakers&limit=5&mode=bm25")
    status_upper, body_upper = api_request("GET", "/search?q=SNEAKERS&limit=5&mode=bm25")
    assert status_lower == 200 and status_upper == 200
    assert body_lower["total"] == body_upper["total"]
    ids_lower = [item["id"] for item in body_lower["items"]]
    ids_upper = [item["id"] for item in body_upper["items"]]
    assert ids_lower == ids_upper


def test_search_singular_plural_equivalence():
    # Stemming should fold shoes and shoe identically
    status_sing, body_sing = api_request("GET", "/search?q=shoe&limit=5&mode=bm25")
    status_plur, body_plur = api_request("GET", "/search?q=shoes&limit=5&mode=bm25")
    assert status_sing == 200 and status_plur == 200
    assert body_sing["total"] == body_plur["total"]
    assert [item["id"] for item in body_sing["items"]] == [item["id"] for item in body_plur["items"]]


def test_search_stopwords_only():
    # Query with only stopwords should return 0 results gracefully, not crash or 500
    status, body = api_request("GET", "/search?q=the+and+with")
    assert status == 200
    assert body["total"] == 0
    assert body["items"] == []


def test_search_whitespace_query():
    # Whitespace query: %20%20
    status, body = api_request("GET", "/search?q=%20%20%20")
    assert status == 200
    assert body["total"] == 0
    assert body["items"] == []


def test_search_punctuation_and_symbols():
    # Punctuation and symbols stripped cleanly
    encoded = urllib.parse.quote("levi's & boots! @100%")
    status, body = api_request("GET", f"/search?q={encoded}")
    assert status == 200
    assert body["total"] > 0


def test_search_query_length_validation():
    # Empty query string -> 422
    status, _ = api_request("GET", "/search?q=")
    assert status == 422

    # Query exceeding max length (> 100 chars) -> 422
    long_q = "a" * 101
    status, _ = api_request("GET", f"/search?q={long_q}")
    assert status == 422


def test_search_department_filtering():
    status, body = api_request("GET", "/search?q=shoes&department=Women&limit=10")
    assert status == 200
    assert body["total"] > 0
    for item in body["items"]:
        assert item["department"] == "Women"


def test_search_reindex():
    status, body = api_request("POST", "/search/reindex")
    assert status == 202
    assert body["documents"] == 4670
    assert body["mode"] == "hybrid"


def test_search_rerank_default_off():
    """rerank defaults to false; result set size and mode stay the same."""
    status_plain, body_plain = api_request("GET", "/search?q=jeans&limit=10")
    assert status_plain == 200
    assert body_plain.get("reranked") is False

    # Explicit rerank=false must behave identically
    status_off, body_off = api_request("GET", "/search?q=jeans&limit=10&rerank=false")
    assert status_off == 200
    assert body_off.get("reranked") is False
    assert body_off["total"] == body_plain["total"]


def test_search_rerank_on():
    """rerank=true returns same total and the reranked flag is true (when ENABLE_RERANKER is set)."""
    status_off, body_off = api_request("GET", "/search?q=jeans&limit=10&rerank=false")
    status_on, body_on = api_request("GET", "/search?q=jeans&limit=10&rerank=true")
    assert status_on == 200
    # reranked flag should be True if ENABLE_RERANKER is set, otherwise False
    assert isinstance(body_on.get("reranked"), bool)
    # Total (number of matching products) should be equal regardless of reranking
    assert body_on["total"] == body_off["total"]
    # Result set size should be equal
    assert len(body_on["items"]) == len(body_off["items"])
