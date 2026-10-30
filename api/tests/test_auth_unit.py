"""Unit tests for Argon2id hashing, JWT claims and refresh-token hashing."""

import hashlib
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import jwt
import pytest

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

import auth

SETTINGS = SimpleNamespace(
    JWT_SECRET_KEY="unit-test-secret-with-at-least-32-bytes",
    JWT_ALGORITHM="HS256",
    ACCESS_TOKEN_EXPIRE_MINUTES=15,
    REFRESH_TOKEN_EXPIRE_DAYS=7,
)


@pytest.fixture(autouse=True)
def fake_settings(monkeypatch):
    monkeypatch.setattr(auth, "get_settings", lambda: SETTINGS)


def test_argon2id_hash_and_verify():
    hashed = auth.hash_password("correct horse battery staple")

    assert hashed.startswith("$argon2id$")
    assert auth.verify_password("correct horse battery staple", hashed) is True
    assert auth.verify_password("wrong password", hashed) is False


def test_verify_password_rejects_malformed_hash():
    assert auth.verify_password("anything", "not-a-hash") is False


def test_access_token_claims_and_default_expiry():
    token = auth.create_access_token({"sub": "admin", "role": "admin"})
    claims = jwt.decode(token, SETTINGS.JWT_SECRET_KEY, algorithms=["HS256"])

    assert claims["sub"] == "admin"
    assert claims["role"] == "admin"
    assert {"sub", "role", "exp", "iat"} <= claims.keys()
    assert claims["exp"] - claims["iat"] == 15 * 60
    assert claims["iat"] <= int(datetime.now(UTC).timestamp())


def test_access_token_custom_expiry():
    token = auth.create_access_token(
        {"sub": "admin", "role": "admin"},
        expires_delta=timedelta(seconds=30),
    )
    claims = jwt.decode(token, SETTINGS.JWT_SECRET_KEY, algorithms=["HS256"])
    assert claims["exp"] - claims["iat"] == 30


def test_expired_access_token_is_rejected():
    token = auth.create_access_token(
        {"sub": "admin", "role": "admin"},
        expires_delta=timedelta(seconds=-1),
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        auth.decode_access_token(token)


def test_refresh_token_hash_is_sha256_hex():
    raw, token_hash, expires_at = auth.create_refresh_token()

    assert raw
    assert token_hash == hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert len(token_hash) == 64
    assert all(char in "0123456789abcdef" for char in token_hash)

    delta = expires_at - datetime.now(UTC)
    assert timedelta(days=6, hours=23) < delta <= timedelta(days=7)


def test_refresh_tokens_are_unique():
    first, first_hash, _ = auth.create_refresh_token()
    second, second_hash, _ = auth.create_refresh_token()
    assert first != second
    assert first_hash != second_hash


def test_hash_refresh_token_known_value():
    assert (
        auth.hash_refresh_token("refresh-token")
        == hashlib.sha256(b"refresh-token").hexdigest()
    )
