"""Copilot endpoints: v2 chat (text-only), tool listing, thread memory."""

import json
import logging
import os

from config import get_settings
from db import get_conn
from fastapi import APIRouter, HTTPException, Path, Request
from fastapi.responses import JSONResponse, StreamingResponse
from memory_trim import trim_memory
from rate_limiter import check_copilot_rate_limit, check_write_rate_limit
from schemas import CopilotChatV2In, CopilotFeedbackIn

logger = logging.getLogger(__name__)

router = APIRouter()

# v2 tool names exposed by /copilot/tools (schema lives in copilot_tools).
V2_TOOL_NAMES = [
    "search_catalog",
    "get_product_details",
    "recommend_size",
    "build_outfit",
    "lookup_store_policy",
    "add_to_cart",
    "flag_unanswered",
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
    # glibc keeps freed LangGraph/tool allocations in its arenas; hand them back
    # to the kernel now so a chatty session cannot creep into the 512 MB ceiling.
    trim_memory()
    return result


@router.post("/copilot/chat/stream")
async def copilot_chat_stream(payload: CopilotChatV2In, request: Request):
    """Stream a copilot v2 turn as Server-Sent Events (`token` and `done`)."""
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

    from copilot_agent import is_llm_available, stream_turn
    from copilot_memory import get_checkpointer
    from guardrail import classify

    settings = get_settings()
    ctx = payload.context
    fake = os.environ.get("COPILOT_FAKE_LLM") == "1"
    label, _ = classify(payload.message)
    if label is None and not fake and not is_llm_available():
        raise HTTPException(
            status_code=503,
            detail="Layanan asisten AI sedang tidak tersedia sementara waktu. Silakan coba kembali nanti.",
        )

    checkpointer = None if fake else get_checkpointer()

    async def event_generator():
        # ``finally`` covers both endings of a streamed turn: the generator
        # running out of events and the client disconnecting mid-stream (which
        # raises ``GeneratorExit``/``CancelledError`` into the ``yield``).
        try:
            async for evt in stream_turn(
                payload.message,
                db_url=settings.DATABASE_URL,
                session_id=payload.session_id,
                page_asin=ctx.page_asin if ctx else None,
                referenced_asins=ctx.referenced_asins if ctx else [],
                thread_id=payload.thread_id,
                checkpointer=checkpointer,
                fake=fake,
            ):
                yield f"data: {json.dumps(evt)}\n\n"
        finally:
            trim_memory()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/copilot/tools")
def copilot_tools():
    """Returns the names of the deterministic tools the v2 copilot can call."""
    return {"tools": V2_TOOL_NAMES}


# ── Feedback (phase 9) ───────────────────────────────────────────────────────

@router.post("/copilot/feedback", status_code=201)
def submit_copilot_feedback(payload: CopilotFeedbackIn, request: Request):
    """Record one thumbs up/down vote for a copilot conversation.

    Rate-limited like the other browser writes; feedback must never be able to
    take the chat down, so the endpoint is a single insert and nothing else.
    """
    allowed, retry_after = check_write_rate_limit(_get_client_ip(request))
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many feedback requests. Please slow down.",
            headers={"Retry-After": str(retry_after)},
        )

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO copilot_feedback (thread_id, session_id, rating)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (payload.thread_id, payload.session_id, payload.rating),
            )
            feedback_id = cur.fetchone()[0]
        conn.commit()

    return {
        "id": feedback_id,
        "thread_id": payload.thread_id,
        "rating": payload.rating,
    }


# ── Thread memory (v2, design §2) ────────────────────────────────────────────

@router.get("/copilot/threads/{thread_id}")
def get_thread(thread_id: str = Path(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")):
    """Return the display messages for a conversation thread (may be empty)."""
    from copilot_memory import get_thread_messages
    messages = get_thread_messages(thread_id)
    return {"thread_id": thread_id, "messages": messages}


@router.delete("/copilot/threads/{thread_id}", status_code=204)
def delete_thread_endpoint(thread_id: str = Path(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")):
    """Reset a conversation: delete all persisted checkpointer state for it."""
    from copilot_memory import delete_thread
    delete_thread(thread_id)
    return None
