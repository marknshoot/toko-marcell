"""Copilot endpoints: v2 chat (text-only), tool listing, thread memory."""

import logging
import os

from config import get_settings
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from rate_limiter import check_copilot_rate_limit
from schemas import CopilotChatV2In

logger = logging.getLogger(__name__)

router = APIRouter()

# v2 tool names exposed by /copilot/tools (schema lives in copilot_tools).
V2_TOOL_NAMES = [
    "search_catalog",
    "get_product_details",
    "get_product_reviews",
    "recommend_size",
    "build_outfit",
    "lookup_store_policy",
    "add_to_cart",
]


def _get_client_ip(request: Request) -> str:
    """Extract the client IP, respecting X-Forwarded-For only when TRUST_PROXY is true."""
    settings = get_settings()
    if settings.TRUST_PROXY:
        xff = request.headers.get("X-Forwarded-For", "")
        if xff:
            return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("/copilot/chat")
async def copilot_chat(payload: CopilotChatV2In, request: Request):
    """Admin Toko Marcell copilot v2 (text-only). See design §API contract v2."""
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
    from copilot_agent import run_turn
    from copilot_memory import get_checkpointer

    settings = get_settings()
    ctx = payload.context
    fake = os.environ.get("COPILOT_FAKE_LLM") == "1"
    checkpointer = None if fake else get_checkpointer()

    result = await run_turn(
        payload.message,
        db_url=settings.DATABASE_URL,
        session_id=payload.session_id,
        page_asin=ctx.page_asin if ctx else None,
        referenced_asins=ctx.referenced_asins if ctx else [],
        thread_id=payload.thread_id,
        checkpointer=checkpointer,
        fake=fake,
    )
    result["thread_id"] = payload.thread_id
    return result


@router.get("/copilot/tools")
def copilot_tools():
    """Returns the names of the deterministic tools the v2 copilot can call."""
    return {"tools": V2_TOOL_NAMES}


# ── Thread memory (v2, design §2) ────────────────────────────────────────────

@router.get("/copilot/threads/{thread_id}")
def get_thread(thread_id: str):
    """Return the display messages for a conversation thread (may be empty)."""
    from copilot_memory import get_thread_messages
    messages = get_thread_messages(thread_id)
    return {"thread_id": thread_id, "messages": messages}


@router.delete("/copilot/threads/{thread_id}", status_code=204)
def delete_thread_endpoint(thread_id: str):
    """Reset a conversation: delete all persisted checkpointer state for it."""
    from copilot_memory import delete_thread
    delete_thread(thread_id)
    return None
