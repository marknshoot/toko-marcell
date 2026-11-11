"""Per-turn bookkeeping shared by the sync and streaming entry points.

One ``_Turn`` accumulates what the turn has used (model calls, tokens, time) and
``_finish_turn`` is the *only* place that writes telemetry, so the sync and
streaming paths can never drift apart.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

from config import get_settings
from copilot_approval import CONFIRM_REPLY
from copilot_context import sanitize_user_message
from copilot_metrics import derive_outcome, estimate_cost_usd, extract_token_usage, record_turn
from copilot_prompts import _LOOP_CAP_MARKER, AFTER_TOOLS_LABEL, TOOL_STATUS_LABELS
from copilot_state import LLMUnavailable
from copilot_tools import ToolContext
from guardrail import trim_guardrail_history
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from llm_usage import LLMCallCounter

logger = logging.getLogger(__name__)


def _is_fake(fake: bool | None) -> bool:
    if fake is None:
        return os.environ.get("COPILOT_FAKE_LLM") == "1"
    return bool(fake)


def _turn_model(fake: bool | None) -> str | None:
    if _is_fake(fake):
        return "fake"
    try:
        return get_settings().LLM_MODEL or None
    except Exception:
        return None


def _tool_names(tool_calls: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for call in tool_calls:
        name = call.get("name")
        if name and name not in names:
            names.append(name)
    return names


def _extract_reply(result: dict[str, Any]) -> str:
    msgs = result.get("messages", [])
    for m in reversed(msgs):
        if isinstance(m, AIMessage) and m.content:
            if isinstance(m.content, str):
                return m.content
            if isinstance(m.content, list):
                return "".join(p if isinstance(p, str) else p.get("text", "") for p in m.content)
    return ""


def _current_turn(messages: list[Any]) -> list[Any]:
    """Messages produced by this turn: from the newest ``HumanMessage`` onward.

    With a checkpointer, ``result["messages"]`` holds the whole thread, so reading tool
    calls or token usage from it attributes earlier turns' work to this one. Tools only
    append ``ToolMessage``s, so the newest ``HumanMessage`` always marks this turn's start.
    """
    for index in range(len(messages) - 1, -1, -1):
        if isinstance(messages[index], HumanMessage):
            return messages[index:]
    return messages


def _status_events(update: Any) -> list[dict[str, str]]:
    """Progress events for one ``updates`` chunk of the graph stream.

    A chunk is ``{node_name: state_update}`` and arrives when that node has finished. When the model
    node finishes with tool calls, those tools are about to run, which is the moment to say which.
    When the tools node finishes, the model is about to write the answer.
    """
    if not isinstance(update, dict):
        return []
    events: list[dict[str, str]] = []
    model_update = update.get("model")
    if isinstance(model_update, dict):
        seen: set[str] = set()
        for message in model_update.get("messages") or []:
            for call in getattr(message, "tool_calls", None) or []:
                label = TOOL_STATUS_LABELS.get(call.get("name") or "")
                if label and label not in seen:
                    seen.add(label)
                    events.append({"type": "status", "label": label, "tool": str(call.get("name"))})
    if "tools" in update:
        events.append({"type": "status", "label": AFTER_TOOLS_LABEL})
    return events


@dataclass(frozen=True)
class Failure:
    """A turn failure in the shape the browser needs to offer a retry."""

    code: str
    retryable: bool
    retry_after: int | None = None


def _retry_after_seconds(exc: Exception) -> int | None:
    """``Retry-After`` from a provider error, clamped so a browser countdown stays sane."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    raw = headers.get("retry-after") or headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(1, min(int(float(raw)), 120))
    except (TypeError, ValueError):
        return None


def _classify_failure(exc: Exception) -> Failure:
    """Map a turn failure to a stable client-facing code.

    LangChain wraps OpenAI-compatible provider errors in subclasses of the ``openai``
    exceptions (``OpenAIRateLimitError`` etc.), so one ``isinstance`` check covers
    OpenRouter, Mistral and the generic fallback. Unknown 5xx/timeouts stay retryable;
    a 4xx or malformed response does not.
    """
    if isinstance(exc, LLMUnavailable):
        return Failure("llm_unavailable", False)
    try:
        import openai
    except Exception:  # pragma: no cover - openai is a hard dependency of the provider chain
        openai = None
    if openai is not None:
        if isinstance(exc, openai.RateLimitError):
            return Failure("provider_rate_limited", True, _retry_after_seconds(exc))
        if isinstance(exc, openai.APITimeoutError):
            return Failure("provider_timeout", True)
        if isinstance(exc, openai.APIConnectionError):
            return Failure("provider_unavailable", True)
        if isinstance(exc, openai.InternalServerError):
            return Failure("provider_error", True)
    status = getattr(exc, "status_code", None)
    if status == 429:
        return Failure("provider_rate_limited", True, _retry_after_seconds(exc))
    if isinstance(status, int) and status >= 500:
        return Failure("provider_error", True)
    return Failure("internal_error", False)


@dataclass
class _Turn:
    """Per-turn scope shared by ``run_turn`` and ``stream_turn``."""

    started: float
    usage: LLMCallCounter
    thread_id: str | None
    session_id: str | None
    db_url: str
    fake: bool | None
    checkpointer: Any = None
    # What an earlier request of this same turn used before a cart-approval pause.
    carried: dict[str, Any] = field(default_factory=dict)
    # True when this request resumes a failed turn instead of starting a new one.
    is_retry: bool = False

    def totals(self) -> dict[str, Any]:
        """Model calls, tokens and active time of the whole turn, earlier requests included."""
        in_new, out_new = self.usage.usage
        in_old, out_old = self.carried.get("input_tokens"), self.carried.get("output_tokens")
        return {
            "llm_calls": self.usage.calls + int(self.carried.get("llm_calls") or 0),
            "input_tokens": None if in_new is None and in_old is None else (in_new or 0) + (in_old or 0),
            "output_tokens": None if out_new is None and out_old is None else (out_new or 0) + (out_old or 0),
            "elapsed_ms": round(
                (time.perf_counter() - self.started) * 1000 + float(self.carried.get("elapsed_ms") or 0), 2
            ),
        }


def _finish_turn(
    turn: _Turn,
    *,
    reply: str,
    ctx: ToolContext | None = None,
    result: dict[str, Any] | None = None,
    guardrail: str | None = None,
    error: bool = False,
    failure: Failure | None = None,
) -> dict[str, Any]:
    """Record a finished turn once and return the response fields shared by every exit.

    Every way a turn can end (guardrail reply, agent unavailable, agent failure, normal
    answer) goes through here, so telemetry and the ``copilot.turn.completed`` log line
    cannot drift between the sync and streaming paths.
    """
    error = error or failure is not None
    turn_model = _turn_model(turn.fake)
    totals = turn.totals()
    input_tokens, output_tokens = totals["input_tokens"], totals["output_tokens"]

    tool_calls: list[dict[str, Any]] = []
    capped = False
    shopper_message = ""
    if result is not None:
        turn_messages = _current_turn(result.get("messages", []))
        if turn_messages and isinstance(turn_messages[0], HumanMessage):
            shopper_message = message_text(turn_messages[0])
        # A call whose result is an error (the shopper declined a cart add, or the arguments were
        # invalid) did not run, so it must not count as a tool the turn used.
        failed = {m.tool_call_id for m in turn_messages if isinstance(m, ToolMessage) and m.status == "error"}
        for m in turn_messages:
            for tc in getattr(m, "tool_calls", None) or []:
                if tc.get("id") in failed:
                    continue
                tool_calls.append({"name": tc.get("name"), "args": tc.get("args") or {}})
            if isinstance(m, AIMessage) and m.additional_kwargs.get(_LOOP_CAP_MARKER):
                capped = True
        if input_tokens is None and output_tokens is None:
            input_tokens, output_tokens = extract_token_usage(turn_messages)

    tool_names = _tool_names(tool_calls)
    outcome = derive_outcome(guardrail, tool_names, error)
    if capped and outcome == "answered":
        # The shopper got the fallback text, not an answer: count it with the questions we
        # could not answer so it shows up in the backlog. A cart action still wins.
        outcome = "unanswered"
        logger.warning(
            "copilot.turn.loop_cap",
            extra={"thread_id": turn.thread_id, "llm_calls": totals["llm_calls"], "tool_names": tool_names},
        )
    route = "guardrail" if guardrail else "agent"
    took_ms = totals["elapsed_ms"]

    record_turn(
        thread_id=turn.thread_id,
        session_id=turn.session_id,
        db_url=turn.db_url,
        guardrail=guardrail,
        outcome=outcome,
        tool_names=tool_names,
        latency_ms=took_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model=turn_model,
        cost_usd=estimate_cost_usd(input_tokens, output_tokens, turn_model),
        unanswered_question=(
            ((ctx.unanswered_question if ctx is not None else None) or (shopper_message if capped else None))
            if outcome == "unanswered"
            else None
        ),
        route=route,
        llm_calls=totals["llm_calls"],
        is_retry=turn.is_retry,
    )

    # Ids, labels and counts only. Never the shopper's message, tool arguments or the reply:
    # the log formatter masks credential-like keys, not content.
    logger.info(
        "copilot.turn.completed",
        extra={
            "thread_id": turn.thread_id,
            "turn_route": route,
            "guardrail": guardrail,
            "outcome": outcome,
            "llm_calls": totals["llm_calls"],
            "tool_names": tool_names,
            "latency_ms": took_ms,
            "model": turn_model,
            "is_retry": turn.is_retry,
        },
    )

    # Record activity for TTL cleanup (design §2). Only a turn that ran the graph is checkpointed.
    if result is not None and turn.checkpointer is not None and turn.thread_id:
        try:
            from copilot_memory import touch_thread

            touch_thread(turn.thread_id, db_url=turn.db_url)
        except Exception:
            pass

    return {
        "reply": reply,
        # Products shown during this turn (not merely rehydrated from history), capped at 6.
        "products": ctx.turn_products(limit=6) if (ctx is not None and result is not None) else [],
        "ui_actions": ctx.ui_actions if (ctx is not None and result is not None) else [],
        "outfit": ctx.outfit if (ctx is not None and result is not None) else None,
        "tool_calls": tool_calls,
        "guardrail": guardrail,
        "took_ms": took_ms,
        "error": error,
        "error_code": failure.code if failure is not None else None,
        "retryable": failure.retryable if failure is not None else False,
        "retry_after": failure.retry_after if failure is not None else None,
    }


def message_text(msg: Any) -> str:
    content = getattr(msg, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p if isinstance(p, str) else p.get("text", "") for p in content)
    return ""


def _pause_turn(turn: _Turn, ctx: ToolContext, result: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
    """Response for a turn that stopped to ask the shopper to confirm a cart add.

    No ``copilot_turns`` row is written: the turn is not finished. The request that resumes it
    records one row for the whole turn (the work done so far is saved in the thread state).

    ``ui_actions`` carries the cart adds this request has already run. That is empty for the first
    question of a turn, but a resumed run can pause again after its approved adds ran, and the
    browser only learns about those from this response.
    """
    logger.info(
        "copilot.approval.requested",
        extra={"thread_id": turn.thread_id, "asins": [str(item.get("asin")) for item in items]},
    )
    if turn.checkpointer is not None and turn.thread_id:
        try:
            from copilot_memory import touch_thread

            touch_thread(turn.thread_id, db_url=turn.db_url)
        except Exception:
            pass
    reply = _extract_reply({"messages": _current_turn(result.get("messages", []))}) or CONFIRM_REPLY
    return {
        "reply": reply,
        "products": ctx.turn_products(limit=6),
        "ui_actions": list(ctx.ui_actions),
        "outfit": ctx.outfit,
        "tool_calls": [],
        "guardrail": None,
        "took_ms": turn.totals()["elapsed_ms"],
        "pending_approval": {"items": items},
    }


def _turn_input(clean_message: str, message: str, history: list[dict[str, str]] | None, checkpointer: Any) -> list[Any]:
    """Messages for this turn.

    With a checkpointer, prior turns are restored by ``thread_id``, so only the new human
    message is passed; otherwise the caller's history is replayed first.
    """
    messages: list[Any] = []
    if checkpointer is None and history:
        for h in trim_guardrail_history(history):
            if h["role"] == "user":
                messages.append(HumanMessage(content=sanitize_user_message(h["content"])))
            elif h["role"] == "assistant":
                messages.append(AIMessage(content=h["content"]))
    messages.append(HumanMessage(content=clean_message or message))
    return messages
