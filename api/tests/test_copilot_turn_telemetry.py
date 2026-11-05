"""Per-turn telemetry: turn-scoped tool/token accounting, model-call counting, reference pricing.

The integration test needs the local Postgres (it migrates it to head, which is idempotent).
"""

import asyncio
import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

import copilot_agent
import copilot_metrics
import psycopg
import pytest
from copilot_agent import _current_turn
from copilot_llm import fake_tool_calling_model
from guardrail import classify_with_llm
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from llm_usage import LLMCallCounter

DB_URL = os.environ.get("DATABASE_URL", "postgresql://toko:toko@localhost:5432/toko")


# ── _current_turn ────────────────────────────────────────────────────────────

def test_current_turn_excludes_earlier_turns():
    history = [
        HumanMessage(content="tolong catat pertanyaan ini"),
        AIMessage(content="", tool_calls=[{"name": "flag_unanswered", "args": {}, "id": "1"}]),
        ToolMessage(content="{}", tool_call_id="1"),
        AIMessage(content="sudah ya kak"),
        HumanMessage(content="ongkir berapa?"),
        AIMessage(content="Rp 15.000"),
    ]
    turn = _current_turn(history)
    names = [tc["name"] for m in turn for tc in (getattr(m, "tool_calls", None) or [])]
    assert names == []
    assert len(turn) == 2


def test_current_turn_returns_everything_without_a_human_message():
    messages = [AIMessage(content="x")]
    assert _current_turn(messages) == messages


# ── LLMCallCounter ───────────────────────────────────────────────────────────

def _llm_result(input_tokens: int, output_tokens: int):
    message = AIMessage(
        content="x",
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
    )
    return SimpleNamespace(generations=[[SimpleNamespace(message=message)]])


def test_counter_counts_calls_and_sums_tokens():
    counter = LLMCallCounter()
    counter.on_chat_model_start({}, [[]])
    counter.on_chat_model_start({}, [[]])
    counter.on_llm_end(_llm_result(10, 4))
    counter.on_llm_end(_llm_result(5, 1))
    assert counter.calls == 2
    assert counter.usage == (15, 5)


def test_counter_reports_unknown_usage_as_none_not_zero():
    counter = LLMCallCounter()
    counter.on_chat_model_start({}, [[]])
    assert counter.calls == 1
    assert counter.usage == (None, None)


def test_guardrail_llm_call_is_counted():
    counter = LLMCallCounter()
    model = FakeListChatModel(responses=["ON_TOPIC"])
    result = classify_with_llm("ada celana chino hitam?", model=model, callbacks=[counter])
    assert result == (None, None)
    assert counter.calls == 1


def test_guardrail_without_callbacks_still_works():
    model = FakeListChatModel(responses=["GREETING"])
    label, _ = classify_with_llm("halo", model=model)
    assert label == "greeting"


# ── Reference pricing ────────────────────────────────────────────────────────

def _settings(reference: str):
    return SimpleNamespace(
        LLM_INPUT_COST_PER_MILLION=0.15,
        LLM_OUTPUT_COST_PER_MILLION=0.60,
        COST_REFERENCE_MODEL=reference,
    )


def test_reference_model_overrides_placeholder_prices(monkeypatch):
    monkeypatch.setattr(copilot_metrics, "get_settings", lambda: _settings("ministral-3-8b"))
    assert copilot_metrics.estimate_cost_usd(1_000_000, 0) == 0.15
    assert copilot_metrics.estimate_cost_usd(0, 1_000_000) == 0.15


def test_unknown_reference_falls_back_to_configured_prices(monkeypatch):
    monkeypatch.setattr(copilot_metrics, "get_settings", lambda: _settings("nope"))
    assert copilot_metrics.estimate_cost_usd(0, 1_000_000) == 0.60


def test_empty_reference_keeps_legacy_behaviour(monkeypatch):
    monkeypatch.setattr(copilot_metrics, "get_settings", lambda: _settings(""))
    assert copilot_metrics.estimate_cost_usd(0, 1_000_000) == 0.60


def test_cost_is_unknown_not_zero_without_tokens(monkeypatch):
    monkeypatch.setattr(copilot_metrics, "get_settings", lambda: _settings("ministral-3-8b"))
    assert copilot_metrics.estimate_cost_usd(None, None) is None


# ── Integration: a later turn must not inherit an earlier turn's telemetry ───

@pytest.fixture
def migrated_db():
    from migrate import run_migrations

    try:
        run_migrations(DB_URL)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"database unavailable: {exc}")


def _usage(input_tokens: int, output_tokens: int) -> dict:
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }


def test_turn_two_does_not_inherit_turn_one_telemetry(migrated_db, monkeypatch):
    import copilot_memory

    saver = copilot_memory.get_checkpointer()
    if saver is None:
        pytest.skip("checkpointer unavailable")

    scripts = iter(
        [
            # Turn 1: the model flags a question it cannot answer, then replies.
            [
                AIMessage(
                    content="",
                    tool_calls=[{
                        "name": "flag_unanswered",
                        "args": {"question": "stok gudang bandung?", "reason": "not in catalog"},
                        "id": "c1",
                    }],
                    usage_metadata=_usage(100, 10),
                ),
                AIMessage(content="Maaf kak, mimin belum yakin.", usage_metadata=_usage(120, 12)),
            ],
            # Turn 2: a plain answer with no tools.
            [AIMessage(content="Halo lagi kak!", usage_metadata=_usage(50, 5))],
        ]
    )
    monkeypatch.setattr(
        copilot_agent,
        "build_chat_model",
        lambda temperature=0.2, fake=None: fake_tool_calling_model(next(scripts)),
    )

    thread_id = f"t-turn-scope-{uuid.uuid4().hex[:8]}"

    async def go():
        for message in ("stok kaos di gudang bandung berapa?", "oke makasih infonya"):
            await copilot_agent.run_turn(
                message, db_url=DB_URL, session_id="s-turn-scope", thread_id=thread_id,
                fake=True, checkpointer=saver,
            )

    try:
        asyncio.run(go())
        with psycopg.connect(DB_URL) as conn:
            rows = conn.execute(
                "SELECT outcome, tool_names, input_tokens, output_tokens, llm_calls, route "
                "FROM copilot_turns WHERE thread_id = %s ORDER BY id",
                (thread_id,),
            ).fetchall()
    finally:
        copilot_memory.delete_thread(thread_id, db_url=DB_URL)
        with psycopg.connect(DB_URL) as conn:
            conn.execute("DELETE FROM copilot_turns WHERE thread_id = %s", (thread_id,))
            conn.commit()

    assert len(rows) == 2
    first, second = rows
    assert first[0] == "unanswered" and list(first[1]) == ["flag_unanswered"]
    assert (first[2], first[3], first[4], first[5]) == (220, 22, 2, "agent")

    # Before the fix this row said outcome "unanswered", the earlier tool call, and 270 tokens.
    assert second[0] == "answered"
    assert list(second[1]) == []
    assert (second[2], second[3], second[4], second[5]) == (50, 5, 1, "agent")
