"""Copilot endpoints: v2 chat (text-only), tool listing, thread memory."""

import json
import logging
import os

from config import get_settings
from db import get_conn
from fastapi import APIRouter, HTTPException, Path, Request
from fastapi.responses import StreamingResponse
from memory_trim import trim_memory
from rate_limiter import check_copilot_rate_limit, check_write_rate_limit
from schemas import CopilotChatV2In, CopilotFeedbackIn, CopilotResumeIn
from web import COPILOT_BUSY_MESSAGE, client_ip, rate_limit_error, rate_limited

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


# Backwards-compatible alias: checkout/events used to import this from here.
_get_client_ip = client_ip


def _copilot_rate_limit(request: Request):
    """Return a ``429`` response when the caller is over the copilot limit, else ``None``."""
    allowed, retry_after, which = check_copilot_rate_limit(client_ip(request))
    if allowed:
        return None
    return rate_limited(COPILOT_BUSY_MESSAGE, retry_after, limit=which)


@router.post("/copilot/chat")
async def copilot_chat(payload: CopilotChatV2In, request: Request):
    """Admin Toko Marcell copilot v2 (text-only). See design §API contract v2."""
    limited = _copilot_rate_limit(request)
    if limited is not None:
        return limited

    # Imported lazily: the LangChain graph and its checkpointer are the heaviest
    # part of the app and only a chat request needs them.
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


@router.post("/copilot/chat/resume")
async def copilot_chat_resume(payload: CopilotResumeIn, request: Request):
    """Answer a pending cart confirmation (yes or no) and finish the paused turn."""
    limited = _copilot_rate_limit(request)
    if limited is not None:
        return limited

    from copilot_agent import resume_turn
    from copilot_approval import NoPendingApproval
    from copilot_memory import get_checkpointer

    settings = get_settings()
    fake = os.environ.get("COPILOT_FAKE_LLM") == "1"
    checkpointer = None if fake else get_checkpointer()
    if checkpointer is None:
        # Approvals need a saved thread; without one nothing can be waiting.
        raise HTTPException(status_code=404, detail="Tidak ada permintaan yang menunggu konfirmasi.")

    try:
        result = await resume_turn(
            payload.thread_id,
            approve=payload.approve,
            db_url=settings.DATABASE_URL,
            session_id=payload.session_id,
            fake=fake,
            checkpointer=checkpointer,
        )
    except NoPendingApproval as exc:
        if exc.thread_exists:
            raise HTTPException(status_code=409, detail="Tidak ada permintaan yang menunggu konfirmasi.") from None
        raise HTTPException(status_code=404, detail="Percakapan tidak ditemukan.") from None

    result["thread_id"] = payload.thread_id
    trim_memory()
    return result


@router.post("/copilot/chat/stream")
async def copilot_chat_stream(payload: CopilotChatV2In, request: Request):
    """Stream a copilot v2 turn as Server-Sent Events (`token` and `done`)."""
    limited = _copilot_rate_limit(request)
    if limited is not None:
        return limited

    from copilot_agent import is_llm_available, stream_turn
    from copilot_memory import get_checkpointer
    from guardrail import classify

    settings = get_settings()
    ctx = payload.context
    fake = os.environ.get("COPILOT_FAKE_LLM") == "1"
    # Regex only: no model call and no blocking I/O on the event loop. The check exists so a
    # greeting still gets its canned reply when no LLM key is configured; stream_turn does the
    # real, history-aware classification.
    label, _ = classify(payload.message, use_llm=False)
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
    allowed, retry_after = check_write_rate_limit(client_ip(request))
    if not allowed:
        raise rate_limit_error("Too many feedback requests. Please slow down.", retry_after)

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
