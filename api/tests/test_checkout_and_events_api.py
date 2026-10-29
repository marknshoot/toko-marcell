import json
import urllib.error
import urllib.request
import uuid

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
        ("copilot_message", {"query": "ada celana chino?"}),
        ("copilot_product_click", {"asin": "B000YXC2LI"}),
        ("copilot_add_to_cart", {"asin": "B000YXC2LI", "qty": 1, "price_idr": 494700}),
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
    # Phase 5 copilot funnel block
    assert "copilot" in body
    cp = body["copilot"]
    for k in ("messages", "product_clicks", "add_to_cart", "total_add_to_cart", "assisted_add_to_cart_rate"):
        assert k in cp
    # rate is null (no denominator) or a 0..1 float, never a fake 0
    r = cp["assisted_add_to_cart_rate"]
    assert r is None or (0.0 <= r <= 1.0)


def test_events_summary_copilot_assisted_rate():
    """After a copilot_add_to_cart + a plain add_to_cart, the assisted rate is a
    sane fraction and the copilot counts increment."""
    sid = "copilot-funnel-sess-01"
    api_request("POST", "/events", {"event_type": "add_to_cart", "session_id": sid, "asin": "B000YXC2LI", "qty": 1, "price_idr": 494700})
    api_request("POST", "/events", {"event_type": "copilot_add_to_cart", "session_id": sid, "asin": "B000YXC2LI", "qty": 1, "price_idr": 494700})
    api_request("POST", "/events", {"event_type": "copilot_message", "session_id": sid, "query": "ada chino?"})

    status, body = api_request("GET", "/events/summary?since_days=1")
    assert status == 200
    cp = body["copilot"]
    assert cp["add_to_cart"] >= 1
    assert cp["total_add_to_cart"] >= cp["add_to_cart"]
    assert cp["messages"] >= 1
    assert cp["assisted_add_to_cart_rate"] is not None
    assert 0.0 < cp["assisted_add_to_cart_rate"] <= 1.0


def test_events_summary_parameter_validation():
    # since_days < 1
    status, _ = api_request("GET", "/events/summary?since_days=0")
    assert status == 422

    # since_days > 365
    status, _ = api_request("GET", "/events/summary?since_days=366")
    assert status == 422


def test_events_summary_phase9_blocks():
    """Phase 9 adds revenue, cohorts and copilot_quality without removing keys."""
    status, body = api_request("GET", "/events/summary?since_days=1")
    assert status == 200

    for key in ("revenue", "cohorts", "copilot_quality"):
        assert key in body

    revenue = body["revenue"]
    for key in ("order_count", "revenue_idr", "aov_idr"):
        assert key in revenue
    assert isinstance(revenue["order_count"], int)
    assert isinstance(revenue["revenue_idr"], int)
    assert revenue["aov_idr"] is None or revenue["aov_idr"] >= 0

    cohorts = body["cohorts"]
    assert {"copilot", "non_copilot", "purchase_source", "caveat"} <= set(cohorts)
    for cohort in ("copilot", "non_copilot"):
        c = cohorts[cohort]
        for key in (
            "sessions",
            "sessions_with_add_to_cart",
            "add_to_cart_rate",
            "purchase_sessions",
            "purchase_rate",
        ):
            assert key in c
        assert 0.0 <= (c["add_to_cart_rate"] or 0.0) <= 1.0
    assert "correlational" in cohorts["caveat"].lower()
    assert "self-selected" in cohorts["caveat"].lower()

    quality = body["copilot_quality"]
    for key in (
        "turns",
        "unanswered_rate",
        "guardrail_rate",
        "avg_latency_ms",
        "p50_latency_ms",
        "p95_latency_ms",
        "thumbs_up",
        "thumbs_down",
        "thumbs_up_rate",
    ):
        assert key in quality
    assert quality["turns"] >= 0


def test_events_summary_revenue_ignores_purchase_mock_events():
    """Revenue/AOV are order-derived; a forged browser purchase event must not count."""
    before_status, before = api_request("GET", "/events/summary?since_days=1")
    assert before_status == 200

    payload = {
        "event_type": "purchase_mock",
        "session_id": f"forged-{uuid.uuid4().hex[:12]}",
        "qty": 1,
        "price_idr": 999_999_999,
    }
    status, _ = api_request("POST", "/events", payload)
    assert status == 201

    after_status, after = api_request("GET", "/events/summary?since_days=1")
    assert after_status == 200
    assert after["revenue"]["order_count"] == before["revenue"]["order_count"]
    assert after["revenue"]["revenue_idr"] == before["revenue"]["revenue_idr"]
    assert after["revenue"]["aov_idr"] == before["revenue"]["aov_idr"]


def test_events_summary_cohorts_and_copilot_quality():
    """A copilot message creates a copilot cohort and a real turn; orders join by session."""
    copilot_sid = f"cohort-copilot-{uuid.uuid4().hex[:10]}"
    other_sid = f"cohort-other-{uuid.uuid4().hex[:10]}"
    thread_id = f"phase9-{uuid.uuid4().hex[:10]}"

    api_request("POST", "/events", {"event_type": "copilot_message", "session_id": copilot_sid, "query": "halo"})
    api_request("POST", "/events", {"event_type": "add_to_cart", "session_id": copilot_sid, "asin": "B000YXC2LI", "qty": 1, "price_idr": 494700})
    api_request("POST", "/events", {"event_type": "add_to_cart", "session_id": other_sid, "asin": "B000YXC2LI", "qty": 1, "price_idr": 494700})

    # Greeting path is deterministic (no LLM) and still logs a copilot turn.
    status, chat = api_request(
        "POST", "/copilot/chat",
        {"thread_id": thread_id, "session_id": copilot_sid, "message": "Halo kak"},
    )
    assert status == 200
    assert chat["guardrail"] == "greeting"

    # Server-confirmed order in the copilot session, so cohort purchase join has data.
    status, order = api_request(
        "POST", "/checkout/confirm",
        {"session_id": copilot_sid, "items": [{"asin": "B000YXC2LI", "qty": 1}]},
    )
    assert status == 201
    assert order["status"] == "paid"

    status, body = api_request("GET", "/events/summary?since_days=1")
    assert status == 200

    copilot = body["cohorts"]["copilot"]
    non_copilot = body["cohorts"]["non_copilot"]
    assert body["cohorts"]["purchase_source"] == "orders"
    assert copilot["sessions"] >= 1
    assert copilot["sessions_with_add_to_cart"] >= 1
    assert copilot["purchase_sessions"] >= 1
    assert non_copilot["sessions"] >= 1
    assert copilot["add_to_cart_rate"] is not None
    assert non_copilot["add_to_cart_rate"] is not None

    quality = body["copilot_quality"]
    assert quality["turns"] >= 1
    assert quality["guardrail_rate"] is not None
    assert quality["avg_latency_ms"] is not None
    assert quality["p50_latency_ms"] is not None
    assert quality["p95_latency_ms"] is not None


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

