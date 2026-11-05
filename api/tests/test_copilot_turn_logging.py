"""Turn-level logging: one structured line per turn, tracebacks on failures, correlated stream logs.

Uses a stand-in agent object, so no model and no catalog search is involved. Telemetry rows are
written to the local Postgres and removed afterwards.
"""

import asyncio
import logging
import os
import sys
import uuid
from pathlib import Path

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

import copilot_agent
import psycopg
import pytest
from fastapi import HTTPException
from langchain_core.messages import AIMessage, HumanMessage
from logging_config import JSONLogFormatter
from logging_context import ctx_request_id

DB_URL = os.environ.get("DATABASE_URL", "postgresql://toko:toko@localhost:5432/toko")

SHOPPER_TEXT = "rahasia-pelanggan-kemeja-flannel-xyz"
REPLY_TEXT = "balasan-asisten-rahasia-abc"


@pytest.fixture(autouse=True)
def _migrated_and_clean():
    from migrate import run_migrations

    try:
        run_migrations(DB_URL)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"database unavailable: {exc}")


def _delete_rows(thread_id: str) -> None:
    with psycopg.connect(DB_URL) as conn:
        conn.execute("DELETE FROM copilot_turns WHERE thread_id = %s", (thread_id,))
        conn.commit()


class _StubAgent:
    """Just enough of a compiled agent for run_turn and stream_turn."""

    def __init__(self, *, reply: str = REPLY_TEXT, error: Exception | None = None, seen: list | None = None):
        self.reply = reply
        self.error = error
        self.seen = seen if seen is not None else []

    def _result(self):
        return {"messages": [HumanMessage(content=SHOPPER_TEXT), AIMessage(content=self.reply)]}

    async def ainvoke(self, payload, config=None):
        if self.error:
            raise self.error
        return self._result()

    def invoke(self, payload, config=None):
        if self.error:
            raise self.error
        return self._result()

    def stream(self, payload, config=None, stream_mode=None):
        self.seen.append(ctx_request_id.get())
        if self.error:
            raise self.error
        yield "values", self._result()


def _use_agent(monkeypatch, agent) -> None:
    monkeypatch.setattr(copilot_agent, "build_agent", lambda *a, **k: agent)


def _turn_lines(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == "copilot.turn.completed"]


def _collect(thread_id: str, message: str = SHOPPER_TEXT):
    async def go():
        return [
            e async for e in copilot_agent.stream_turn(message, db_url=DB_URL, fake=True, thread_id=thread_id)
        ]

    return asyncio.run(go())


# ── One structured line per turn ─────────────────────────────────────────────

def test_agent_turn_logs_one_line_with_ids_labels_and_counts(monkeypatch, caplog):
    _use_agent(monkeypatch, _StubAgent())
    thread_id = f"t-log-{uuid.uuid4().hex[:6]}"
    caplog.set_level(logging.INFO, logger="copilot_agent")
    try:
        asyncio.run(copilot_agent.run_turn(SHOPPER_TEXT, db_url=DB_URL, fake=True, thread_id=thread_id))
    finally:
        _delete_rows(thread_id)

    lines = _turn_lines(caplog)
    assert len(lines) == 1
    line = lines[0]
    assert line.thread_id == thread_id
    assert (line.turn_route, line.outcome, line.guardrail) == ("agent", "answered", None)
    assert line.llm_calls == 0  # the stub makes no model call
    assert line.tool_names == []
    assert isinstance(line.latency_ms, int | float)


def test_guardrail_turn_logs_one_line_with_the_label(caplog):
    thread_id = f"t-log-{uuid.uuid4().hex[:6]}"
    caplog.set_level(logging.INFO, logger="copilot_agent")
    try:
        asyncio.run(copilot_agent.run_turn("halo kak", db_url=DB_URL, fake=True, thread_id=thread_id))
    finally:
        _delete_rows(thread_id)

    (line,) = _turn_lines(caplog)
    assert (line.turn_route, line.outcome, line.guardrail) == ("guardrail", "guardrail", "greeting")


def test_streamed_turn_logs_exactly_one_line(monkeypatch, caplog):
    _use_agent(monkeypatch, _StubAgent())
    thread_id = f"t-log-{uuid.uuid4().hex[:6]}"
    caplog.set_level(logging.INFO, logger="copilot_agent")
    try:
        events = _collect(thread_id)
    finally:
        _delete_rows(thread_id)

    assert events[-1]["type"] == "done" and events[-1]["reply"] == REPLY_TEXT
    assert len(_turn_lines(caplog)) == 1


def test_turn_line_never_contains_the_message_or_the_reply(monkeypatch, caplog):
    _use_agent(monkeypatch, _StubAgent())
    thread_id = f"t-log-{uuid.uuid4().hex[:6]}"
    caplog.set_level(logging.DEBUG)
    try:
        asyncio.run(copilot_agent.run_turn(SHOPPER_TEXT, db_url=DB_URL, fake=True, thread_id=thread_id))
        _collect(thread_id, message=SHOPPER_TEXT)
    finally:
        _delete_rows(thread_id)

    rendered = [JSONLogFormatter().format(r) for r in caplog.records if r.name.startswith("copilot")]
    assert rendered, "expected copilot log records"
    for text in rendered:
        assert SHOPPER_TEXT not in text
        assert REPLY_TEXT not in text


# ── Tracebacks on failures ───────────────────────────────────────────────────

def test_agent_error_log_carries_the_traceback(monkeypatch, caplog):
    _use_agent(monkeypatch, _StubAgent(error=RuntimeError("boom")))
    thread_id = f"t-log-{uuid.uuid4().hex[:6]}"
    caplog.set_level(logging.INFO, logger="copilot_agent")
    try:
        with pytest.raises(HTTPException) as excinfo:
            asyncio.run(copilot_agent.run_turn("ada celana?", db_url=DB_URL, fake=True, thread_id=thread_id))
    finally:
        _delete_rows(thread_id)

    assert excinfo.value.status_code == 502
    (record,) = [r for r in caplog.records if r.getMessage().startswith("copilot agent error")]
    assert record.exc_info and record.exc_info[0] is RuntimeError
    assert "Traceback" in JSONLogFormatter().format(record)
    (line,) = _turn_lines(caplog)
    assert line.outcome == "error"


def test_stream_error_log_carries_the_traceback(monkeypatch, caplog):
    _use_agent(monkeypatch, _StubAgent(error=RuntimeError("stream boom")))
    thread_id = f"t-log-{uuid.uuid4().hex[:6]}"
    caplog.set_level(logging.INFO, logger="copilot_agent")
    try:
        events = _collect(thread_id, message="ada celana?")
    finally:
        _delete_rows(thread_id)

    assert [e["type"] for e in events] == ["token", "done"]
    (record,) = [r for r in caplog.records if r.getMessage().startswith("copilot stream error")]
    assert record.exc_info and record.exc_info[0] is RuntimeError
    assert "Traceback" in JSONLogFormatter().format(record)


# ── Request ids reach the stream worker thread ───────────────────────────────

def test_stream_worker_sees_the_request_id(monkeypatch):
    """The worker used to start with an empty context, so its logs lost request and trace ids."""
    seen: list = []
    _use_agent(monkeypatch, _StubAgent(seen=seen))
    thread_id = f"t-log-{uuid.uuid4().hex[:6]}"

    async def go():
        token = ctx_request_id.set("req_stream_check")
        try:
            return [
                e
                async for e in copilot_agent.stream_turn("ada celana?", db_url=DB_URL, fake=True, thread_id=thread_id)
            ]
        finally:
            ctx_request_id.reset(token)

    try:
        asyncio.run(go())
    finally:
        _delete_rows(thread_id)

    assert seen == ["req_stream_check"]
