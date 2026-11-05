"""The model-call cap: shopper-facing reply, recorded as an unanswered question."""

import asyncio
import logging
import os
import sys
import uuid
from pathlib import Path

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

import copilot_agent
import copilot_memory
import psycopg
import pytest
from copilot_agent import LOOP_CAP_REPLY
from copilot_llm import fake_tool_calling_model
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

DB_URL = os.environ.get("DATABASE_URL", "postgresql://toko:toko@localhost:5432/toko")
QUESTION = "carikan sesuatu yang tidak ada ya"


@pytest.fixture(autouse=True)
def _migrated_db():
    from migrate import run_migrations

    try:
        run_migrations(DB_URL)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"database unavailable: {exc}")


@tool
def ping() -> str:
    """Return pong."""
    return "pong"


def _tool_call(call_id: str) -> AIMessage:
    # Non-empty text: the fake model yields no stream chunks for a message that is only tool calls.
    return AIMessage(content="Sebentar ya kak.", tool_calls=[{"name": "ping", "args": {}, "id": call_id}])


def _script(monkeypatch, *messages: AIMessage) -> None:
    monkeypatch.setattr(copilot_agent, "build_tools", lambda ctx, categories=None: [ping])
    monkeypatch.setattr(
        copilot_agent, "build_chat_model", lambda temperature=0.2, fake=None: fake_tool_calling_model(list(messages))
    )


def _row(thread_id: str):
    with psycopg.connect(DB_URL) as conn:
        return conn.execute(
            "SELECT outcome, unanswered_question, route, llm_calls, tool_names FROM copilot_turns "
            "WHERE thread_id = %s ORDER BY id",
            (thread_id,),
        ).fetchall()


def _cleanup(thread_id: str) -> None:
    copilot_memory.delete_thread(thread_id, db_url=DB_URL)
    with psycopg.connect(DB_URL) as conn:
        conn.execute("DELETE FROM copilot_turns WHERE thread_id = %s", (thread_id,))
        conn.commit()


def _thread_id() -> str:
    return f"t-cap-{uuid.uuid4().hex[:6]}"


def test_capped_turn_replies_in_indonesian_and_is_recorded_as_unanswered(monkeypatch, caplog):
    _script(monkeypatch, *[_tool_call(f"c{i}") for i in range(6)])
    caplog.set_level(logging.INFO, logger="copilot_agent")
    thread_id = _thread_id()
    try:
        result = asyncio.run(copilot_agent.run_turn(QUESTION, db_url=DB_URL, fake=True, thread_id=thread_id))
        (row,) = _row(thread_id)
    finally:
        _cleanup(thread_id)

    assert result["reply"] == LOOP_CAP_REPLY
    assert "Model call limits exceeded" not in result["reply"]
    outcome, question, route, llm_calls, tool_names = row
    assert (outcome, question, route, llm_calls) == ("unanswered", QUESTION, "agent", 3)
    assert list(tool_names) == ["ping"]

    (warning,) = [r for r in caplog.records if r.getMessage() == "copilot.turn.loop_cap"]
    assert warning.thread_id == thread_id and warning.llm_calls == 3
    assert QUESTION not in str(warning.__dict__)


def test_capped_stream_turn_sends_the_fallback_as_the_reply(monkeypatch):
    """stream_turn must report the cap message as the reply and record the turn as unanswered.

    The fake model cannot stream tool calls (it yields no tool-call chunks), so a stand-in agent
    delivers the final state a capped real run produces; the sync tests above cover the graph.
    """
    capped_state = {
        "messages": [
            HumanMessage(content=QUESTION),
            AIMessage(content="Sebentar ya kak.", tool_calls=[{"name": "ping", "args": {}, "id": "c0"}]),
            ToolMessage(content="pong", tool_call_id="c0"),
            AIMessage(content=LOOP_CAP_REPLY, additional_kwargs={"loop_cap": True}),
        ]
    }

    class CappedAgent:
        def stream(self, payload, config=None, stream_mode=None):
            yield "values", capped_state

    monkeypatch.setattr(copilot_agent, "build_agent", lambda *a, **k: CappedAgent())
    thread_id = _thread_id()

    async def collect():
        return [e async for e in copilot_agent.stream_turn(QUESTION, db_url=DB_URL, fake=True, thread_id=thread_id)]

    try:
        events = asyncio.run(collect())
        (row,) = _row(thread_id)
    finally:
        _cleanup(thread_id)

    assert events[-1]["type"] == "done"
    assert events[-1]["reply"] == LOOP_CAP_REPLY
    assert row[0] == "unanswered" and row[1] == QUESTION


def test_a_turn_that_finishes_within_the_cap_is_answered(monkeypatch, caplog):
    _script(monkeypatch, _tool_call("c0"), AIMessage(content="Sudah ketemu kak."))
    caplog.set_level(logging.INFO, logger="copilot_agent")
    thread_id = _thread_id()
    try:
        result = asyncio.run(copilot_agent.run_turn(QUESTION, db_url=DB_URL, fake=True, thread_id=thread_id))
        (row,) = _row(thread_id)
    finally:
        _cleanup(thread_id)

    assert result["reply"] == "Sudah ketemu kak."
    assert row[0] == "answered" and row[1] is None and row[3] == 2
    assert not [r for r in caplog.records if r.getMessage() == "copilot.turn.loop_cap"]


def test_the_turn_after_a_capped_turn_is_not_marked_capped(monkeypatch):
    """The cap marker stays in the saved thread; the next turn must only look at its own messages."""
    saver = copilot_memory.get_checkpointer()
    if saver is None:
        pytest.skip("checkpointer unavailable")

    scripts = iter(
        [[_tool_call(f"c{i}") for i in range(6)], [AIMessage(content="Halo lagi kak!")]]
    )
    monkeypatch.setattr(copilot_agent, "build_tools", lambda ctx, categories=None: [ping])
    monkeypatch.setattr(
        copilot_agent, "build_chat_model", lambda temperature=0.2, fake=None: fake_tool_calling_model(next(scripts))
    )

    thread_id = _thread_id()

    async def go():
        for message in (QUESTION, "oke, makasih infonya"):
            await copilot_agent.run_turn(
                message, db_url=DB_URL, fake=True, thread_id=thread_id, checkpointer=saver
            )

    try:
        asyncio.run(go())
        rows = _row(thread_id)
    finally:
        _cleanup(thread_id)

    assert [r[0] for r in rows] == ["unanswered", "answered"]
    assert rows[1][1] is None


def test_loop_cap_middleware_keeps_the_library_counting():
    """Three model calls are still allowed, and the graph exits on the fourth attempt."""
    assert copilot_agent.MAX_MODEL_CALLS == 3
    middleware = copilot_agent.LoopCapMiddleware(run_limit=copilot_agent.MAX_MODEL_CALLS, exit_behavior="end")
    assert middleware.run_limit == 3 and middleware.exit_behavior == "end"
