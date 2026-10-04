"""
Test Suite for Toko Marcell AI Copilot API (/copilot/chat & /copilot/tools).

Covers:
- Tool Registry Discovery
- Node 0 Guardrail & Greeting Fast-Path
- All 7 Core Shopping & Operations Use Cases (UC-1 through UC-7)
- Anti-Hallucination & Off-Topic Deflection
- Strict Currency (Rp) & Pricing Verification
"""

import json
import time
import urllib.error
import urllib.request

import pytest

BASE_URL = "http://localhost:8001"


def api_request(method, path, data=None):
    time.sleep(1.2)  # Prevent back-to-back LLM reservation spikes
    url = f"{BASE_URL}{path}"
    headers = {"Content-Type": "application/json"} if data else {}
    body = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            payload = json.loads(raw)
        except Exception:
            payload = raw
        return e.code, payload


# ── 1. Tool Registry Discovery ────────────────────────────────────────────────

def test_copilot_tools_schema():
    status, body = api_request("GET", "/copilot/tools")
    assert status == 200
    assert "tools" in body
    tool_names = [t["function"]["name"] for t in body["tools"]]
    assert "search_catalog" in tool_names
    assert "get_product_details" in tool_names
    assert "search_by_image" in tool_names
    assert "lookup_store_policy" in tool_names
    assert "get_order_status" not in tool_names  # no order tool: checkout is a simulation
    assert "get_product_reviews" not in tool_names


# ── 2. Node 0: Guardrail & Greeting Fast-Path ────────────────────────────────

@pytest.mark.llm
def test_copilot_greeting_fast_path():
    payload = {
        "session_id": "test_sess_01",
        "messages": [{"role": "user", "content": "Halo min"}],
    }
    status, body = api_request("POST", "/copilot/chat", payload)
    # Accept 200 (LLM available) or 429/502/503 (rate limited / no key)
    if status in (429, 502, 503):
        pytest.skip(f"Copilot not available (status {status})")
    assert status == 200
    assert "reply" in body
    assert "Halo kak!" in body["reply"] or "Toko Marcell" in body["reply"]
    # Fast path: zero tool calls
    assert len(body.get("tool_calls", [])) == 0


@pytest.mark.llm
def test_copilot_off_topic_guardrail():
    payload = {
        "session_id": "test_sess_02",
        "messages": [{"role": "user", "content": "Write Python code to solve fibonacci"}],
    }
    status, body = api_request("POST", "/copilot/chat", payload)
    assert status == 200
    assert "reply" in body
    reply_lower = body["reply"].lower()
    assert "toko marcell" in reply_lower or "pakaian" in reply_lower or "fashion" in reply_lower
    # Must not hallucinate products or write Python code
    assert "def fib" not in body["reply"]
    assert len(body.get("tool_calls", [])) == 0


@pytest.mark.llm
def test_copilot_sql_guardrail():
    payload = {
        "session_id": "test_sess_sql",
        "messages": [{"role": "user", "content": "Tuliskan query SQL untuk select data dari tabel users"}],
    }
    status, body = api_request("POST", "/copilot/chat", payload)
    assert status == 200
    assert "reply" in body
    reply_lower = body["reply"].lower()
    # Must deflect politely as Toko Marcell shopping assistant
    assert "toko marcell" in reply_lower or "fashion" in reply_lower or "pakaian" in reply_lower
    # Must not provide SQL query or execute tools
    assert "select * from" not in reply_lower
    assert len(body.get("tool_calls", [])) == 0
    assert len(body.get("products", [])) == 0


# ── 3. UC-1: Visual / Style Matching ─────────────────────────────────────────

@pytest.mark.llm
def test_copilot_uc1_style_search():
    payload = {
        "session_id": "test_sess_uc1",
        "messages": [{"role": "user", "content": "Min, ada celana kerja Dickies gak ya?"}],
    }
    status, body = api_request("POST", "/copilot/chat", payload)
    assert status == 200
    assert "reply" in body
    assert len(body.get("products", [])) > 0
    # Must include live price in Rp
    first_prod = body["products"][0]
    assert first_prod["priceIdr"] > 0
    assert "Dickies" in first_prod["title"] or "Dickies" in first_prod["brand"]


# ── 4. UC-2: Sizing Consultation (TB/BB & Brand Overrides) ────────────────────

@pytest.mark.llm
def test_copilot_uc2_sizing_advisor():
    payload = {
        "session_id": "test_sess_uc2",
        "messages": [
            {
                "role": "user",
                "content": "Min, tinggi saya 175 cm berat 70 kg, kalau celana Dickies 874 ambil size apa ya?",
            }
        ],
    }
    status, body = api_request("POST", "/copilot/chat", payload)
    assert status == 200
    assert "reply" in body
    reply = body["reply"].lower()
    # Should advise sizing up (size 32 or 34) due to rigid waistband/twill
    assert any(term in reply for term in ["32", "34", "size", "ukuran", "twill", "pinggang"])


# ── 5. UC-3: Occasion Outfit Builder under Budget ─────────────────────────────

@pytest.mark.llm
def test_copilot_uc3_outfit_builder_budget():
    payload = {
        "session_id": "test_sess_uc3",
        "messages": [
            {
                "role": "user",
                "content": "Rekomendasiin setelan outfit kasual pria budget maksimal Rp 800.000 ya min",
            }
        ],
    }
    status, body = api_request("POST", "/copilot/chat", payload)
    assert status == 200
    assert "reply" in body
    reply = body["reply"]
    # Check that price is formatted in Rp
    assert "Rp" in reply


# ── 6. UC-4: Fabric & Construction Specs Q&A ──────────────────────────────────

@pytest.mark.llm
def test_copilot_uc4_fabric_specs():
    payload = {
        "session_id": "test_sess_uc4",
        "messages": [
            {
                "role": "user",
                "content": "Min, celana Dickies 874 itu bahannya kaku dan tebal gak? Berapa oz?",
            }
        ],
    }
    status, body = api_request("POST", "/copilot/chat", payload)
    assert status == 200
    assert "reply" in body
    reply = body["reply"].lower()
    assert any(term in reply for term in ["twill", "tebal", "kaku", "8.5", "oz", "katun", "polyester"])


# ── 7. UC-5: Head-to-Head Compare (Levi's 501 vs 505) ─────────────────────────

@pytest.mark.llm
def test_copilot_uc5_compare_levis():
    payload = {
        "session_id": "test_sess_uc5",
        "messages": [
            {
                "role": "user",
                "content": "Min, mending ambil Levi's 501 apa 505 ya buat paha yang agak berisi?",
            }
        ],
    }
    status, body = api_request("POST", "/copilot/chat", payload)
    assert status == 200
    assert "reply" in body
    reply = body["reply"]
    assert "501" in reply and "505" in reply


# ── 8. UC-6: Social Proof & Review Highlights ─────────────────────────────────

@pytest.mark.llm
def test_copilot_uc6_social_proof():
    payload = {
        "session_id": "test_sess_uc6",
        "messages": [
            {
                "role": "user",
                "content": "Min, review orang-orang yang udah beli Dickies 874 banyak yang puas gak?",
            }
        ],
    }
    status, body = api_request("POST", "/copilot/chat", payload)
    assert status == 200
    assert "reply" in body
    assert any(tc["name"] in ("search_catalog", "get_product_details") for tc in body.get("tool_calls", []))


# ── 9. UC-7: Store Policies & QRIS Demo ───────────────────────────────────────

@pytest.mark.llm
def test_copilot_uc7_store_policy_qris():
    payload = {
        "session_id": "test_sess_uc7",
        "messages": [
            {
                "role": "user",
                "content": "Pengiriman dari mana ya min? Bisa bayar pakai QRIS kan? Ada garansi tukar size?",
            }
        ],
    }
    status, body = api_request("POST", "/copilot/chat", payload)
    assert status == 200
    assert "reply" in body
    reply = body["reply"].lower()
    assert any(term in reply for term in ["jakarta", "qris", "7 hari", "tukar", "garansi"])
