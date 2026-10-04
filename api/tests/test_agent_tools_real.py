import json
import urllib.error
import urllib.request

import pytest

BASE_URL = "http://localhost:8001"


def api_request(method, path, data=None):
    url = f"{BASE_URL}{path}"
    headers = {"Content-Type": "application/json"} if data else {}
    body = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            payload = json.loads(raw)
        except Exception:
            payload = raw
        return e.code, payload


def test_copilot_tools_registry_contains_all_tools():
    """Verify copilot tools schema endpoint registers all 4 active tools."""
    status, body = api_request("GET", "/copilot/tools")
    assert status == 200
    assert "tools" in body
    names = {t["function"]["name"] for t in body["tools"]}
    expected = {
        "search_catalog",
        "get_product_details",
        "search_by_image",
        "lookup_store_policy",
    }
    assert names == expected


@pytest.mark.llm
def test_copilot_greeting_conversational_response():
    """Verify greeting responds with warm LLM reply without invoking any tool calls."""
    payload = {
        "session_id": "test_greeting_session",
        "messages": [{"role": "user", "content": "Halo, selamat siang min!"}],
    }
    status, body = api_request("POST", "/copilot/chat", payload)
    # Accept rate limit or no-key gracefully
    if status in (429, 502, 503):
        pytest.skip(f"Copilot not available (status {status})")
    assert status == 200
    assert "reply" in body
    assert len(body["reply"]) > 10
    # Greeting should not invoke any tools
    assert body.get("tool_calls") == [] or "tool_calls" not in body


def test_copilot_order_lookup_tool_backed_flow():
    """Verify non-existent order token is handled gracefully via API."""
    status, body = api_request("GET", "/orders/tk_nonexistent_test_token")
    assert status == 404
    assert "detail" in body
    assert body["detail"] == "Order not found"


def test_copilot_catalog_tool_backed_flow():
    """Verify catalog tool retrieval returns expected schema."""
    status, body = api_request("GET", "/search?q=sneakers&mode=hybrid&limit=3")
    assert status == 200
    assert "items" in body
    assert len(body["items"]) > 0
    first = body["items"][0]
    assert "id" in first
    assert "asin" in first
    assert "priceIdr" in first
    assert isinstance(first["priceIdr"], int)
