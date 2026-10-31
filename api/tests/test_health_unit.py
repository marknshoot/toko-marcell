"""Unit tests for the health endpoint (api/routers/health.py).

The route reports Render (API) and Neon (Postgres) independently: the API is
alive as soon as the process answers, while Neon may be waking from a
scale-to-zero suspend.  These tests pin both branches without touching a real
database — ``get_conn`` is patched in the router module namespace.
"""

import sys
from contextlib import contextmanager
from pathlib import Path

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import Response

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

from routers import health as health_module
from routers.health import router


class _FakeConn:
    """Minimal stand-in for a psycopg connection that records executed SQL."""

    def __init__(self):
        self.executed: list[str] = []

    def execute(self, query, *args, **kwargs):
        self.executed.append(query)
        return None


def _ok_conn_factory(conn: _FakeConn):
    @contextmanager
    def _get_conn():
        yield conn

    return _get_conn


@contextmanager
def _raising_conn(exc: Exception):
    raise exc
    yield  # pragma: no cover — unreachable, keeps the generator contract


@pytest.fixture
def client():
    """TestClient over a router-only app (no lifespan, no DB, no migrations)."""
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ── Healthy path ────────────────────────────────────────────────────────────


def test_health_ok_direct(monkeypatch):
    conn = _FakeConn()
    monkeypatch.setattr(health_module, "get_conn", _ok_conn_factory(conn))

    response = Response()
    body = health_module.health(response)

    assert response.status_code == 200
    assert body == {
        "status": "ok",
        "services": {"api": "ok", "database": "ok"},
    }
    assert conn.executed == ["SELECT 1;"]


def test_health_ok_via_testclient(monkeypatch, client):
    conn = _FakeConn()
    monkeypatch.setattr(health_module, "get_conn", _ok_conn_factory(conn))

    resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "ok",
        "services": {"api": "ok", "database": "ok"},
    }
    assert len(conn.executed) == 1
    assert "SELECT 1" in conn.executed[0]


# ── Degraded / waking path ──────────────────────────────────────────────────


def test_health_waking_on_operational_error_direct(monkeypatch):
    exc = psycopg.OperationalError("connection to neon failed: compute suspended")
    monkeypatch.setattr(health_module, "get_conn", lambda: _raising_conn(exc))

    response = Response()
    body = health_module.health(response)

    assert response.status_code == 503
    assert body == {
        "status": "waking",
        "services": {"api": "ok", "database": "waking"},
        "error": "Database is waking up or unreachable",
    }


def test_health_waking_on_generic_exception_via_testclient(monkeypatch, client):
    exc = RuntimeError("pool exhausted")
    monkeypatch.setattr(health_module, "get_conn", lambda: _raising_conn(exc))

    resp = client.get("/health")

    assert resp.status_code == 503
    assert resp.json() == {
        "status": "waking",
        "services": {"api": "ok", "database": "waking"},
        "error": "Database is waking up or unreachable",
    }


def test_health_waking_when_query_raises(monkeypatch, client):
    """A live connection whose ``SELECT 1;`` fails must also report waking."""

    class _FailingConn(_FakeConn):
        def execute(self, query, *args, **kwargs):
            raise psycopg.OperationalError("server closed the connection")

    monkeypatch.setattr(health_module, "get_conn", _ok_conn_factory(_FailingConn()))

    resp = client.get("/health")

    assert resp.status_code == 503
    assert resp.json()["status"] == "waking"
    assert resp.json()["services"] == {"api": "ok", "database": "waking"}


def test_health_logs_warning_on_failure(monkeypatch, caplog):
    exc = psycopg.OperationalError("boom")
    monkeypatch.setattr(health_module, "get_conn", lambda: _raising_conn(exc))

    with caplog.at_level("WARNING", logger=health_module.__name__):
        health_module.health(Response())

    assert any(record.levelname == "WARNING" and "health" in record.getMessage().lower() for record in caplog.records)
