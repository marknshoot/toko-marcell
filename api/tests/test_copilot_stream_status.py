"""Progress events while the agent works: status events before the answer, tokens unchanged."""

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

import copilot_agent
import psycopg
import pytest
from copilot_agent import AFTER_TOOLS_LABEL, TOOL_STATUS_LABELS, _status_events
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import tool

DB_URL = os.environ.get("DATABASE_URL", "postgresql://toko:toko@localhost:5432/toko")


class WordStreamingScripted(BaseChatModel):
    """Streams scripted messages like a provider: the text word by word, then the tool calls."""

    script: list
    pos: int = 0

    @property
    def _llm_type(self) -> str:
        return "word-streaming-scripted"

    def bind_tools(self, tools, **kwargs):  # noqa: ARG002
        return self

    def _next(self):
        message = self.script[self.pos]
        self.pos += 1
        return message

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=self._next())])

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        message = self._next()
        words = message.content.split(" ") if message.content else []
        for index, word in enumerate(words):
            yield ChatGenerationChunk(message=AIMessageChunk(content=word + (" " if index < len(words) - 1 else "")))
        for index, call in enumerate(message.tool_calls):
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {"name": call["name"], "args": json.dumps(call["args"]), "id": call["id"], "index": index}
                    ],
                )
            )
        if not words and not message.tool_calls:
            yield ChatGenerationChunk(message=AIMessageChunk(content=""))


@pytest.fixture(autouse=True)
def _migrated_db():
    from migrate import run_migrations

    try:
        run_migrations(DB_URL)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"database unavailable: {exc}")


@tool
def search_catalog(query: str) -> str:
    """Stand-in catalog search."""
    return "hasil"


@tool
def lookup_store_policy(query: str) -> str:
    """Stand-in policy lookup."""
    return "kebijakan"


def _call(name: str, call_id: str, **args) -> dict:
    return {"name": name, "args": args or {"query": "x"}, "id": call_id}


def _stream_events(monkeypatch, *messages: AIMessage, message: str = "ada celana chino?") -> list[dict]:
    script = list(messages)
    monkeypatch.setattr(copilot_agent, "build_tools", lambda ctx, categories=None: [search_catalog, lookup_store_policy])
    monkeypatch.setattr(
        copilot_agent, "build_chat_model", lambda temperature=0.2, fake=None: WordStreamingScripted(script=script)
    )
    thread_id = f"t-status-{uuid.uuid4().hex[:6]}"

    async def collect():
        return [e async for e in copilot_agent.stream_turn(message, db_url=DB_URL, fake=True, thread_id=thread_id)]

    try:
        return asyncio.run(collect())
    finally:
        with psycopg.connect(DB_URL) as conn:
            conn.execute("DELETE FROM copilot_turns WHERE thread_id = %s", (thread_id,))
            conn.commit()


# ── The events a tool-calling turn produces ──────────────────────────────────

def test_status_names_the_tool_then_the_answer_follows(monkeypatch):
    events = _stream_events(
        monkeypatch,
        AIMessage(content="", tool_calls=[_call("search_catalog", "c1")]),
        AIMessage(content="Ini beberapa pilihan celana chino untuk kakak"),
    )
    types = [e["type"] for e in events]

    statuses = [e for e in events if e["type"] == "status"]
    assert [s["label"] for s in statuses] == [TOOL_STATUS_LABELS["search_catalog"], AFTER_TOOLS_LABEL]
    assert statuses[0]["tool"] == "search_catalog"
    assert types[-1] == "done"
    assert types.index("status") < types.index("token"), "progress must show before the answer starts"


def test_token_events_are_unchanged_by_the_status_events(monkeypatch):
    reply = "Ini beberapa pilihan celana chino untuk kakak"
    events = _stream_events(
        monkeypatch,
        AIMessage(content="", tool_calls=[_call("search_catalog", "c1")]),
        AIMessage(content=reply),
    )
    tokens = [e["content"] for e in events if e["type"] == "token"]

    assert len(tokens) >= 2, "the answer must still stream in pieces"
    assert "".join(tokens) == reply
    assert events[-1]["reply"] == reply
    assert all(set(e) == {"type", "content"} for e in events if e["type"] == "token")


def test_two_tools_in_one_message_each_get_a_label(monkeypatch):
    events = _stream_events(
        monkeypatch,
        AIMessage(content="", tool_calls=[_call("search_catalog", "c1"), _call("lookup_store_policy", "c2")]),
        AIMessage(content="Sudah mimin cek ya kak"),
    )
    labels = [e["label"] for e in events if e["type"] == "status"]
    assert labels[:2] == [TOOL_STATUS_LABELS["search_catalog"], TOOL_STATUS_LABELS["lookup_store_policy"]]
    assert labels[-1] == AFTER_TOOLS_LABEL


def test_a_plain_answer_has_no_status_events(monkeypatch):
    events = _stream_events(monkeypatch, AIMessage(content="Halo kak ada yang bisa dibantu"))
    assert [e["type"] for e in events if e["type"] != "token"] == ["done"]


def test_a_guardrail_turn_has_no_status_events(monkeypatch):
    events = _stream_events(monkeypatch, AIMessage(content="tidak dipakai"), message="halo kak")
    assert [e["type"] for e in events] == ["token", "done"]


# ── The label map ────────────────────────────────────────────────────────────

def test_every_copilot_tool_has_a_label():
    from routers.copilot import V2_TOOL_NAMES

    assert set(V2_TOOL_NAMES) <= set(TOOL_STATUS_LABELS)


def test_unknown_tools_and_other_steps_produce_nothing():
    unknown = AIMessage(content="", tool_calls=[{"name": "brand_new_tool", "args": {}, "id": "x"}])
    assert _status_events({"model": {"messages": [unknown]}}) == []
    assert _status_events({"GuardrailMiddleware.before_agent": {"guardrail": None}}) == []
    assert _status_events({"__interrupt__": ()}) == []
    assert _status_events("not a dict") == []


def test_status_labels_are_display_text_only():
    """The labels must not be able to influence the agent: they are plain strings."""
    assert all(isinstance(label, str) and label for label in TOOL_STATUS_LABELS.values())
