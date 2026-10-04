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


# ── Health & Categories ──────────────────────────────────────────────────────

def test_health_check():
    status, body = api_request("GET", "/health")
    assert status == 200
    assert body == {"status": "ok"}


def test_categories_facets():
    status, body = api_request("GET", "/categories")
    assert status == 200
    assert "departments" in body
    assert "categories" in body
    
    # Must have departments with real counts
    dept_names = {d["name"] for d in body["departments"]}
    assert "Women" in dept_names
    assert "Men" in dept_names
    for dept in body["departments"]:
        assert dept["count"] > 0

    # Top categories should not be empty
    assert len(body["categories"]) > 0
    for cat in body["categories"]:
        assert cat["name"] != ""
        assert cat["count"] > 0


# ── Products Catalog & Pagination ─────────────────────────────────────────────

def test_products_default_pagination():
    status, body = api_request("GET", "/products")
    assert status == 200
    assert body["limit"] == 24
    assert body["offset"] == 0
    assert body["total"] == 4670
    assert len(body["items"]) == 24
    
    first = body["items"][0]
    expected_fields = {
        "id", "asin", "title", "brand", "priceUsd", "priceIdr",
        "department", "category", "categoryPath", "description",
        "features", "imageUrl", "avgRating", "ratingCount", "alsoBuy", "alsoView"
    }
    assert expected_fields.issubset(first.keys())
    assert isinstance(first["id"], int)
    assert isinstance(first["priceIdr"], int)


def test_products_pagination_boundaries():
    # Min limit
    status, body = api_request("GET", "/products?limit=1&offset=0")
    assert status == 200
    assert len(body["items"]) == 1

    # Offset pagination
    status1, body1 = api_request("GET", "/products?limit=5&offset=0")
    status2, body2 = api_request("GET", "/products?limit=5&offset=5")
    assert status1 == 200 and status2 == 200
    ids1 = [item["id"] for item in body1["items"]]
    ids2 = [item["id"] for item in body2["items"]]
    # Next page must have different products
    assert set(ids1).isdisjoint(set(ids2))


def test_products_invalid_pagination():
    # Limit too large (> 24)
    status, _ = api_request("GET", "/products?limit=25")
    assert status == 422

    # Limit <= 0
    status, _ = api_request("GET", "/products?limit=0")
    assert status == 422
    status, _ = api_request("GET", "/products?limit=-5")
    assert status == 422

    # Offset < 0
    status, _ = api_request("GET", "/products?offset=-1")
    assert status == 422


def test_products_filtering():
    # Filter by department
    status, body = api_request("GET", "/products?department=Men&limit=10")
    assert status == 200
    assert body["total"] > 0
    for item in body["items"]:
        assert item["department"] == "Men"

    # Filter by non-existent department
    status, body = api_request("GET", "/products?department=NonExistentDepartment")
    assert status == 200
    assert body["total"] == 0
    assert body["items"] == []


# ── Product Detail (PDP) ──────────────────────────────────────────────────────

def test_product_detail_success():
    # Retrieve product #1
    status, product = api_request("GET", "/products/1")
    assert status == 200
    assert product["id"] == 1
    assert product["asin"] != ""
    assert product["title"] != ""
    assert isinstance(product["priceIdr"], int)
    assert isinstance(product["categoryPath"], list)


def test_product_detail_not_found():
    # Product ID that does not exist
    status, body = api_request("GET", "/products/999999")
    assert status == 404
    assert body["detail"] == "Product not found"


def test_product_detail_invalid_id():
    # Non-integer product ID
    status, _ = api_request("GET", "/products/abc")
    assert status == 422

    # Float product ID
    status, _ = api_request("GET", "/products/1.5")
    assert status == 422
