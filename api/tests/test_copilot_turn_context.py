"""The page/browsing context is read once per turn; the registry part is rebuilt per model call."""

import asyncio
import contextlib
import os
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

import copilot_agent
import copilot_context
import psycopg
import pytest
from copilot_agent import ADMIN_SYSTEM_PROMPT, build_context_block
from copilot_tools import ToolContext
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

DB_URL = os.environ.get("DATABASE_URL", "postgresql://toko:toko@localhost:5432/toko")
SESSION_ID = "s-context-0001"


@pytest.fixture
def page_asin():
    try:
        with psycopg.connect(DB_URL) as conn:
            row = conn.execute("SELECT asin FROM products ORDER BY id LIMIT 1").fetchone()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"database unavailable: {exc}")
    if row is None:
        pytest.skip("catalog is empty")
    return row[0]


class RecordingModel(GenericFakeChatModel):
    """Fake model that remembers the system prompt of every call it receives."""

    def bind_tools(self, tools, **kwargs):  # noqa: ARG002
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        PROMPTS.append(next(m.content for m in messages if isinstance(m, SystemMessage)))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


PROMPTS: list[str] = []


@pytest.fixture(autouse=True)
def _reset_prompts():
    PROMPTS.clear()
    yield
    PROMPTS.clear()


def _tool_call(call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": "find", "args": {}, "id": call_id}])


def _setup(monkeypatch, ctx, replies):
    """build_agent with a tool that registers one product per call and a recording model."""
    counter = {"n": 0}

    @tool
    def find() -> dict:
        """Find a product."""
        counter["n"] += 1
        product = {"asin": f"B{counter['n']}", "title": f"Chino {counter['n']}", "priceIdr": 100_000}
        return {"ref": ctx.register(product), **product}

    monkeypatch.setattr(copilot_agent, "build_tools", lambda c, categories=None: [find])
    # build_agent reads the category list through _connect on a cold cache; that is not the
    # context read being counted here.
    monkeypatch.setattr(copilot_agent, "_catalog_categories", lambda db_url: [])
    monkeypatch.setattr(
        copilot_agent, "build_chat_model", lambda temperature=0.2, fake=None: RecordingModel(messages=iter(replies))
    )


def _count_connects(monkeypatch) -> list[str]:
    """Count database connections opened through copilot_context._connect."""
    opened: list[str] = []
    real = copilot_context._connect

    @contextlib.contextmanager
    def counting(db_url):
        opened.append(db_url)
        with real(db_url) as conn:
            yield conn

    monkeypatch.setattr(copilot_context, "_connect", counting)
    return opened


def _expected_prompt(block: str) -> str:
    if not block:
        return ADMIN_SYSTEM_PROMPT
    return f"{ADMIN_SYSTEM_PROMPT}\n\n<store_context>\n{block}\n</store_context>"


def test_three_model_calls_read_the_database_context_once(monkeypatch, page_asin):
    ctx = ToolContext(db_url=DB_URL, page_asin=page_asin)
    _setup(monkeypatch, ctx, [_tool_call("a"), _tool_call("b"), AIMessage(content="Selesai kak.")])
    opened = _count_connects(monkeypatch)
    agent = copilot_agent.build_agent(ctx, SESSION_ID, fake=True)

    agent.invoke({"messages": [HumanMessage(content="ada celana?")]})

    assert len(PROMPTS) == 3, "expected three model calls"
    assert len(opened) == 1  # it was 3: one per model call


def test_async_path_also_reads_the_context_once(monkeypatch, page_asin):
    ctx = ToolContext(db_url=DB_URL, page_asin=page_asin)
    _setup(monkeypatch, ctx, [_tool_call("a"), _tool_call("b"), AIMessage(content="Selesai kak.")])
    opened = _count_connects(monkeypatch)
    agent = copilot_agent.build_agent(ctx, SESSION_ID, fake=True)

    asyncio.run(agent.ainvoke({"messages": [HumanMessage(content="ada celana?")]}))

    assert len(PROMPTS) == 3
    assert len(opened) == 1


def test_prompts_match_the_old_per_call_composition(monkeypatch, page_asin):
    ctx = ToolContext(db_url=DB_URL, page_asin=page_asin)
    _setup(monkeypatch, ctx, [_tool_call("a"), AIMessage(content="Ini kak.")])
    before_any_product = build_context_block(ToolContext(db_url=DB_URL, page_asin=page_asin), SESSION_ID)
    agent = copilot_agent.build_agent(ctx, SESSION_ID, fake=True)

    agent.invoke({"messages": [HumanMessage(content="ada celana?")]})

    after_the_tool = build_context_block(ctx, SESSION_ID)  # the registry now holds the new product
    assert "[Produk dalam percakapan ini]" in after_the_tool
    assert PROMPTS[0] == _expected_prompt(before_any_product)
    assert PROMPTS[1] == _expected_prompt(after_the_tool)  # registry part is rebuilt per call


def test_a_turn_with_no_context_leaves_the_prompt_untouched(monkeypatch):
    ctx = ToolContext(db_url=DB_URL)  # no page product, no session
    _setup(monkeypatch, ctx, [AIMessage(content="Halo kak.")])
    opened = _count_connects(monkeypatch)
    agent = copilot_agent.build_agent(ctx, None, fake=True)

    agent.invoke({"messages": [HumanMessage(content="ada celana?")]})

    assert PROMPTS == [ADMIN_SYSTEM_PROMPT]
    assert opened == []


def test_a_greeting_reads_no_context(monkeypatch, page_asin):
    ctx = ToolContext(db_url=DB_URL, page_asin=page_asin)
    _setup(monkeypatch, ctx, [AIMessage(content="never used")])
    opened = _count_connects(monkeypatch)
    agent = copilot_agent.build_agent(ctx, SESSION_ID, fake=True)

    result = agent.invoke({"messages": [HumanMessage(content="halo kak")]})

    assert result["guardrail"] == "greeting"
    assert opened == [] and PROMPTS == []


def test_turn_context_is_scratch_and_is_not_saved_with_the_thread(monkeypatch, page_asin):
    ctx = ToolContext(db_url=DB_URL, page_asin=page_asin)
    _setup(monkeypatch, ctx, [AIMessage(content="Ini kak.")])
    saver = InMemorySaver()
    cfg = {"configurable": {"thread_id": "ctx-thread"}}
    agent = copilot_agent.build_agent(ctx, SESSION_ID, fake=True, checkpointer=saver, thread_id="ctx-thread")

    result = agent.invoke({"messages": [HumanMessage(content="ada celana?")]}, cfg)

    assert "turn_context" not in result
    assert "turn_context" not in saver.get_tuple(cfg).checkpoint["channel_values"]


def test_each_turn_reads_fresh_context(monkeypatch, page_asin):
    """A later turn must not reuse the previous turn's page context."""
    first_ctx = ToolContext(db_url=DB_URL, page_asin=page_asin)
    _setup(monkeypatch, first_ctx, [AIMessage(content="Satu.")])
    saver = InMemorySaver()
    cfg = {"configurable": {"thread_id": "ctx-thread-2"}}
    copilot_agent.build_agent(first_ctx, SESSION_ID, fake=True, checkpointer=saver, thread_id="ctx-thread-2").invoke(
        {"messages": [HumanMessage(content="ada celana?")]}, cfg
    )

    second_ctx = ToolContext(db_url=DB_URL)  # the shopper moved to a page with no product
    _setup(monkeypatch, second_ctx, [AIMessage(content="Dua.")])
    copilot_agent.build_agent(second_ctx, None, fake=True, checkpointer=saver, thread_id="ctx-thread-2").invoke(
        {"messages": [HumanMessage(content="oke, yang lain?")]}, cfg
    )

    assert "[Produk yang sedang dilihat]" in PROMPTS[0]
    assert "[Produk yang sedang dilihat]" not in PROMPTS[1]
