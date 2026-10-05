import json
import urllib.error
import urllib.request

BASE_URL = "http://localhost:8001"
VALID_SESSION = "sess-test12345"


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


# ── Events Telemetry ─────────────────────────────────────────────────────────

def test_events_all_valid_types():
    event_types = [
        ("view_product", {"asin": "B000YXC2LI", "price_idr": 494700}),
        ("search", {"query": "jeans", "results_count": 25}),
        ("add_to_cart", {"asin": "B000YXC2LI", "qty": 1, "price_idr": 494700}),
        ("checkout_start", {"qty": 1, "price_idr": 494700}),
        ("purchase_mock", {"qty": 1, "price_idr": 494700}),
    ]

    for event_type, extra in event_types:
        payload = {"event_type": event_type, "session_id": VALID_SESSION, **extra}
        status, body = api_request("POST", "/events", payload)
        assert status == 201
        assert body["event_type"] == event_type
        assert "id" in body


def test_events_invalid_type():
    payload = {"event_type": "invalid_event", "session_id": VALID_SESSION}
    status, _ = api_request("POST", "/events", payload)
    assert status == 422


def test_events_session_id_bounds():
    # Session ID too short (< 8 chars)
    payload = {"event_type": "view_product", "session_id": "abc"}
    status, _ = api_request("POST", "/events", payload)
    assert status == 422

    # Session ID too long (> 64 chars)
    payload = {"event_type": "view_product", "session_id": "a" * 65}
    status, _ = api_request("POST", "/events", payload)
    assert status == 422


def test_events_qty_and_price_bounds():
    # Qty < 1
    payload = {"event_type": "add_to_cart", "session_id": VALID_SESSION, "qty": 0}
    status, _ = api_request("POST", "/events", payload)
    assert status == 422

    # Qty > 99
    payload = {"event_type": "add_to_cart", "session_id": VALID_SESSION, "qty": 100}
    status, _ = api_request("POST", "/events", payload)
    assert status == 422

    # Negative price
    payload = {"event_type": "add_to_cart", "session_id": VALID_SESSION, "price_idr": -100}
    status, _ = api_request("POST", "/events", payload)
    assert status == 422


def test_events_summary():
    status, body = api_request("GET", "/events/summary")
    assert status == 200
    assert "rates" in body
    assert "sessions_by_step" in body
    assert "search" in body
    assert "caveats" in body
    assert isinstance(body["caveats"], list)


def test_events_summary_parameter_validation():
    # since_days < 1
    status, _ = api_request("GET", "/events/summary?since_days=0")
    assert status == 422

    # since_days > 365
    status, _ = api_request("GET", "/events/summary?since_days=366")
    assert status == 422


# ── Checkout Confirm & Orders (B5) ──────────────────────────────────────────

def test_checkout_confirm_happy_path():
    # Product 1 is Levi's Men's 501 Original-Fit Jean (priceIdr = 494700)
    asin = "B000YXC2LI"
    payload = {
        "session_id": VALID_SESSION,
        "items": [{"asin": asin, "qty": 2}],
    }
    status, body = api_request("POST", "/checkout/confirm", payload)
    assert status == 201
    assert body["status"] == "paid"
    assert body["item_count"] == 2
    assert body["total_idr"] == 494700 * 2
    assert "token" in body
    assert len(body["items"]) == 1
    assert body["items"][0]["asin"] == asin

    # Read the order back from /orders/{token}
    token = body["token"]
    status_order, order = api_request("GET", f"/orders/{token}")
    assert status_order == 200
    assert order["token"] == token
    assert order["status"] == "paid"
    assert order["totalIdr"] == 494700 * 2
    assert order["itemCount"] == 2
    assert len(order["items"]) == 1
    assert order["items"][0]["unitPriceIdr"] == 494700


def test_checkout_confirm_price_tampering_rejected():
    # Security check: Client attempts to supply price_idr or total
    payload = {
        "session_id": VALID_SESSION,
        "items": [{"asin": "B000YXC2LI", "qty": 1, "price_idr": 100}],
    }
    status, _ = api_request("POST", "/checkout/confirm", payload)
    assert status == 422  # extra="forbid" rejects client price

    payload_total = {
        "session_id": VALID_SESSION,
        "total_idr": 100,
        "items": [{"asin": "B000YXC2LI", "qty": 1}],
    }
    status, _ = api_request("POST", "/checkout/confirm", payload_total)
    assert status == 422


def test_checkout_confirm_unknown_asin():
    payload = {
        "session_id": VALID_SESSION,
        "items": [{"asin": "UNKNOWN_ASIN_XYZ", "qty": 1}],
    }
    status, body = api_request("POST", "/checkout/confirm", payload)
    assert status == 400
    assert "unknown asin(s)" in body["detail"]


def test_checkout_confirm_empty_cart():
    payload = {
        "session_id": VALID_SESSION,
        "items": [],
    }
    status, _ = api_request("POST", "/checkout/confirm", payload)
    assert status == 422


def test_checkout_confirm_missing_session_id():
    payload = {
        "items": [{"asin": "B000YXC2LI", "qty": 1}],
    }
    status, _ = api_request("POST", "/checkout/confirm", payload)
    assert status == 422


def test_checkout_confirm_invalid_qty():
    # Qty 0
    payload = {
        "session_id": VALID_SESSION,
        "items": [{"asin": "B000YXC2LI", "qty": 0}],
    }
    status, _ = api_request("POST", "/checkout/confirm", payload)
    assert status == 422

    # Qty > 99
    payload = {
        "session_id": VALID_SESSION,
        "items": [{"asin": "B000YXC2LI", "qty": 100}],
    }
    status, _ = api_request("POST", "/checkout/confirm", payload)
    assert status == 422


def test_orders_not_found():
    status, body = api_request("GET", "/orders/non_existent_token_12345")
    assert status == 404
    assert body["detail"] == "Order not found"


def test_checkout_confirm_duplicate_asins_aggregated():
    # If client sends duplicate ASIN entries, quantities must be aggregated, not overwritten
    payload = {
        "session_id": VALID_SESSION,
        "items": [
            {"asin": "B000YXC2LI", "qty": 1},
            {"asin": "B000YXC2LI", "qty": 2},
        ],
    }
    status, body = api_request("POST", "/checkout/confirm", payload)
    assert status == 201
    assert body["item_count"] == 3
    assert body["total_idr"] == 494700 * 3
    assert len(body["items"]) == 1
    assert body["items"][0]["qty"] == 3

