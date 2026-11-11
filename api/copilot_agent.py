"""Copilot v2 agent (design §1): graph assembly and the turn entry points.

LangChain v1 ``create_agent`` (runs on LangGraph) with:

  - the guardrail (``guardrail.classify``) as the FIRST NODE of the graph;
  - the v2 tool layer (``copilot_tools.build_tools``);
  - middleware that injects an authoritative ``<store_context>`` block, keeps the
    shown-products registry across turns, trims history and caps the tool loop;
  - a Postgres checkpointer supplied by the router, so approvals can pause and
    resume across HTTP requests.

The agent is built per-request because the tools close over a per-request
``ToolContext`` (registry, page/pinned ASIN, DB url).

Layout (this file is the assembly + entry points, not the whole copilot):

    copilot_prompts     system prompt and shopper-facing copy
    copilot_state       graph state, LLM-unavailable fallback model
    copilot_context     <store_context> block and input sanitizing
    copilot_middleware  the six graph hooks
    copilot_turn        usage/token accounting and turn telemetry
    copilot_tools       the model-facing tools
    copilot_approval    cart confirmation (human in the loop)
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import threading
import time
from typing import Any

import catalog_repo
from copilot_approval import (
    AbandonedApprovalMiddleware,
    NoPendingApproval,
    approval_decisions,
    approval_from_result,
    cart_approval_middleware,
    pending_items,
    pending_request,
)

# ── Re-exports ───────────────────────────────────────────────────────────────
# This module is the copilot's public surface: the routers import the turn entry
# points, and tests/older call sites also reach for the prompt, sanitizer,
# context, middleware and copy constants.  Keeping the names importable from
# here means the internals can move between modules without breaking callers.
from copilot_context import (  # noqa: F401
    _seed_registry_from_checkpoint,
    build_context_block,
    sanitize_user_message,
)
from copilot_llm import build_chat_model, llm_available
from copilot_middleware import (
    ContextInjectionMiddleware,
    GuardrailMiddleware,
    LoopCapMiddleware,
    RegistryMiddleware,
    TrimHistoryMiddleware,
    TurnCarryMiddleware,
)
from copilot_prompts import (  # noqa: F401
    _EMPTY_REPLY,
    _LLM_NETWORK_DETAIL,
    _LLM_UNAVAILABLE_DETAIL,
    _STREAM_ERROR_REPLY,
    ADMIN_SYSTEM_PROMPT,
    AFTER_TOOLS_LABEL,
    LOOP_CAP_REPLY,
    TOOL_STATUS_LABELS,
)
from copilot_state import CopilotState, LLMUnavailable, _UnavailableChatModel  # noqa: F401
from copilot_tools import ToolContext, build_tools
from copilot_turn import (  # noqa: F401 - re-exported for callers/tests
    Failure,
    _classify_failure,
    _current_turn,
    _extract_reply,
    _finish_turn,
    _pause_turn,
    _status_events,
    _Turn,
    _turn_input,
    message_text,
)
from fastapi import HTTPException
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage
from langgraph.types import Command
from llm_usage import LLMCallCounter

logger = logging.getLogger(__name__)

# Max model→tool iterations in a single turn (design §1).
MAX_MODEL_CALLS = 3

# ``_chunk_text`` is the historical name for the shared content extractor.
_chunk_text = message_text


def _catalog_categories(db_url: str) -> list[str]:
    """Categories for the tool-description enum (cached in ``catalog_repo``)."""
    return catalog_repo.category_names()


def build_agent(
    ctx: ToolContext,
    session_id: str | None,
    *,
    fake: bool | None = None,
    checkpointer=None,
    thread_id: str | None = None,
    turn: _Turn | None = None,
):
    """Construct a per-request compiled agent. Always returns a graph.

    Without a provider the model is a stand-in that raises ``LLMUnavailable`` when called,
    so the guardrail node can still answer greetings.
    """
    model = build_chat_model(temperature=0.2, fake=fake)
    if model is None:
        model = _UnavailableChatModel()
    tools = build_tools(ctx, _catalog_categories(ctx.db_url))
    middleware: list[AgentMiddleware] = [
        GuardrailMiddleware(use_llm=not bool(fake)),
        AbandonedApprovalMiddleware(thread_id),
        RegistryMiddleware(ctx, checkpointer, thread_id),
        ContextInjectionMiddleware(ctx, session_id),
        TrimHistoryMiddleware(),
    ]
    if checkpointer is not None and turn is not None:
        # A pause needs somewhere to keep the paused graph. Without a checkpointer (the fake-LLM
        # test mode) a cart add runs straight away, as it always did. Order matters: see
        # TurnCarryMiddleware.
        approval_middleware, approval = cart_approval_middleware(ctx)
        middleware += [approval_middleware, TurnCarryMiddleware(ctx, turn, approval)]
    middleware.append(LoopCapMiddleware(run_limit=MAX_MODEL_CALLS, exit_behavior="end"))
    return create_agent(
        model,
        tools,
        system_prompt=ADMIN_SYSTEM_PROMPT,
        middleware=middleware,
        checkpointer=checkpointer,
    )


async def _prepare_agent(
    turn: _Turn,
    *,
    page_asin: str | None,
    referenced_asins: list[str] | None,
) -> tuple[ToolContext, Any]:
    """Per-request tool context plus the compiled agent (blocking work runs off the event loop)."""
    ctx = ToolContext(
        db_url=turn.db_url,
        page_asin=page_asin,
        referenced_asins=referenced_asins or [],
    )
    agent = await asyncio.to_thread(
        build_agent,
        ctx,
        turn.session_id,
        fake=turn.fake,
        checkpointer=turn.checkpointer,
        thread_id=turn.thread_id,
        turn=turn,
    )
    return ctx, agent


def _run_config(turn: _Turn) -> dict[str, Any]:
    """Run config. The counter rides here so every model call in the graph, provider-fallback
    attempts included, is counted for this turn."""
    config: dict[str, Any] = {"callbacks": [turn.usage]}
    if turn.checkpointer and turn.thread_id:
        config["configurable"] = {"thread_id": turn.thread_id}
    return config


# One graph run at a time per thread. Without this, a double-click (or a retry racing a
# still-running worker after the client disconnected) could resume the same checkpoint twice.
_ACTIVE_THREADS: set[str] = set()
_ACTIVE_THREADS_LOCK = threading.Lock()


def _begin_thread_run(thread_id: str | None) -> bool:
    if not thread_id:
        return True
    with _ACTIVE_THREADS_LOCK:
        if thread_id in _ACTIVE_THREADS:
            return False
        _ACTIVE_THREADS.add(thread_id)
        return True


def _end_thread_run(thread_id: str | None) -> None:
    if not thread_id:
        return
    with _ACTIVE_THREADS_LOCK:
        _ACTIVE_THREADS.discard(thread_id)


def _retry_block(snapshot: Any) -> tuple[str, str] | None:
    """Why a retry cannot resume this thread; ``None`` means it can."""
    if snapshot is None or not getattr(snapshot, "values", None):
        return (
            "thread_not_found",
            "Percakapan ini sudah kedaluwarsa. Silakan mulai percakapan baru.",
        )
    if pending_request(snapshot) is not None:
        return ("approval_pending", "Konfirmasi keranjang masih menunggu jawaban.")
    return None


def _error_summary(
    code: str,
    message: str,
    *,
    retryable: bool = False,
    retry_after: int | None = None,
) -> dict[str, Any]:
    """A ``done`` payload for a turn that never reached the model."""
    return {
        "reply": message,
        "products": [],
        "ui_actions": [],
        "outfit": None,
        "tool_calls": [],
        "guardrail": None,
        "took_ms": 0,
        "error": True,
        "error_code": code,
        "retryable": retryable,
        "retry_after": retry_after,
    }


def _completed_retry_summary(snapshot: Any) -> dict[str, Any]:
    """The answer a double-clicked retry should show, without calling the model again."""
    values = snapshot.values or {}
    return {
        "reply": _extract_reply({"messages": values.get("messages", [])}) or _EMPTY_REPLY,
        "products": [],
        "ui_actions": [],
        "outfit": None,
        "tool_calls": [],
        "guardrail": values.get("guardrail"),
        "took_ms": 0,
        "error": False,
        "error_code": None,
        "retryable": False,
        "retry_after": None,
    }


def _seed_retry_context(snapshot: Any, ctx: ToolContext, turn: _Turn) -> None:
    """Restore what the before-agent hooks built before the failed model call.

    The resumed run starts at the node that failed, so ``before_agent`` does not run
    again: the registry (product refs) and the carried totals must come from the
    checkpoint. ``turn_context`` is untracked, so the router passes the page/product
    context again when it calls this function.
    """
    saved = snapshot.values or {}
    if saved.get("shown_products") is not None:
        ctx.seed(saved["shown_products"])
    elif len(saved.get("messages", [])) > 1:
        _seed_registry_from_checkpoint(ctx, turn.checkpointer, turn.thread_id)
    turn.carried = dict(saved.get("turn_carry") or {})


async def run_turn(
    message: str,
    *,
    db_url: str,
    session_id: str | None = None,
    page_asin: str | None = None,
    referenced_asins: list[str] | None = None,
    history: list[dict[str, str]] | None = None,
    fake: bool | None = None,
    checkpointer=None,
    thread_id: str | None = None,
    retry: bool = False,
) -> dict[str, Any]:
    """Run one copilot v2 turn.

    1. Deterministic guardrail → canned reply (zero LLM) if it fires.
    2. Otherwise invoke the create_agent agent with the tools + context middleware.

    ``retry=True`` resumes the thread's last failed node instead of adding another
    human message, so a retry cannot grow the context or duplicate the prompt.
    """
    turn = _Turn(
        started=time.perf_counter(),
        usage=LLMCallCounter(),
        thread_id=thread_id,
        session_id=session_id,
        db_url=db_url,
        fake=fake,
        checkpointer=checkpointer,
        is_retry=retry,
    )
    clean_message = sanitize_user_message(message)
    ctx, agent = await _prepare_agent(turn, page_asin=page_asin, referenced_asins=referenced_asins)
    config = _run_config(turn)

    if retry:
        snapshot = await asyncio.to_thread(agent.get_state, config)
        block = _retry_block(snapshot)
        if block is not None:
            code, text = block
            raise HTTPException(
                status_code=404 if code == "thread_not_found" else 409,
                detail=text,
            )
        if not getattr(snapshot, "next", None):
            logger.info("copilot.retry.already_completed", extra={"thread_id": thread_id})
            return _completed_retry_summary(snapshot)
        _seed_retry_context(snapshot, ctx, turn)
        payload = None
    else:
        payload = {"messages": _turn_input(clean_message, message, history, checkpointer)}

    if not _begin_thread_run(thread_id):
        raise HTTPException(
            status_code=409,
            detail="Permintaan sebelumnya masih diproses. Coba lagi sebentar.",
        )

    try:
        try:
            if checkpointer is not None:
                # The sync PostgresSaver doesn't implement async methods, so run the
                # synchronous graph in a worker thread to keep the route async.
                result = await asyncio.to_thread(agent.invoke, payload, config)
            else:
                result = await agent.ainvoke(payload, config=config)
        except LLMUnavailable as e:
            _finish_turn(turn, reply="", error=True, failure=_classify_failure(e))
            raise HTTPException(status_code=503, detail=_LLM_UNAVAILABLE_DETAIL) from None
        except Exception as e:
            failure = _classify_failure(e)
            logger.error("copilot agent error: %s code=%s", e, failure.code, exc_info=True)
            _finish_turn(turn, reply="", error=True, failure=failure)
            raise HTTPException(status_code=502, detail=_LLM_NETWORK_DETAIL) from None
    finally:
        _end_thread_run(thread_id)

    items = approval_from_result(result)
    if items is not None:
        return _pause_turn(turn, ctx, result, items)

    reply = _extract_reply(result) or _EMPTY_REPLY
    return _finish_turn(turn, reply=reply, ctx=ctx, result=result, guardrail=result.get("guardrail"))


async def resume_turn(
    thread_id: str,
    *,
    approve: bool,
    db_url: str,
    session_id: str | None = None,
    fake: bool | None = None,
    checkpointer=None,
) -> dict[str, Any]:
    """Answer a pending cart approval and finish the paused turn.

    The question the shopper saw is read back from the saved thread; the only thing the
    caller supplies is yes or no. Raises ``NoPendingApproval`` if nothing is waiting.
    """
    turn = _Turn(
        started=time.perf_counter(),
        usage=LLMCallCounter(),
        thread_id=thread_id,
        session_id=session_id,
        db_url=db_url,
        fake=fake,
        checkpointer=checkpointer,
    )
    ctx, agent = await _prepare_agent(turn, page_asin=None, referenced_asins=None)
    config = _run_config(turn)

    snapshot = await asyncio.to_thread(agent.get_state, config)
    request = pending_request(snapshot)
    if request is None:
        raise NoPendingApproval(thread_exists=bool(getattr(snapshot, "values", None)))

    # The resumed run starts inside the paused node, so the before-agent steps do not run again:
    # restore what the rest of the turn needs from the saved state.
    saved = snapshot.values
    ctx.seed(saved.get("shown_products") or [])
    turn.carried = dict(saved.get("turn_carry") or {})

    decisions = approval_decisions(request, approve=approve)
    logger.info(
        "copilot.approval.answered",
        extra={
            "thread_id": thread_id,
            "approved": approve,
            "asins": [str(item.get("asin")) for item in pending_items(request)],
        },
    )
    if not _begin_thread_run(thread_id):
        raise HTTPException(
            status_code=409,
            detail="Permintaan sebelumnya masih diproses. Coba lagi sebentar.",
        )
    try:
        try:
            result = await asyncio.to_thread(agent.invoke, Command(resume={"decisions": decisions}), config)
        except LLMUnavailable as e:
            _finish_turn(turn, reply="", error=True, failure=_classify_failure(e))
            raise HTTPException(status_code=503, detail=_LLM_UNAVAILABLE_DETAIL) from None
        except Exception as e:
            failure = _classify_failure(e)
            logger.error("copilot resume error: %s code=%s", e, failure.code, exc_info=True)
            _finish_turn(turn, reply="", error=True, failure=failure)
            raise HTTPException(status_code=502, detail=_LLM_NETWORK_DETAIL) from None
    finally:
        _end_thread_run(thread_id)

    items = approval_from_result(result)
    if items is not None:  # the model asked for another cart add
        return _pause_turn(turn, ctx, result, items)
    reply = _extract_reply(result) or _EMPTY_REPLY
    return _finish_turn(turn, reply=reply, ctx=ctx, result=result, guardrail=result.get("guardrail"))


async def stream_turn(
    message: str,
    *,
    db_url: str,
    session_id: str | None = None,
    page_asin: str | None = None,
    referenced_asins: list[str] | None = None,
    history: list[dict[str, str]] | None = None,
    fake: bool | None = None,
    checkpointer=None,
    thread_id: str | None = None,
    retry: bool = False,
):
    """Stream one copilot v2 turn as event dicts (`token` and `done`)."""
    turn = _Turn(
        started=time.perf_counter(),
        usage=LLMCallCounter(),
        thread_id=thread_id,
        session_id=session_id,
        db_url=db_url,
        fake=fake,
        checkpointer=checkpointer,
        is_retry=retry,
    )
    clean_message = sanitize_user_message(message)
    ctx, agent = await _prepare_agent(turn, page_asin=page_asin, referenced_asins=referenced_asins)
    config = _run_config(turn)

    if retry:
        try:
            snapshot = await asyncio.to_thread(agent.get_state, config)
        except Exception as exc:
            failure = _classify_failure(exc)
            logger.error("copilot retry preflight error: %s", exc, exc_info=True)
            yield {
                "type": "done",
                "thread_id": thread_id,
                **_error_summary(
                    failure.code,
                    _STREAM_ERROR_REPLY,
                    retryable=failure.retryable,
                    retry_after=failure.retry_after,
                ),
            }
            return
        block = _retry_block(snapshot)
        if block is not None:
            code, text = block
            yield {"type": "done", "thread_id": thread_id, **_error_summary(code, text)}
            return
        if not getattr(snapshot, "next", None):
            logger.info("copilot.retry.already_completed", extra={"thread_id": thread_id})
            yield {"type": "done", "thread_id": thread_id, **_completed_retry_summary(snapshot)}
            return
        _seed_retry_context(snapshot, ctx, turn)
        payload = None
    else:
        payload = {"messages": _turn_input(clean_message, message, history, checkpointer)}

    if not _begin_thread_run(thread_id):
        yield {
            "type": "done",
            "thread_id": thread_id,
            **_error_summary(
                "retry_in_progress",
                "Permintaan sebelumnya masih diproses. Coba lagi sebentar.",
                retryable=True,
                retry_after=2,
            ),
        }
        return

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

    def _worker():
        try:
            for mode, chunk in agent.stream(payload, config=config, stream_mode=["messages", "values", "updates"]):
                loop.call_soon_threadsafe(queue.put_nowait, (mode, chunk))
            loop.call_soon_threadsafe(queue.put_nowait, ("__eof__", None))
        except Exception as exc:
            loop.call_soon_threadsafe(queue.put_nowait, ("__err__", exc))
        finally:
            _end_thread_run(thread_id)

    # A plain Thread starts with an empty context, which would drop the request and trace
    # ids from every log line written by tools and model calls during a streamed turn.
    worker_context = contextvars.copy_context()
    try:
        threading.Thread(target=worker_context.run, args=(_worker,), daemon=True).start()
    except BaseException:
        _end_thread_run(thread_id)
        raise

    final_result: dict[str, Any] = {}
    streamed_parts: list[str] = []

    while True:
        mode, event = await queue.get()
        if mode == "__eof__":
            break
        if mode == "__err__":
            failure = _classify_failure(event)
            if isinstance(event, LLMUnavailable):
                _finish_turn(turn, reply="", error=True, failure=failure)
                raise HTTPException(status_code=503, detail=_LLM_UNAVAILABLE_DETAIL)
            logger.error(
                "copilot stream error: %s code=%s retry_after=%s",
                event,
                failure.code,
                failure.retry_after,
                exc_info=event,
            )
            summary = _finish_turn(
                turn,
                reply="".join(streamed_parts) or _STREAM_ERROR_REPLY,
                error=True,
                failure=failure,
            )
            summary["partial"] = bool(streamed_parts)
            if not streamed_parts:
                yield {"type": "token", "content": _STREAM_ERROR_REPLY}
            yield {"type": "done", "thread_id": thread_id, **summary}
            return
        if mode == "messages":
            msg_chunk, meta = event
            if (
                isinstance(msg_chunk, AIMessage)
                and (meta or {}).get("langgraph_node") == "model"
                and not getattr(msg_chunk, "tool_calls", None)
                and not getattr(msg_chunk, "tool_call_chunks", None)
            ):
                txt = _chunk_text(msg_chunk)
                if txt:
                    streamed_parts.append(txt)
                    yield {"type": "token", "content": txt}
        elif mode == "values" and isinstance(event, dict):
            final_result = event
        elif mode == "updates":
            for status in _status_events(event):
                logger.debug("copilot.stream.status", extra={"thread_id": thread_id, "label": status["label"]})
                yield status

    items = approval_from_result(final_result)
    if items is not None:
        summary = _pause_turn(turn, ctx, final_result, items)
        if not streamed_parts and summary["reply"]:
            yield {"type": "token", "content": summary["reply"]}
        yield {"type": "approval_required", "thread_id": thread_id, "items": items}
        yield {"type": "done", "thread_id": thread_id, **summary}
        return

    reply = _extract_reply(final_result) or "".join(streamed_parts) or _EMPTY_REPLY
    if not streamed_parts and reply:
        yield {"type": "token", "content": reply}

    summary = _finish_turn(turn, reply=reply, ctx=ctx, result=final_result, guardrail=final_result.get("guardrail"))
    yield {"type": "done", "thread_id": thread_id, **summary}


def is_llm_available() -> bool:
    return llm_available()
