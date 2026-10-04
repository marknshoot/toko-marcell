"""Copilot endpoints: chat, tool listing."""

import logging

from config import get_settings
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from rate_limiter import check_copilot_rate_limit
from schemas import CopilotChatIn

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_client_ip(request: Request) -> str:
    """Extract the client IP, respecting X-Forwarded-For only when TRUST_PROXY is true."""
    settings = get_settings()
    if settings.TRUST_PROXY:
        xff = request.headers.get("X-Forwarded-For", "")
        if xff:
            return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("/copilot/chat")
async def copilot_chat(payload: CopilotChatIn, request: Request):
    """End-to-End Multimodal AI Shopping Copilot (Admin Toko Marcell)."""
    # ── Rate limiting ────────────────────────────────────────────────────────
    client_ip = _get_client_ip(request)
    allowed, retry_after, which = check_copilot_rate_limit(client_ip)
    if not allowed:
        msg = (
            "Kak, mimin butuh istirahat sebentar ya 😊 "
            "Terlalu banyak permintaan dalam waktu singkat. "
            "Silakan coba lagi nanti!"
        )
        return JSONResponse(
            status_code=429,
            content={"detail": msg, "limit": which},
            headers={"Retry-After": str(retry_after)},
        )
    # ─────────────────────────────────────────────────────────────────────────
    from agent import chat_copilot

    settings = get_settings()
    image_ref = payload.image_url
    if not image_ref and payload.messages:
        image_ref = payload.messages[-1].imageUrl

    res = await chat_copilot(
        messages=[{"role": m.role, "content": m.content} for m in payload.messages],
        session_id=payload.session_id,
        image_url=image_ref,
        db_url=settings.DATABASE_URL,
    )
    return res


@router.get("/copilot/tools")
def copilot_tools():
    """Returns the deterministic tool definitions used by the AI Copilot."""
    from agent import TOOLS_SCHEMA
    return {"tools": TOOLS_SCHEMA}
