"""Admin authentication endpoints: login, refresh rotation, logout, profile."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from auth import (
    AdminPrincipal,
    create_access_token,
    create_refresh_token,
    get_current_admin,
    hash_refresh_token,
    verify_dummy_password,
    verify_password,
)
from config import get_settings
from db import get_conn
from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "refresh_token"
REFRESH_COOKIE_PATH = "/auth"


class LoginRequest(BaseModel):
    username: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str | None = None


def _is_https(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return proto.split(",")[0].strip().lower() == "https"


def _set_refresh_cookie(response: Response, request: Request, raw_token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=raw_token,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        httponly=True,
        secure=_is_https(request),
        samesite="lax",
        path=REFRESH_COOKIE_PATH,
    )


def _clear_refresh_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(
        key=REFRESH_COOKIE,
        path=REFRESH_COOKIE_PATH,
        secure=_is_https(request),
        httponly=True,
        samesite="lax",
    )


@router.post("/login")
def login(payload: LoginRequest, request: Request, response: Response):
    """Verify Argon2id credentials and issue a 15-minute access token."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, username, password_hash, role FROM admin_users WHERE username = %s",
            (payload.username,),
        ).fetchone()

    if row is None:
        verify_dummy_password(payload.password)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    user_id, username, password_hash, role = row
    if not verify_password(payload.password, password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    raw_refresh, refresh_hash, expires_at = create_refresh_token()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO admin_refresh_tokens (user_id, token_hash, expires_at)
            VALUES (%s, %s, %s)
            """,
            (user_id, refresh_hash, expires_at),
        )

    access_token = create_access_token({"sub": username, "role": role})
    _set_refresh_cookie(response, request, raw_refresh)
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {"username": username, "role": role},
    }


@router.post("/refresh")
def refresh(
    request: Request,
    response: Response,
    payload: RefreshRequest | None = Body(default=None),  # noqa: B008
):
    """Rotate a refresh token and issue a fresh access token.

    Presenting an already-revoked token is treated as theft: every active
    refresh token for that user is revoked before returning ``401``.
    """
    raw_token = request.cookies.get(REFRESH_COOKIE)
    if not raw_token and payload is not None:
        raw_token = payload.refresh_token

    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token required",
        )

    token_hash = hash_refresh_token(raw_token)
    now = datetime.now(UTC)
    rotated: tuple[str, str, str] | None = None
    reuse_detected = False
    expired_token = False

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT t.id, t.user_id, t.revoked, t.expires_at, u.username, u.role
                FROM admin_refresh_tokens t
                JOIN admin_users u ON u.id = t.user_id
                WHERE t.token_hash = %s
                FOR UPDATE OF t
                """,
                (token_hash,),
            )
            row = cur.fetchone()

            if row is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid refresh token",
                )

            token_id, user_id, revoked, expires_at, username, role = row

            if revoked:
                # Token replay: assume compromise and invalidate the family.
                cur.execute(
                    """
                    UPDATE admin_refresh_tokens
                    SET revoked = TRUE
                    WHERE user_id = %s AND revoked = FALSE
                    """,
                    (user_id,),
                )
                reuse_detected = True
            elif expires_at <= now:
                cur.execute(
                    "UPDATE admin_refresh_tokens SET revoked = TRUE WHERE id = %s",
                    (token_id,),
                )
                expired_token = True
            else:
                cur.execute(
                    "UPDATE admin_refresh_tokens SET revoked = TRUE WHERE id = %s",
                    (token_id,),
                )
                new_raw, new_hash, new_expires_at = create_refresh_token()
                cur.execute(
                    """
                    INSERT INTO admin_refresh_tokens (user_id, token_hash, expires_at)
                    VALUES (%s, %s, %s)
                    """,
                    (user_id, new_hash, new_expires_at),
                )
                rotated = (new_raw, username, role)

    if reuse_detected:
        logger.warning(
            "auth.refresh.reuse_detected",
            extra={"user_id": user_id, "token_id": token_id},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token reuse detected; all sessions were revoked",
        )

    if expired_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token expired",
        )

    if rotated is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    new_raw, username, role = rotated
    access_token = create_access_token({"sub": username, "role": role})
    _set_refresh_cookie(response, request, new_raw)
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {"username": username, "role": role},
    }


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    payload: RefreshRequest | None = Body(default=None),  # noqa: B008
):
    """Revoke the presented refresh token and clear the HttpOnly cookie."""
    raw_token = request.cookies.get(REFRESH_COOKIE)
    if not raw_token and payload is not None:
        raw_token = payload.refresh_token

    if raw_token:
        token_hash = hash_refresh_token(raw_token)
        with get_conn() as conn:
            conn.execute(
                "UPDATE admin_refresh_tokens SET revoked = TRUE WHERE token_hash = %s",
                (token_hash,),
            )

    _clear_refresh_cookie(response, request)
    return {"ok": True}


@router.get("/me")
def me(admin: AdminPrincipal = Depends(get_current_admin)):  # noqa: B008
    """Return the authenticated admin's identity.

    Ages/roles live in PostgreSQL, so the row is re-read rather than trusting
    the JWT claims for display or authorisation decisions.
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, username, role FROM admin_users WHERE username = %s",
            (admin.username,),
        ).fetchone()

    if row is not None:
        user_id, username, role = row
    else:
        user_id, username, role = admin.id, admin.username, admin.role

    return {"user": {"id": user_id, "username": username, "role": role}}
