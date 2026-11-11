"""Orchestration regressions: no double guardrail call, blocking work off the event loop."""

import asyncio
import os
import sys
import threading
from pathlib import Path

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

import copilot_agent
import copilot_middleware
import guardrail
from fastapi.testclient import TestClient
from main import app


def test_stream_route_classifies_with_regex_only(monkeypatch):
    """The stream route must not make a model call: stream_turn does the real classification."""
    calls: list[dict] = []

    def recording_classify(message, history=None, *, use_llm=True, model=None, **kwargs):
        calls.append({"use_llm": use_llm})
        return (None, None)

    async def fake_stream_turn(message, **kwargs):
        yield {"type": "done", "reply": "ok", "thread_id": kwargs.get("thread_id")}

    monkeypatch.setenv("COPILOT_FAKE_LLM", "1")
    monkeypatch.setattr(guardrail, "classify", recording_classify)
    monkeypatch.setattr(copilot_agent, "stream_turn", fake_stream_turn)

    response = TestClient(app).post(
        "/copilot/chat/stream",
        json={"message": "ada celana chino hitam?", "thread_id": "t-orch-1", "session_id": "s-orch-0001"},
    )

    assert response.status_code == 200
    assert calls == [{"use_llm": False}]


def test_stream_route_503_check_still_works_for_greeting_without_llm(monkeypatch):
    """Regex-only classification keeps greetings answerable when no LLM key is configured."""
    monkeypatch.delenv("COPILOT_FAKE_LLM", raising=False)
    monkeypatch.setattr(copilot_agent, "is_llm_available", lambda: False)

    async def fake_stream_turn(message, **kwargs):
        yield {"type": "done", "reply": "halo", "thread_id": None}

    monkeypatch.setattr(copilot_agent, "stream_turn", fake_stream_turn)

    greeting = TestClient(app).post(
        "/copilot/chat/stream",
        json={"message": "halo kak", "thread_id": "t-orch-2", "session_id": "s-orch-0002"},
    )
    assert greeting.status_code == 200

    question = TestClient(app).post(
        "/copilot/chat/stream",
        json={"message": "ada celana chino hitam?", "thread_id": "t-orch-3", "session_id": "s-orch-0003"},
    )
    assert question.status_code == 503


def test_run_turn_classifies_off_the_event_loop(monkeypatch):
    seen: dict[str, bool] = {}
    loop_thread = {}

    def spy_classify(message, history=None, *, use_llm=True, model=None, **kwargs):
        seen["off_loop"] = threading.current_thread() is not loop_thread["id"]
        return ("greeting", "halo")

    monkeypatch.setattr(copilot_middleware, "classify", spy_classify)

    async def go():
        loop_thread["id"] = threading.current_thread()
        return await copilot_agent.run_turn(
            "halo kak",
            db_url=os.environ.get("DATABASE_URL", "postgresql://toko:toko@localhost:5432/toko"),
            fake=True,
        )

    result = asyncio.run(go())
    assert result["guardrail"] == "greeting"
    assert seen["off_loop"] is True
