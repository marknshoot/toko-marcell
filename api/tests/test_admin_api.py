"""API tests for the token-gated KPI dashboard.

These use an in-process TestClient so the ADMIN_TOKEN setting can be varied per
test without restarting the live API. The endpoint reads from the same local
Postgres database as the rest of the API suite.
"""

import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

from fastapi.testclient import TestClient
from main import app
from routers import admin

client = TestClient(app)
TOKEN = "phase12-admin-test-token"  # noqa: S105


def _fake_settings(token: str) -> SimpleNamespace:
    return SimpleNamespace(ADMIN_TOKEN=token)


def test_admin_kpi_503_when_token_not_configured(monkeypatch):
    monkeypatch.setattr(admin, "get_settings", lambda: _fake_settings(""))
    response = client.get("/admin/kpi", headers={"X-Admin-Token": TOKEN})
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"].lower()


def test_admin_kpi_403_when_token_missing(monkeypatch):
    monkeypatch.setattr(admin, "get_settings", lambda: _fake_settings(TOKEN))
    response = client.get("/admin/kpi")
    assert response.status_code == 403


def test_admin_kpi_403_when_token_wrong(monkeypatch):
    monkeypatch.setattr(admin, "get_settings", lambda: _fake_settings(TOKEN))
    response = client.get("/admin/kpi", headers={"X-Admin-Token": "wrong-token"})
    assert response.status_code == 403


def test_admin_kpi_accepts_query_token(monkeypatch):
    monkeypatch.setattr(admin, "get_settings", lambda: _fake_settings(TOKEN))
    response = client.get(f"/admin/kpi?since_days=7&token={TOKEN}")
    assert response.status_code == 200
    assert response.json()["metadata"]["since_days"] == 7


def test_admin_kpi_window_validation(monkeypatch):
    monkeypatch.setattr(admin, "get_settings", lambda: _fake_settings(TOKEN))
    headers = {"X-Admin-Token": TOKEN}
    assert client.get("/admin/kpi?since_days=0", headers=headers).status_code == 422
    assert client.get("/admin/kpi?since_days=366", headers=headers).status_code == 422


def test_admin_kpi_full_schema(monkeypatch):
    monkeypatch.setattr(admin, "get_settings", lambda: _fake_settings(TOKEN))
    response = client.get(
        "/admin/kpi?since_days=30", headers={"X-Admin-Token": TOKEN}
    )
    assert response.status_code == 200
    body = response.json()

    assert set(body) == {
        "metadata",
        "revenue",
        "funnel",
        "cohorts",
        "unit_economics",
        "search_health",
        "copilot_quality",
    }

    metadata = body["metadata"]
    assert metadata["since_days"] == 30
    assert metadata["synthetic"] is True
    assert metadata["environment"] == "demo"
    assert (
        metadata["caveat"]
        == "All metrics are derived from seeded/simulated demo data; not real customer revenue."
    )
    datetime.fromisoformat(metadata["generated_at"])

    revenue = body["revenue"]
    assert set(revenue) == {"order_count", "total_revenue_idr", "aov_idr"}
    assert isinstance(revenue["order_count"], int)
    assert isinstance(revenue["total_revenue_idr"], int)
    assert revenue["aov_idr"] is None or isinstance(revenue["aov_idr"], int | float)

    funnel = body["funnel"]
    assert set(funnel) == {
        "sessions",
        "view_product_sessions",
        "add_to_cart_sessions",
        "checkout_start_sessions",
        "order_sessions",
        "rates",
    }
    for key in (
        "sessions",
        "view_product_sessions",
        "add_to_cart_sessions",
        "checkout_start_sessions",
        "order_sessions",
    ):
        assert isinstance(funnel[key], int)
        assert funnel[key] >= 0
    assert set(funnel["rates"]) == {
        "view_to_cart",
        "cart_to_checkout",
        "checkout_to_order",
        "overall_conversion",
    }
    for value in funnel["rates"].values():
        assert value is None or isinstance(value, int | float)

    cohorts = body["cohorts"]
    assert set(cohorts) == {
        "copilot_sessions",
        "non_copilot_sessions",
        "caveat",
    }
    assert cohorts["caveat"] == (
        "Observational comparison; copilot usage is self-selected, "
        "not a randomized trial."
    )
    for key in ("copilot_sessions", "non_copilot_sessions"):
        block = cohorts[key]
        assert set(block) == {
            "sessions",
            "add_to_cart_rate",
            "conversion_rate",
            "aov_idr",
        }
        assert isinstance(block["sessions"], int)
        assert block["add_to_cart_rate"] is None or isinstance(
            block["add_to_cart_rate"], int | float
        )
        assert block["conversion_rate"] is None or isinstance(
            block["conversion_rate"], int | float
        )
        assert block["aov_idr"] is None or isinstance(block["aov_idr"], int | float)

    economics = body["unit_economics"]
    assert set(economics) == {
        "total_cost_usd",
        "avg_cost_per_turn_usd",
        "cost_per_conversation_usd",
        "cost_per_resolved_conversation_usd",
        "cost_per_assisted_order_usd",
    }
    for value in economics.values():
        assert value is None or isinstance(value, int | float)
        assert value is None or value >= 0

    search_health = body["search_health"]
    assert set(search_health) == {
        "total_searches",
        "zero_result_searches",
        "zero_result_rate",
        "search_to_pdp_rate",
        "top_zero_result_queries",
        "top_queries",
    }
    assert isinstance(search_health["total_searches"], int)
    assert isinstance(search_health["zero_result_searches"], int)
    assert search_health["zero_result_searches"] <= search_health["total_searches"]
    assert search_health["zero_result_rate"] is None or isinstance(
        search_health["zero_result_rate"], int | float
    )
    assert search_health["search_to_pdp_rate"] is None or isinstance(
        search_health["search_to_pdp_rate"], int | float
    )
    for list_key in ("top_zero_result_queries", "top_queries"):
        assert isinstance(search_health[list_key], list)
        assert len(search_health[list_key]) <= 20
        for row in search_health[list_key]:
            assert set(row) == {"query", "count"}
            assert isinstance(row["query"], str)
            assert isinstance(row["count"], int)

    quality = body["copilot_quality"]
    assert set(quality) == {
        "total_turns",
        "answered_turns",
        "guardrail_turns",
        "unanswered_turns",
        "unanswered_rate",
        "deflection_eligible_conversations",
        "deflection_answered_conversations",
        "deflection_rate",
        "latency_p50_ms",
        "latency_p95_ms",
        "thumbs_up",
        "thumbs_down",
        "thumbs_up_rate",
    }
    for key in (
        "total_turns",
        "answered_turns",
        "guardrail_turns",
        "unanswered_turns",
        "thumbs_up",
        "thumbs_down",
    ):
        assert isinstance(quality[key], int)
        assert quality[key] >= 0
    for key in (
        "unanswered_rate",
        "deflection_rate",
        "latency_p50_ms",
        "latency_p95_ms",
        "thumbs_up_rate",
    ):
        assert quality[key] is None or isinstance(quality[key], int | float)
