"""API tests for admin login, refresh rotation, logout and protected routes."""

import sys
from pathlib import Path

import pytest

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

from fastapi.testclient import TestClient
from main import app
from migrate import run_migrations

USERNAME = "admin"
PASSWORD = "marcell-admin-2026"  # noqa: S105


@pytest.fixture(scope="module", autouse=True)
def _schema():
    try:
        run_migrations()
    except Exception as exc:  # pragma: no cover - environment guard
        pytest.skip(f"Postgres is unavailable: {exc}")


@pytest.fixture
def client():
    return TestClient(app)


def _login(client: TestClient) -> dict:
    response = client.post(
        "/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_login_success_sets_httponly_refresh_cookie(client):
    response = client.post(
        "/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"  # noqa: S105
    assert body["access_token"]
    assert body["user"] == {"username": USERNAME, "role": "admin"}

    set_cookie = response.headers["set-cookie"]
    assert "HttpOnly" in set_cookie
    assert "Path=/auth" in set_cookie
    assert "Max-Age=604800" in set_cookie
    assert "SameSite=lax" in set_cookie
    assert client.cookies.get("refresh_token")


def test_login_wrong_password_is_rejected(client):
    response = client.post(
        "/auth/login",
        json={"username": USERNAME, "password": "definitely-wrong"},
    )
    assert response.status_code == 401
    assert client.cookies.get("refresh_token") is None


def test_login_unknown_user_is_rejected(client):
    response = client.post(
        "/auth/login",
        json={"username": "nobody", "password": PASSWORD},
    )
    assert response.status_code == 401


def test_refresh_rotates_token(client):
    first = _login(client)
    old_raw = client.cookies.get("refresh_token")

    response = client.post("/auth/refresh")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["access_token"] != first["access_token"]
    assert body["user"]["username"] == USERNAME

    new_raw = client.cookies.get("refresh_token")
    assert new_raw
    assert new_raw != old_raw


def test_refresh_token_reuse_revokes_all_sessions(client):
    _login(client)
    old_raw = client.cookies.get("refresh_token")

    rotated = client.post("/auth/refresh")
    assert rotated.status_code == 200
    new_raw = client.cookies.get("refresh_token")
    assert new_raw != old_raw

    # Replay the already-rotated token.
    client.cookies.clear()
    client.cookies.set("refresh_token", old_raw)
    replay = client.post("/auth/refresh")
    assert replay.status_code == 401
    assert "reuse" in replay.json()["detail"].lower()

    # The whole token family must now be dead, including the newest token.
    client.cookies.clear()
    client.cookies.set("refresh_token", new_raw)
    assert client.post("/auth/refresh").status_code == 401


def test_refresh_accepts_body_fallback(client):
    _login(client)
    raw_token = client.cookies.get("refresh_token")
    client.cookies.clear()

    response = client.post("/auth/refresh", json={"refresh_token": raw_token})
    assert response.status_code == 200
    assert response.json()["access_token"]


def test_refresh_without_token_is_rejected(client):
    client.cookies.clear()
    response = client.post("/auth/refresh")
    assert response.status_code == 401


def test_logout_revokes_and_clears_cookie(client):
    _login(client)
    raw_token = client.cookies.get("refresh_token")

    response = client.post("/auth/logout")
    assert response.status_code == 200
    set_cookie = response.headers["set-cookie"]
    assert "Max-Age=0" in set_cookie
    assert "Path=/auth" in set_cookie

    client.cookies.clear()
    client.cookies.set("refresh_token", raw_token)
    assert client.post("/auth/refresh").status_code == 401


def test_admin_kpi_requires_bearer_token(client):
    client.cookies.clear()
    assert client.get("/admin/kpi").status_code == 401

    body = _login(client)
    response = client.get(
        "/admin/kpi?since_days=7",
        headers=_bearer(body["access_token"]),
    )
    assert response.status_code == 200
    assert response.json()["metadata"]["since_days"] == 7


def test_auth_me_returns_database_identity(client):
    body = _login(client)
    response = client.get("/auth/me", headers=_bearer(body["access_token"]))

    assert response.status_code == 200
    user = response.json()["user"]
    assert isinstance(user["id"], int)
    assert user["username"] == USERNAME
    assert user["role"] == "admin"


def test_auth_me_requires_credentials(client):
    client.cookies.clear()
    assert client.get("/auth/me").status_code == 401
