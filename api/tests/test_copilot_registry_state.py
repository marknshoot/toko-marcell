"""The shown-products registry lives in the graph state and survives between turns."""

import json
import os
import sys
import uuid
from pathlib import Path

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

import copilot_agent
import copilot_middleware
from copilot_llm import fake_tool_calling_model
from copilot_tools import MAX_REGISTRY, ToolContext
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

DB_URL = os.environ.get("DATABASE_URL", "postgresql://toko:toko@localhost:5432/toko")


def _product(n: int) -> dict:
    return {"asin": f"B{n}", "title": f"Chino {n}", "priceIdr": 100_000 + n}


# ── ToolContext.seed / snapshot ──────────────────────────────────────────────

def test_seed_restores_refs_and_continues_numbering():
    ctx = ToolContext(db_url=DB_URL)
    ctx.seed([{"ref": 1, **_product(1)}, {"ref": 3, **_product(3)}])

    assert sorted(ctx.registry) == [1, 3]
    assert ctx.registry[3]["asin"] == "B3"
    assert "ref" not in ctx.registry[3]
    assert ctx.register(_product(9)) == 4  # continues after the highest restored ref


def test_seeded_products_are_not_reported_as_this_turns_cards():
    ctx = ToolContext(db_url=DB_URL)
    ctx.seed([{"ref": 1, **_product(1)}])
    assert ctx.turn_products() == []
    ctx.register(_product(2))
    assert [p["asin"] for p in ctx.turn_products()] == ["B2"]


def test_snapshot_round_trips_through_seed():
    first = ToolContext(db_url=DB_URL)
    for n in (1, 2, 3):
        first.register(_product(n))
    second = ToolContext(db_url=DB_URL)
    second.seed(json.loads(json.dumps(first.snapshot())))  # as saved in a checkpoint

    assert second.registry == first.registry
    assert second.register(_product(4)) == 4


def test_seed_keeps_the_registry_bounded_and_ignores_junk():
    ctx = ToolContext(db_url=DB_URL)
    ctx.seed([{"ref": n, **_product(n)} for n in range(1, MAX_REGISTRY + 4)] + [{"asin": "no-ref"}, {"ref": True}])
    assert len(ctx.registry) == MAX_REGISTRY
    assert min(ctx.registry) == 4  # the lowest refs are dropped first


# ── The registry is saved with the thread ────────────────────────────────────

def _agent_with_registering_tool(monkeypatch, ctx, saver, thread_id, replies, products_per_call=(1, 2)):
    """build_agent with a tool that registers products in ``ctx``, as search_catalog does."""

    @tool
    def find_products() -> list[dict]:
        """Find some products."""
        out = []
        for n in products_per_call:
            out.append({"ref": ctx.register(_product(n)), **_product(n)})
        return out

    monkeypatch.setattr(copilot_agent, "build_tools", lambda c, categories=None: [find_products])
    scripts = iter(replies)
    monkeypatch.setattr(
        copilot_agent, "build_chat_model", lambda temperature=0.2, fake=None: fake_tool_calling_model(next(scripts))
    )
    return copilot_agent.build_agent(ctx, None, fake=True, checkpointer=saver, thread_id=thread_id)


FIND = AIMessage(content="", tool_calls=[{"name": "find_products", "args": {}, "id": "f1"}])
CFG = {"configurable": {"thread_id": "reg-thread"}}


def test_registry_is_saved_in_the_state_after_a_turn(monkeypatch):
    saver = InMemorySaver()
    ctx = ToolContext(db_url=DB_URL)
    agent = _agent_with_registering_tool(monkeypatch, ctx, saver, "reg-thread", [[FIND, AIMessage(content="Ini kak.")]])

    result = agent.invoke({"messages": [HumanMessage(content="ada celana?")]}, CFG)

    assert [(p["ref"], p["asin"]) for p in result["shown_products"]] == [(1, "B1"), (2, "B2")]
    saved = saver.get_tuple(CFG).checkpoint["channel_values"]["shown_products"]
    assert [p["asin"] for p in saved] == ["B1", "B2"]


def test_next_turn_restores_the_registry_without_replaying_the_thread(monkeypatch):
    saver = InMemorySaver()
    replays: list[str | None] = []
    real_replay = copilot_middleware._seed_registry_from_checkpoint
    monkeypatch.setattr(
        copilot_middleware,
        "_seed_registry_from_checkpoint",
        lambda ctx, cp, tid: (replays.append(tid), real_replay(ctx, cp, tid))[1],
    )

    first_ctx = ToolContext(db_url=DB_URL)
    first = _agent_with_registering_tool(monkeypatch, first_ctx, saver, "reg-thread", [[FIND, AIMessage(content="Ini kak.")]])
    first.invoke({"messages": [HumanMessage(content="ada celana?")]}, CFG)

    second_ctx = ToolContext(db_url=DB_URL)
    second = _agent_with_registering_tool(monkeypatch, second_ctx, saver, "reg-thread", [[AIMessage(content="Siap.")]])
    second.invoke({"messages": [HumanMessage(content="yang kedua berapa?")]}, CFG)

    assert sorted(second_ctx.registry) == [1, 2]
    assert second_ctx.registry[2]["asin"] == "B2"
    assert replays == []  # state had the registry; no checkpoint replay, not even for a new thread


def test_a_greeting_between_turns_does_not_wipe_the_registry(monkeypatch):
    saver = InMemorySaver()
    ctx = ToolContext(db_url=DB_URL)
    agent = _agent_with_registering_tool(monkeypatch, ctx, saver, "reg-thread", [[FIND, AIMessage(content="Ini kak.")]])
    agent.invoke({"messages": [HumanMessage(content="ada celana?")]}, CFG)

    greeting_ctx = ToolContext(db_url=DB_URL)
    greeter = _agent_with_registering_tool(monkeypatch, greeting_ctx, saver, "reg-thread", [[AIMessage(content="unused")]])
    greeted = greeter.invoke({"messages": [HumanMessage(content="halo kak")]}, CFG)
    assert greeted["guardrail"] == "greeting"

    later_ctx = ToolContext(db_url=DB_URL)
    later = _agent_with_registering_tool(monkeypatch, later_ctx, saver, "reg-thread", [[AIMessage(content="Siap.")]])
    later.invoke({"messages": [HumanMessage(content="yang pertama bahannya apa?")]}, CFG)

    assert sorted(later_ctx.registry) == [1, 2]


def test_new_products_continue_the_numbering_on_a_later_turn(monkeypatch):
    saver = InMemorySaver()
    first_ctx = ToolContext(db_url=DB_URL)
    first = _agent_with_registering_tool(monkeypatch, first_ctx, saver, "reg-thread", [[FIND, AIMessage(content="Ini kak.")]])
    first.invoke({"messages": [HumanMessage(content="ada celana?")]}, CFG)

    second_ctx = ToolContext(db_url=DB_URL)
    # A real provider issues a fresh tool-call id every time; reusing one makes the graph skip the call.
    find_again = AIMessage(content="", tool_calls=[{"name": "find_products", "args": {}, "id": "f2"}])
    second = _agent_with_registering_tool(
        monkeypatch, second_ctx, saver, "reg-thread", [[find_again, AIMessage(content="Ada lagi.")]], products_per_call=(7,)
    )
    result = second.invoke({"messages": [HumanMessage(content="ada yang lain?")]}, CFG)

    assert [(p["ref"], p["asin"]) for p in result["shown_products"]] == [(1, "B1"), (2, "B2"), (3, "B7")]


# ── Threads saved before the state field existed ─────────────────────────────

def test_a_legacy_thread_is_replayed_once_and_then_saved(monkeypatch):
    saver = InMemorySaver()

    @tool
    def find_products() -> list[dict]:
        """Find some products."""
        return [_product(1), _product(2)]

    # Written by the old graph: no shown_products field, products only inside the tool message.
    legacy = create_agent(
        fake_tool_calling_model([FIND, AIMessage(content="Ini kak.")]), [find_products], checkpointer=saver
    )
    legacy.invoke({"messages": [HumanMessage(content="ada celana?")]}, CFG)
    assert "shown_products" not in saver.get_tuple(CFG).checkpoint["channel_values"]

    ctx = ToolContext(db_url=DB_URL)
    agent = _agent_with_registering_tool(monkeypatch, ctx, saver, "reg-thread", [[AIMessage(content="Siap.")]])
    result = agent.invoke({"messages": [HumanMessage(content="yang kedua berapa?")]}, CFG)

    assert sorted(ctx.registry) == [1, 2]
    assert ctx.registry[2]["asin"] == "B2"
    assert [p["asin"] for p in result["shown_products"]] == ["B1", "B2"]  # saved for next time


def test_a_brand_new_thread_does_not_read_the_checkpoint(monkeypatch):
    reads: list[str | None] = []
    monkeypatch.setattr(copilot_middleware, "_seed_registry_from_checkpoint", lambda ctx, cp, tid: reads.append(tid))
    ctx = ToolContext(db_url=DB_URL)
    agent = _agent_with_registering_tool(
        monkeypatch, ctx, InMemorySaver(), f"new-{uuid.uuid4().hex[:6]}", [[AIMessage(content="Halo.")]]
    )
    agent.invoke({"messages": [HumanMessage(content="ada celana?")]}, {"configurable": {"thread_id": "fresh"}})
    assert reads == []
