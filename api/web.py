"""Shared HTTP helpers: who is calling, and what a rate-limited answer looks like.

``client_ip`` used to live in ``routers/copilot.py`` and was imported *from there*
by the checkout and events routers (a private name crossing modules).  The
rate-limit responses were copy-pasted with three different messages and two
different shapes (raise vs return).
"""

from __future__ import annotations

from config import get_settings
from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse

# The shopper-facing "slow down" reply, written once.
COPILOT_BUSY_MESSAGE = (
    "Kak, mimin butuh istirahat sebentar ya 😊 Terlalu banyak permintaan dalam waktu singkat. Silakan coba lagi nanti."
)


def client_ip(request: Request) -> str:
    """Client IP, trusting ``X-Forwarded-For`` only when ``TRUST_PROXY`` is set."""
    if get_settings().TRUST_PROXY:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limited(
    detail: str,
    retry_after: int,
    *,
    limit: str | None = None,
) -> JSONResponse:
    """A ``429`` JSON response; ``limit`` names the window that was hit."""
    content: dict[str, object] = {"detail": detail}
    if limit:
        content["limit"] = limit
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content=content,
        headers={"Retry-After": str(retry_after)},
    )


def rate_limit_error(detail: str, retry_after: int) -> HTTPException:
    """A ``429`` exception for endpoints that raise instead of returning."""
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=detail,
        headers={"Retry-After": str(retry_after)},
    )
