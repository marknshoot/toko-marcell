"""Admin authentication primitives.

Passwords are hashed with Argon2id.  Access tokens are short-lived HS256 JWTs;
refresh tokens are opaque random values whose SHA-256 digest is stored in
PostgreSQL (the raw token only ever lives in the HttpOnly cookie).  The legacy
static ``ADMIN_TOKEN`` remains accepted as a fallback for scripts.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from config import get_settings
from fastapi import Depends, Header, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.exceptions import InvalidTokenError

# PasswordHasher defaults to Argon2id with the OWASP-recommended parameters.
password_hasher = PasswordHasher()

# A valid Argon2id hash used only to equalise timing when a username does not
# exist, so login does not reveal account existence.
_DUMMY_PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$2h2RJtrOjS/cCEEn22XmTA$"  # noqa: S105
    "88kNZ9wS4X++MhCfiaJln9LLLf6yaVMVk8Qg0FKlvxo"
)

bearer_scheme = HTTPBearer(auto_error=False)

CREDENTIALS_ERROR = "Could not validate credentials"


@dataclass(frozen=True)
class AdminPrincipal:
    """The authenticated caller, independent of the credential type used."""

    id: int | None
    username: str
    role: str


# ── Passwords ────────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    """Hash a plaintext password with Argon2id."""
    return password_hasher.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """Return ``True`` only when ``plain`` matches ``hashed``."""
    try:
        return password_hasher.verify(hashed, plain)
    except (VerifyMismatchError, VerificationError, InvalidHashError, TypeError):
        return False


def verify_dummy_password(plain: str) -> None:
    """Burn the same Argon2 work as a real check (unknown username path)."""
    try:
        password_hasher.verify(_DUMMY_PASSWORD_HASH, plain)
    except (VerifyMismatchError, VerificationError, InvalidHashError, TypeError):
        pass


# ── JWT access tokens ────────────────────────────────────────────────────────

def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """Create a signed JWT containing ``sub``, ``role``, ``exp`` and ``iat``."""
    settings = get_settings()
    now = datetime.now(UTC)
    expires = (
        expires_delta
        if expires_delta is not None
        else timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )

    payload = dict(data)
    payload.setdefault("jti", secrets.token_hex(8))
    payload["iat"] = now
    payload["exp"] = now + expires
    if "sub" in payload and not isinstance(payload["sub"], str):
        payload["sub"] = str(payload["sub"])

    return jwt.encode(
        payload,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )


def decode_access_token(token: str) -> dict:
    """Validate signature/expiry and return the claims."""
    settings = get_settings()
    return jwt.decode(
        token,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
        options={"require": ["exp", "iat", "sub"]},
    )


# ── Refresh tokens ───────────────────────────────────────────────────────────

def hash_refresh_token(raw_token: str) -> str:
    """SHA-256 hex digest stored in ``admin_refresh_tokens.token_hash``."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def create_refresh_token() -> tuple[str, str, datetime]:
    """Return ``(raw_token, sha256_hash, expires_at)`` for a new refresh token."""
    settings = get_settings()
    raw_token = secrets.token_urlsafe(48)
    expires_at = datetime.now(UTC) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    return raw_token, hash_refresh_token(raw_token), expires_at


# ── FastAPI dependency ───────────────────────────────────────────────────────

def get_current_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),  # noqa: B008
    token: str | None = Query(default=None, description="Legacy static admin token"),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> AdminPrincipal:
    """Authenticate an admin via Bearer JWT, then the legacy static token.

    ``Authorization: Bearer <jwt>`` is checked first.  If that is absent or
    invalid, ``X-Admin-Token`` or ``?token=`` is compared in constant time
    against ``settings.ADMIN_TOKEN`` for backward compatibility with scripts.
    """
    settings = get_settings()

    if credentials is not None and credentials.scheme.lower() == "bearer":
        try:
            claims = decode_access_token(credentials.credentials)
            username = claims.get("sub")
            if username:
                return AdminPrincipal(
                    id=claims.get("id"),
                    username=str(username),
                    role=str(claims.get("role") or "admin"),
                )
        except (InvalidTokenError, ValueError):
            pass

    configured = settings.ADMIN_TOKEN or ""
    provided = x_admin_token or token or ""
    if (
        configured
        and provided
        and hmac.compare_digest(
            provided.encode("utf-8"), configured.encode("utf-8")
        )
    ):
        # The seeded demo admin shares this identity; /auth/me resolves its id.
        return AdminPrincipal(id=None, username="admin", role="admin")

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=CREDENTIALS_ERROR,
        headers={"WWW-Authenticate": "Bearer"},
    )
