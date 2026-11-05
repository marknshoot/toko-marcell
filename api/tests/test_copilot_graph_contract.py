"""Contract tests for the LangChain/LangGraph behaviour the copilot graph relies on.

These tests use only the framework plus the fake tool-calling model, so a library upgrade that
changes any of these behaviours fails *here*, with a clear name, rather than somewhere inside the
copilot. Each test documents a fact the restructure depends on and, where the fact could have
gone the other way, the fallback that would have been used.

No database and no network.
"""

import asyncio
import sys
import threading
from pathlib import Path
from typing import Any

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

from typing import NotRequired

import pytest
from copilot_llm import fake_tool_calling_model
from copilot_tools import ToolContext
from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    HumanInTheLoopMiddleware,
    ModelCallLimitMiddleware,
)
from langchain.agents.middleware.types import hook_config
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from logging_context import ctx_request_id

CFG = {"configurable": {"thread_id": "contract"}}


# ── Shared fixtures ──────────────────────────────────────────────────────────

CALLS: list[str] = []


@pytest.fixture(autouse=True)
def _reset_calls():
    CALLS.clear()
    yield
    CALLS.clear()


@tool
def ping() -> str:
    """Return pong."""
    CALLS.append("ping")
    return "pong"


def _tool_call(name: str, call_id: str, **args: Any) -> dict:
    return {"name": name, "args": args, "id": call_id}


def _always_calls_ping(n: int = 8) -> list[AIMessage]:
    return [AIMessage(content="", tool_calls=[_tool_call("ping", f"c{i}")]) for i in range(n)]


def _last_text(result: dict) -> str:
    return result["messages"][-1].content


class GuardNode(AgentMiddleware):
    """A guardrail-shaped first node: canned reply and early exit on a trigger word."""

    @hook_config(can_jump_to=["end"])
    def before_agent(self, state, runtime) -> dict[str, Any] | None:
        CALLS.append("guard.sync")
        if "halo" in state["messages"][-1].content.lower():
            return {"messages": [AIMessage(content="CANNED")], "jump_to": "end"}
        return None

    @hook_config(can_jump_to=["end"])
    async def abefore_agent(self, state, runtime) -> dict[str, Any] | None:
        CALLS.append("guard.async")
        if "halo" in state["messages"][-1].content.lower():
            return {"messages": [AIMessage(content="CANNED")], "jump_to": "end"}
        return None


class Finalize(AgentMiddleware):
    """A finalize-shaped last node."""

    def after_agent(self, state, runtime) -> None:
        CALLS.append("finalize.sync")
        return None

    async def aafter_agent(self, state, runtime) -> None:
        CALLS.append("finalize.async")
        return None


# ── (a) after_agent after an early exit ──────────────────────────────────────

def test_after_agent_runs_after_a_normal_turn():
    agent = create_agent(
        fake_tool_calling_model([AIMessage(content="ok")]), [ping], middleware=[GuardNode(), Finalize()]
    )
    agent.invoke({"messages": [HumanMessage(content="cari celana")]})
    assert "finalize.sync" in CALLS


def test_after_agent_runs_after_the_guardrail_jumps_to_end():
    agent = create_agent(
        fake_tool_calling_model([AIMessage(content="never used")]),
        [ping],
        middleware=[GuardNode(), Finalize()],
    )
    result = agent.invoke({"messages": [HumanMessage(content="halo kak")]})
    assert _last_text(result) == "CANNED"
    # If this fails, persist state from the guardrail branch directly instead of after_agent.
    assert "finalize.sync" in CALLS


def test_after_agent_runs_after_the_loop_cap_exits():
    agent = create_agent(
        fake_tool_calling_model(_always_calls_ping()),
        [ping],
        middleware=[Finalize(), ModelCallLimitMiddleware(run_limit=3, exit_behavior="end")],
    )
    agent.invoke({"messages": [HumanMessage(content="cari")]})
    assert CALLS.count("ping") == 3
    assert "finalize.sync" in CALLS


# ── (b) sync and async hooks ─────────────────────────────────────────────────

def test_sync_invoke_uses_the_sync_hooks():
    agent = create_agent(
        fake_tool_calling_model([AIMessage(content="ok")]), [], middleware=[GuardNode(), Finalize()]
    )
    agent.invoke({"messages": [HumanMessage(content="cari")]})
    assert "guard.sync" in CALLS and "guard.async" not in CALLS
    assert "finalize.sync" in CALLS


def test_async_invoke_uses_the_async_hooks():
    agent = create_agent(
        fake_tool_calling_model([AIMessage(content="ok")]), [], middleware=[GuardNode(), Finalize()]
    )
    asyncio.run(agent.ainvoke({"messages": [HumanMessage(content="cari")]}))
    assert "guard.async" in CALLS and "guard.sync" not in CALLS
    assert "finalize.async" in CALLS


def test_async_invoke_works_with_sync_only_hooks():
    """run_turn calls ainvoke when there is no checkpointer, so a sync-only hook must still run."""

    class SyncOnly(AgentMiddleware):
        def before_agent(self, state, runtime):
            CALLS.append("sync-only")
            return None

    agent = create_agent(fake_tool_calling_model([AIMessage(content="ok")]), [], middleware=[SyncOnly()])
    asyncio.run(agent.ainvoke({"messages": [HumanMessage(content="x")]}))
    assert "sync-only" in CALLS


# ── (c) the loop-cap message ─────────────────────────────────────────────────

def test_default_loop_cap_reply_is_the_english_technical_string():
    agent = create_agent(
        fake_tool_calling_model(_always_calls_ping()),
        [ping],
        middleware=[ModelCallLimitMiddleware(run_limit=3, exit_behavior="end")],
    )
    result = agent.invoke({"messages": [HumanMessage(content="cari")]})
    assert _last_text(result) == "Model call limits exceeded: run limit (3/3)"


def test_loop_cap_message_can_be_replaced_by_subclassing_before_model():
    """The text is built by a module-level function, so the constructor cannot change it."""

    class FriendlyCap(ModelCallLimitMiddleware):
        @hook_config(can_jump_to=["end"])
        def before_model(self, state, runtime):
            update = super().before_model(state, runtime)
            if update and update.get("jump_to") == "end":
                update["messages"] = [
                    AIMessage(content="Maaf kak, coba tanya dengan cara lain ya.", additional_kwargs={"loop_cap": True})
                ]
            return update

        @hook_config(can_jump_to=["end"])
        async def abefore_model(self, state, runtime):
            return self.before_model(state, runtime)

    agent = create_agent(
        fake_tool_calling_model(_always_calls_ping()),
        [ping],
        middleware=[FriendlyCap(run_limit=3, exit_behavior="end")],
    )
    result = agent.invoke({"messages": [HumanMessage(content="cari")]})
    last = result["messages"][-1]
    assert last.content == "Maaf kak, coba tanya dengan cara lain ya."
    assert last.additional_kwargs.get("loop_cap") is True
    assert CALLS.count("ping") == 3


# ── (d) parallel tool calls keep ref numbering safe ──────────────────────────

def test_parallel_tool_calls_get_distinct_sequential_refs():
    ctx = ToolContext(db_url="postgresql://unused")

    @tool
    def register(asin: str) -> dict:
        """Register a product in the per-request registry."""
        return {"asin": asin, "ref": ctx.register({"asin": asin, "title": asin, "priceIdr": 1})}

    two_at_once = AIMessage(
        content="",
        tool_calls=[_tool_call("register", "a", asin="B1"), _tool_call("register", "b", asin="B2")],
    )
    agent = create_agent(fake_tool_calling_model([two_at_once, AIMessage(content="done")]), [register])
    agent.invoke({"messages": [HumanMessage(content="cari")]})

    assert sorted(ctx.registry) == [1, 2]
    assert {p["asin"] for p in ctx.registry.values()} == {"B1", "B2"}


# ── (e) old checkpoints load under a new state schema ────────────────────────

class NewState(AgentState):
    shown_products: NotRequired[list[dict]]
    guardrail: NotRequired[str | None]


class NewStateMW(AgentMiddleware):
    state_schema = NewState


def test_thread_saved_without_new_fields_loads_under_the_new_schema():
    saver = InMemorySaver()
    old = create_agent(fake_tool_calling_model([AIMessage(content="first")]), [], checkpointer=saver)
    old.invoke({"messages": [HumanMessage(content="satu")]}, CFG)

    new = create_agent(
        fake_tool_calling_model([AIMessage(content="second")]), [], middleware=[NewStateMW()], checkpointer=saver
    )
    result = new.invoke({"messages": [HumanMessage(content="dua")]}, CFG)

    texts = [m.content for m in result["messages"]]
    assert texts == ["satu", "first", "dua", "second"]
    assert not result.get("shown_products")


# ── (f) interrupt and resume (human approval) ────────────────────────────────

def _approval_agent(saver):
    return create_agent(
        fake_tool_calling_model(
            [AIMessage(content="", tool_calls=[_tool_call("ping", "p1")]), AIMessage(content="selesai")]
        ),
        [ping],
        middleware=[
            HumanInTheLoopMiddleware(interrupt_on={"ping": {"allowed_decisions": ["approve", "reject"]}})
        ],
        checkpointer=saver,
    )


def test_tool_call_pauses_for_approval_and_nothing_runs_yet():
    result = _approval_agent(InMemorySaver()).invoke({"messages": [HumanMessage(content="jalankan")]}, CFG)
    interrupts = result.get("__interrupt__")
    assert interrupts, "expected the graph to pause"
    request = interrupts[0].value
    assert request["action_requests"][0]["name"] == "ping"
    assert request["review_configs"][0]["allowed_decisions"] == ["approve", "reject"]
    assert CALLS.count("ping") == 0


def test_approve_runs_the_tool_and_finishes():
    saver = InMemorySaver()
    agent = _approval_agent(saver)
    agent.invoke({"messages": [HumanMessage(content="jalankan")]}, CFG)
    result = agent.invoke(Command(resume={"decisions": [{"type": "approve"}]}), CFG)
    assert CALLS.count("ping") == 1
    assert _last_text(result) == "selesai"
    assert not result.get("__interrupt__")


def test_reject_skips_the_tool_and_tells_the_model():
    saver = InMemorySaver()
    agent = _approval_agent(saver)
    agent.invoke({"messages": [HumanMessage(content="jalankan")]}, CFG)
    result = agent.invoke(
        Command(resume={"decisions": [{"type": "reject", "message": "shopper declined"}]}), CFG
    )
    assert CALLS.count("ping") == 0
    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert tool_messages and "shopper declined" in tool_messages[-1].content


def test_a_paused_thread_survives_a_new_graph_instance():
    """The approval request arrives in one HTTP request and the decision in another."""
    saver = InMemorySaver()
    _approval_agent(saver).invoke({"messages": [HumanMessage(content="jalankan")]}, CFG)
    fresh = create_agent(
        fake_tool_calling_model([AIMessage(content="selesai")]),
        [ping],
        middleware=[
            HumanInTheLoopMiddleware(interrupt_on={"ping": {"allowed_decisions": ["approve", "reject"]}})
        ],
        checkpointer=saver,
    )
    # The decision is applied to the saved AI message, so the fresh model only writes the final reply.
    result = fresh.invoke(Command(resume={"decisions": [{"type": "approve"}]}), CFG)
    assert CALLS.count("ping") == 1
    assert _last_text(result) == "selesai"


# ── (g) stream_mode="updates" exposes the steps ──────────────────────────────

def test_updates_stream_names_middleware_model_and_tool_steps():
    agent = create_agent(
        fake_tool_calling_model(
            [AIMessage(content="", tool_calls=[_tool_call("ping", "p1")]), AIMessage(content="ok")]
        ),
        [ping],
        middleware=[GuardNode(), Finalize()],
    )
    steps: list[str] = []
    for update in agent.stream({"messages": [HumanMessage(content="cari")]}, stream_mode="updates"):
        steps.extend(update.keys())

    assert "model" in steps and "tools" in steps
    assert any("GuardNode" in s for s in steps)
    assert steps.index("model") < steps.index("tools")


def test_messages_stream_keeps_the_model_node_name():
    """stream_turn only forwards tokens from the node named "model"."""
    agent = create_agent(fake_tool_calling_model([AIMessage(content="satu dua tiga empat")]), [], middleware=[GuardNode()])
    nodes = {
        meta.get("langgraph_node")
        for _chunk, meta in agent.stream(
            {"messages": [HumanMessage(content="cari")]}, stream_mode="messages"
        )
    }
    assert "model" in nodes


# ── (h) request and trace ids reach graph nodes ──────────────────────────────

def _id_probe_agent():
    seen: list[str | None] = []

    @tool
    def probe() -> str:
        """Record the request id visible inside the graph."""
        seen.append(ctx_request_id.get())
        return "ok"

    agent = create_agent(
        fake_tool_calling_model([AIMessage(content="", tool_calls=[_tool_call("probe", "x")]), AIMessage(content="ok")]),
        [probe],
    )
    return agent, seen


def test_request_id_reaches_nodes_run_via_asyncio_to_thread():
    """run_turn runs the checkpointed graph this way."""
    agent, seen = _id_probe_agent()
    token = ctx_request_id.set("req_contract")

    async def go():
        await asyncio.to_thread(agent.invoke, {"messages": [HumanMessage(content="x")]})

    try:
        asyncio.run(go())
    finally:
        ctx_request_id.reset(token)
    assert seen == ["req_contract"]


def test_request_id_is_lost_in_a_plain_thread_and_kept_with_copy_context():
    """stream_turn started its worker in a plain Thread, so its logs lost the ids."""
    import contextvars

    results: dict[str, list[str | None]] = {}
    token = ctx_request_id.set("req_contract")
    try:
        for name, wrap in (("plain", False), ("copied", True)):
            agent, seen = _id_probe_agent()
            payload = {"messages": [HumanMessage(content="x")]}
            if wrap:
                ctx = contextvars.copy_context()
                thread = threading.Thread(target=lambda a=agent, p=payload, c=ctx: c.run(a.invoke, p))
            else:
                thread = threading.Thread(target=lambda a=agent, p=payload: a.invoke(p))
            thread.start()
            thread.join()
            results[name] = list(seen)
    finally:
        ctx_request_id.reset(token)

    assert results["plain"] == [None]
    assert results["copied"] == ["req_contract"]

