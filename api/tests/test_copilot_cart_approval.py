"""Human approval before a cart add: pause, approve, reject, abandon, stream and HTTP."""

import asyncio
import json
import logging
import os
import sys
import uuid
from pathlib import Path

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

import copilot_agent
import copilot_llm
import psycopg
import pytest
from copilot_approval import _dangling_tool_calls
from copilot_llm import fake_tool_calling_model
from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from main import app

DB_URL = os.environ.get("DATABASE_URL", "postgresql://toko:toko@localhost:5432/toko")


# ── Fixtures and helpers ─────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _migrated_db():
    from migrate import run_migrations

    try:
        run_migrations(DB_URL)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"database unavailable: {exc}")


@pytest.fixture(scope="module")
def products():
    """Two real catalog products: (asin, title, price_idr)."""
    with psycopg.connect(DB_URL) as conn:
        rows = conn.execute(
            "SELECT asin, title, price_idr FROM products WHERE price_idr IS NOT NULL ORDER BY id LIMIT 2"
        ).fetchall()
    if len(rows) < 2:
        pytest.skip("catalog needs at least two products")
    return rows


class StreamingScripted(BaseChatModel):
    """Plays scripted AIMessages and streams them the way a provider does (text, then tool calls)."""

    script: list
    pos: int = 0

    @property
    def _llm_type(self) -> str:
        return "streaming-scripted"

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
        if message.content:
            yield ChatGenerationChunk(message=AIMessageChunk(content=message.content))
        for index, call in enumerate(message.tool_calls):
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {"name": call["name"], "args": json.dumps(call["args"]), "id": call["id"], "index": index}
                    ],
                )
            )
        if not message.content and not message.tool_calls:
            yield ChatGenerationChunk(message=AIMessageChunk(content=""))


def _thread_id() -> str:
    return f"t-appr-{uuid.uuid4().hex[:8]}"


def _cart_call(asin: str, call_id: str, **args) -> AIMessage:
    return AIMessage(
        content="Sebentar ya kak.",
        tool_calls=[{"name": "add_to_cart", "args": {"ref_or_asin": asin, **args}, "id": call_id}],
    )


def _script(monkeypatch, *messages: AIMessage, streaming: bool = False) -> None:
    script = list(messages)
    make = (lambda: StreamingScripted(script=script)) if streaming else (lambda: fake_tool_calling_model(script))
    monkeypatch.setattr(copilot_agent, "build_chat_model", lambda temperature=0.2, fake=None: make())


def _rows(thread_id: str):
    with psycopg.connect(DB_URL) as conn:
        return conn.execute(
            "SELECT outcome, tool_names, llm_calls, route FROM copilot_turns WHERE thread_id = %s ORDER BY id",
            (thread_id,),
        ).fetchall()


def _delete_rows(thread_id: str) -> None:
    with psycopg.connect(DB_URL) as conn:
        conn.execute("DELETE FROM copilot_turns WHERE thread_id = %s", (thread_id,))
        conn.commit()


def _run(coro):
    return asyncio.run(coro)


def _start(message: str, thread_id: str, saver, **kwargs):
    return _run(
        copilot_agent.run_turn(message, db_url=DB_URL, fake=True, thread_id=thread_id, checkpointer=saver, **kwargs)
    )


def _resume(thread_id: str, saver, *, approve: bool):
    return _run(copilot_agent.resume_turn(thread_id, approve=approve, db_url=DB_URL, fake=True, checkpointer=saver))


# ── The pause ────────────────────────────────────────────────────────────────

def test_a_cart_add_pauses_and_shows_the_item_the_server_resolved(monkeypatch, products):
    (asin, title, price), _ = products
    _script(monkeypatch, _cart_call(asin, "c1", qty=2, size="M"), AIMessage(content="Sudah masuk keranjang kak."))
    saver, thread_id = InMemorySaver(), _thread_id()
    try:
        result = _start("tambahkan yang itu ke keranjang", thread_id, saver)
        rows = _rows(thread_id)
    finally:
        _delete_rows(thread_id)

    (item,) = result["pending_approval"]["items"]
    assert (item["asin"], item["title"], item["priceIdr"]) == (asin, title, price)  # read from the database
    assert (item["qty"], item["size"]) == (2, "M")
    assert result["ui_actions"] == [], "nothing may reach the cart before the shopper answers"
    assert result["reply"] == "Sebentar ya kak."
    assert rows == [], "a paused turn is not finished, so no telemetry row yet"


def test_quantity_is_clamped_in_what_the_shopper_is_asked_to_confirm(monkeypatch, products):
    (asin, *_), _ = products
    _script(monkeypatch, _cart_call(asin, "c1", qty=5000))
    saver, thread_id = InMemorySaver(), _thread_id()
    try:
        result = _start("tambahkan ke keranjang", thread_id, saver)
    finally:
        _delete_rows(thread_id)
    assert result["pending_approval"]["items"][0]["qty"] == 99


def test_an_unknown_product_does_not_pause_and_the_tool_reports_it(monkeypatch):
    _script(monkeypatch, _cart_call("DOES-NOT-EXIST", "c1"), AIMessage(content="Maaf, produknya tidak ketemu."))
    saver, thread_id = InMemorySaver(), _thread_id()
    try:
        result = _start("tambahkan ke keranjang", thread_id, saver)
    finally:
        _delete_rows(thread_id)
    assert "pending_approval" not in result
    assert result["ui_actions"] == []
    assert result["reply"] == "Maaf, produknya tidak ketemu."


def test_without_a_checkpointer_a_cart_add_runs_straight_away(monkeypatch, products):
    """The fake-LLM test mode has no saved thread to pause, so behaviour there is unchanged."""
    (asin, *_), _ = products
    _script(monkeypatch, _cart_call(asin, "c1"), AIMessage(content="Sudah masuk keranjang."))
    result = _run(copilot_agent.run_turn("tambahkan ke keranjang", db_url=DB_URL, fake=True))
    assert "pending_approval" not in result
    assert [a["asin"] for a in result["ui_actions"]] == [asin]


# ── Answering ────────────────────────────────────────────────────────────────

def test_approving_adds_the_item_and_records_one_row_for_the_whole_turn(monkeypatch, products):
    (asin, title, price), _ = products
    _script(monkeypatch, _cart_call(asin, "c1", qty=2, size="M"))
    saver, thread_id = InMemorySaver(), _thread_id()
    try:
        _start("tambahkan yang itu ke keranjang", thread_id, saver)
        # A new graph instance, as in a separate HTTP request.
        _script(monkeypatch, AIMessage(content="Sudah masuk keranjang kak."))
        result = _resume(thread_id, saver, approve=True)
        rows = _rows(thread_id)
    finally:
        _delete_rows(thread_id)

    (action,) = result["ui_actions"]
    assert (action["type"], action["asin"], action["qty"], action["size"]) == ("add_to_cart", asin, 2, "M")
    assert (action["title"], action["priceIdr"]) == (title, price)
    assert result["reply"] == "Sudah masuk keranjang kak."
    assert "pending_approval" not in result

    assert len(rows) == 1
    outcome, tool_names, llm_calls, route = rows[0]
    assert (outcome, list(tool_names), route) == ("cart", ["add_to_cart"], "agent")
    assert llm_calls == 2, "one model call before the pause plus one after it"


def test_the_item_that_runs_is_the_one_the_shopper_was_shown(monkeypatch, products):
    """'produk ini' only resolves through the page product of the request that asked.

    The answer arrives in a different request that knows nothing about the page, so the cart add
    must run with the ASIN resolved at the pause, not re-resolve the model's words.
    """
    (asin, title, price), _ = products
    _script(monkeypatch, _cart_call("produk ini", "c1"))
    saver, thread_id = InMemorySaver(), _thread_id()
    try:
        paused = _start("tambahkan produk ini ke keranjang", thread_id, saver, page_asin=asin)
        _script(monkeypatch, AIMessage(content="Sudah masuk keranjang kak."))
        result = _resume(thread_id, saver, approve=True)
    finally:
        _delete_rows(thread_id)

    assert paused["pending_approval"]["items"][0]["asin"] == asin
    (action,) = result["ui_actions"]
    assert (action["asin"], action["title"], action["priceIdr"]) == (asin, title, price)


def test_rejecting_adds_nothing_and_the_model_acknowledges(monkeypatch, products):
    (asin, *_), _ = products
    _script(monkeypatch, _cart_call(asin, "c1"))
    saver, thread_id = InMemorySaver(), _thread_id()
    try:
        _start("tambahkan ke keranjang", thread_id, saver)
        _script(monkeypatch, AIMessage(content="Baik kak, tidak jadi ditambahkan."))
        result = _resume(thread_id, saver, approve=False)
        rows = _rows(thread_id)
    finally:
        _delete_rows(thread_id)

    assert result["ui_actions"] == []
    assert result["tool_calls"] == [], "a declined call did not run, so it is not a tool the turn used"
    assert result["reply"] == "Baik kak, tidak jadi ditambahkan."
    (outcome, tool_names, llm_calls, _route) = rows[0]
    assert (outcome, list(tool_names), llm_calls) == ("answered", [], 2)


def test_the_answer_cannot_be_given_twice_or_for_a_thread_with_nothing_pending(monkeypatch, products):
    from copilot_approval import NoPendingApproval

    (asin, *_), _ = products
    _script(monkeypatch, _cart_call(asin, "c1"))
    saver, thread_id = InMemorySaver(), _thread_id()
    try:
        _start("tambahkan ke keranjang", thread_id, saver)
        _script(monkeypatch, AIMessage(content="Beres."))
        _resume(thread_id, saver, approve=True)

        with pytest.raises(NoPendingApproval) as already:
            _resume(thread_id, saver, approve=True)
        with pytest.raises(NoPendingApproval) as unknown:
            _resume("t-never-existed", saver, approve=True)
    finally:
        _delete_rows(thread_id)

    assert already.value.thread_exists is True
    assert unknown.value.thread_exists is False


def test_two_cart_adds_in_one_message_are_confirmed_together(monkeypatch, products):
    (a, *_), (b, *_) = products
    both = AIMessage(
        content="Oke kak.",
        tool_calls=[
            {"name": "add_to_cart", "args": {"ref_or_asin": a}, "id": "c1"},
            {"name": "add_to_cart", "args": {"ref_or_asin": b, "qty": 3}, "id": "c2"},
        ],
    )
    _script(monkeypatch, both)
    saver, thread_id = InMemorySaver(), _thread_id()
    try:
        paused = _start("tambahkan keduanya ke keranjang", thread_id, saver)
        _script(monkeypatch, AIMessage(content="Dua-duanya masuk."))
        result = _resume(thread_id, saver, approve=True)
    finally:
        _delete_rows(thread_id)

    assert [i["asin"] for i in paused["pending_approval"]["items"]] == [a, b]
    # Parallel tool calls finish in no fixed order, so compare as sets.
    assert {(x["asin"], x["qty"]) for x in result["ui_actions"]} == {(a, 1), (b, 3)}
    assert len(result["ui_actions"]) == 2


# ── Abandoned requests ───────────────────────────────────────────────────────

def test_a_new_message_abandons_the_pending_request_and_leaves_valid_history(monkeypatch, products):
    from copilot_approval import ABANDONED_NOTE, NoPendingApproval

    (asin, *_), _ = products
    _script(monkeypatch, _cart_call(asin, "c1"))
    saver, thread_id = InMemorySaver(), _thread_id()
    cfg = {"configurable": {"thread_id": thread_id}}
    try:
        _start("tambahkan ke keranjang", thread_id, saver)
        _script(monkeypatch, AIMessage(content="Ini kemeja pilihan kak."))
        result = _start("eh, cariin kemeja aja", thread_id, saver)

        messages = saver.get_tuple(cfg).checkpoint["channel_values"]["messages"]
        with pytest.raises(NoPendingApproval):
            _resume(thread_id, saver, approve=True)
        rows = _rows(thread_id)
    finally:
        _delete_rows(thread_id)

    assert "pending_approval" not in result and result["ui_actions"] == []
    assert result["reply"] == "Ini kemeja pilihan kak."
    assert _dangling_tool_calls([*messages, AIMessage(content="x")]) == []
    assert ABANDONED_NOTE in [m.content for m in messages]
    assert [r[0] for r in rows] == ["answered"], "only the finished turn has a row"


def test_a_greeting_after_a_pause_does_not_break_the_next_real_turn(monkeypatch, products):
    (asin, *_), _ = products
    _script(monkeypatch, _cart_call(asin, "c1"))
    saver, thread_id = InMemorySaver(), _thread_id()
    try:
        _start("tambahkan ke keranjang", thread_id, saver)
        greeting = _start("halo kak", thread_id, saver)
        _script(monkeypatch, AIMessage(content="Siap kak."))
        later = _start("ada kemeja putih?", thread_id, saver)
    finally:
        _delete_rows(thread_id)

    assert greeting["guardrail"] == "greeting"
    assert later["reply"] == "Siap kak."  # history was repaired before the model was called


# ── Streaming ────────────────────────────────────────────────────────────────

def test_a_streamed_turn_ends_with_the_approval_request(monkeypatch, products):
    (asin, _title, price), _ = products
    _script(monkeypatch, _cart_call(asin, "c1", qty=2), streaming=True)
    saver, thread_id = InMemorySaver(), _thread_id()

    async def collect():
        return [
            e
            async for e in copilot_agent.stream_turn(
                "tambahkan ke keranjang", db_url=DB_URL, fake=True, thread_id=thread_id, checkpointer=saver
            )
        ]

    try:
        events = _run(collect())
        rows = _rows(thread_id)
    finally:
        _delete_rows(thread_id)

    types = [e["type"] for e in events]
    assert types[-2:] == ["approval_required", "done"]
    assert set(types[:-2]) <= {"token", "status"}
    approval, done = events[-2], events[-1]
    assert approval["thread_id"] == thread_id
    assert (approval["items"][0]["asin"], approval["items"][0]["priceIdr"]) == (asin, price)
    assert done["pending_approval"]["items"] == approval["items"]
    assert done["ui_actions"] == []
    assert rows == []


# ── Logging ──────────────────────────────────────────────────────────────────

def test_approval_log_lines_carry_ids_only(monkeypatch, products, caplog):
    (asin, title, _price), _ = products
    _script(monkeypatch, _cart_call(asin, "c1"))
    saver, thread_id = InMemorySaver(), _thread_id()
    caplog.set_level(logging.INFO, logger="copilot_agent")
    try:
        _start("tambahkan ke keranjang", thread_id, saver)
        _script(monkeypatch, AIMessage(content="Beres."))
        _resume(thread_id, saver, approve=True)
    finally:
        _delete_rows(thread_id)

    requested = [r for r in caplog.records if r.getMessage() == "copilot.approval.requested"]
    answered = [r for r in caplog.records if r.getMessage() == "copilot.approval.answered"]
    assert len(requested) == len(answered) == 1
    assert requested[0].thread_id == thread_id and requested[0].asins == [asin]
    assert answered[0].approved is True
    rendered = " ".join(str(r.__dict__) for r in requested + answered)
    assert title not in rendered and "tambahkan" not in rendered


# ── HTTP routes ──────────────────────────────────────────────────────────────

@pytest.fixture
def real_mode(monkeypatch):
    """Non-fake mode with the real Postgres checkpointer and stand-in models."""
    import copilot_memory

    monkeypatch.delenv("COPILOT_FAKE_LLM", raising=False)
    monkeypatch.setattr(copilot_llm, "llm_available", lambda: True)
    monkeypatch.setattr(copilot_agent, "is_llm_available", lambda: True)
    monkeypatch.setattr(
        copilot_llm, "build_chat_model", lambda temperature=0.0, fake=None: FakeListChatModel(responses=["ON_TOPIC"] * 4)
    )
    if copilot_memory.get_checkpointer() is None:
        pytest.skip("checkpointer unavailable")
    return copilot_memory


def test_http_flow_pause_then_approve(real_mode, monkeypatch, products):
    (asin, _title, price), _ = products
    _script(monkeypatch, _cart_call(asin, "c1", qty=2))
    thread_id = _thread_id()
    client = TestClient(app)
    chat = {"message": "tambahkan ke keranjang", "thread_id": thread_id, "session_id": "s-appr-http-1"}
    try:
        paused = client.post("/copilot/chat", json=chat)
        assert paused.status_code == 200
        body = paused.json()
        assert body["pending_approval"]["items"][0]["asin"] == asin
        assert body["ui_actions"] == []

        _script(monkeypatch, AIMessage(content="Sudah masuk keranjang kak."))
        approved = client.post("/copilot/chat/resume", json={"thread_id": thread_id, "approve": True})
        assert approved.status_code == 200
        done = approved.json()
        assert done["thread_id"] == thread_id
        assert [(a["asin"], a["qty"], a["priceIdr"]) for a in done["ui_actions"]] == [(asin, 2, price)]

        again = client.post("/copilot/chat/resume", json={"thread_id": thread_id, "approve": True})
        assert again.status_code == 409
        rows = _rows(thread_id)
    finally:
        real_mode.delete_thread(thread_id, db_url=DB_URL)
        _delete_rows(thread_id)

    assert [r[0] for r in rows] == ["cart"]


def test_http_resume_rejects_anything_but_a_yes_or_no(real_mode):
    client = TestClient(app)
    base = {"thread_id": "t-appr-http-2", "approve": True}
    assert client.post("/copilot/chat/resume", json={**base, "priceIdr": 1}).status_code == 422
    assert client.post("/copilot/chat/resume", json={**base, "asin": "B0X"}).status_code == 422
    assert client.post("/copilot/chat/resume", json={"thread_id": "t-appr-http-2"}).status_code == 422
    assert client.post("/copilot/chat/resume", json={**base, "thread_id": "bad id!"}).status_code == 422


def test_http_resume_of_an_unknown_thread_is_404(real_mode):
    response = TestClient(app).post(
        "/copilot/chat/resume", json={"thread_id": f"t-appr-none-{uuid.uuid4().hex[:6]}", "approve": True}
    )
    assert response.status_code == 404


def test_http_resume_is_rate_limited(real_mode, monkeypatch):
    import routers.copilot as route_module

    monkeypatch.setattr(route_module, "check_copilot_rate_limit", lambda ip: (False, 7, "minute"))
    response = TestClient(app).post("/copilot/chat/resume", json={"thread_id": "t-appr-http-3", "approve": True})
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "7"


def test_http_resume_without_a_checkpointer_is_404(monkeypatch):
    import copilot_memory

    monkeypatch.delenv("COPILOT_FAKE_LLM", raising=False)
    monkeypatch.setattr(copilot_memory, "get_checkpointer", lambda: None)
    response = TestClient(app).post("/copilot/chat/resume", json={"thread_id": "t-appr-http-4", "approve": True})
    assert response.status_code == 404
