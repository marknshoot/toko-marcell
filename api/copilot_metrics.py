"""Best-effort telemetry for copilot turns.

Phase 9 writes one ``copilot_turns`` row per turn; phase 10 adds the honest
``unanswered`` outcome and stores the shopper's question (capped at 300 chars).
The aggregate is exposed in ``GET /events/summary``. Measurement must never
break a shopper's chat, so every database operation here is defensive: failures
are logged and swallowed.

The token extractor reads LangChain's standard ``AIMessage.usage_metadata``
(``input_tokens`` / ``output_tokens``). Providers that do not return usage simply
produce ``None``; phase 13 can refine cost estimates when billing data is wired.
"""

import logging
from collections.abc import Iterable, Sequence
from typing import Any

from langchain_core.messages import AIMessage

logger = logging.getLogger(__name__)

OUTCOMES = {"answered", "guardrail", "unanswered", "cart", "error"}


def derive_outcome(
    guardrail: str | None,
    tool_names: Sequence[str] | None,
    reply_error: bool | str | None = False,
) -> str:
    """Map one turn's evidence to the ``copilot_turns.outcome`` vocabulary.

    Precedence: error → guardrail → cart → unanswered flag → answered.
    A cart action wins over a fallback flag because the shopper still received a
    concrete, server-validated outcome.
    """
    if reply_error:
        return "error"
    if guardrail:
        return "guardrail"
    names = {str(name) for name in (tool_names or []) if name}
    if "add_to_cart" in names:
        return "cart"
    if "flag_unanswered" in names:
        return "unanswered"
    return "answered"


def extract_token_usage(messages: Iterable[Any] | None) -> tuple[int | None, int | None]:
    """Sum ``input_tokens`` / ``output_tokens`` across AIMessages.

    Returns ``(None, None)`` when no message carries usage metadata. If only one
    side is reported, the other side is ``None`` rather than an invented zero.
    """
    input_total = 0
    output_total = 0
    saw_input = False
    saw_output = False

    for message in messages or []:
        if not isinstance(message, AIMessage):
            continue
        usage = getattr(message, "usage_metadata", None)
        if not isinstance(usage, dict):
            continue
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        if input_tokens is not None:
            input_total += int(input_tokens)
            saw_input = True
        if output_tokens is not None:
            output_total += int(output_tokens)
            saw_output = True

    return (input_total if saw_input else None, output_total if saw_output else None)


def _connect(db_url: str | None):
    """Keep the import lazy so this module stays cheap to import in unit tests."""
    from agent_tools import _connect as connect

    return connect(db_url)


def record_turn(
    *,
    thread_id: str | None,
    session_id: str | None,
    db_url: str | None,
    outcome: str,
    guardrail: str | None = None,
    tool_names: Sequence[str] | None = None,
    latency_ms: float | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    model: str | None = None,
    cost_usd: float | None = None,
    unanswered_question: str | None = None,
) -> None:
    """Insert one turn row. Never raises: telemetry must not break chat."""
    if outcome not in OUTCOMES:
        logger.warning("[copilot_metrics] ignoring invalid outcome %r", outcome)
        return

    question = (unanswered_question or "").strip()[:300] or None

    try:
        with _connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO copilot_turns
                        (thread_id, session_id, guardrail, outcome, tool_names,
                         latency_ms, input_tokens, output_tokens, model, cost_usd,
                         unanswered_question)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        thread_id,
                        session_id,
                        guardrail,
                        outcome,
                        [str(name) for name in (tool_names or []) if name],
                        latency_ms,
                        input_tokens,
                        output_tokens,
                        model,
                        cost_usd,
                        question,
                    ),
                )
            conn.commit()
    except Exception as e:
        logger.warning("[copilot_metrics] record_turn failed: %s", e)
