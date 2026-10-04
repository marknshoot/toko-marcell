import json
import urllib.error
import urllib.request

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


def test_get_product_reviews_valid():
    # Fetch first product to get a valid id and asin
    status, prod = api_request("GET", "/products/1")
    assert status == 200
    asin = prod["asin"]

    status, body = api_request("GET", "/products/1/reviews")
    assert status == 200
    assert body["asin"] == asin
    assert "total" in body
    assert "breakdown" in body
    assert isinstance(body["reviews"], list)

    # Check breakdown keys
    for k in ["1", "2", "3", "4", "5"]:
        assert k in body["breakdown"]

    if body["reviews"]:
        r = body["reviews"][0]
        assert "rating" in r
        assert "comment" in r
        assert "author" in r
        assert "verified" in r
        assert isinstance(r["verified"], bool)
        assert 1.0 <= r["rating"] <= 5.0


def test_get_asin_reviews_valid():
    status, prod = api_request("GET", "/products/1")
    assert status == 200
    asin = prod["asin"]

    status, body = api_request("GET", f"/reviews/{asin}")
    assert status == 200
    assert body["asin"] == asin
    assert isinstance(body["reviews"], list)


def test_get_reviews_limit():
    status, body = api_request("GET", "/products/1/reviews?limit=3")
    assert status == 200
    assert len(body["reviews"]) <= 3


def test_get_reviews_product_not_found():
    status, body = api_request("GET", "/products/9999999/reviews")
    assert status == 404

    status, body = api_request("GET", "/reviews/NON_EXISTENT_ASIN_XYZ")
    assert status == 404


def test_get_reviews_invalid_limit():
    status, _ = api_request("GET", "/products/1/reviews?limit=0")
    assert status == 422

    status, _ = api_request("GET", "/products/1/reviews?limit=100")
    assert status == 422
