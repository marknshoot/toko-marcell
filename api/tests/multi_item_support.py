"""Shared setup for the multi-item cart approval tests (not a test module)."""

import functools
import re
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

import copilot_agent
import psycopg
import pytest
from copilot_tools import ToolContext
from langchain_core.messages import AIMessage
from test_copilot_cart_approval import DB_URL


def _products(where: str, count: int) -> list[tuple[str, str, int]]:
    from migrate import run_migrations

    try:
        run_migrations(DB_URL)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"database unavailable: {exc}")
    with psycopg.connect(DB_URL) as conn:
        rows = conn.execute(
            f"SELECT asin, title, price_idr FROM products WHERE price_idr IS NOT NULL AND {where} ORDER BY id LIMIT %s",
            (count,),
        ).fetchall()
    if len(rows) < count:
        pytest.skip(f"catalog has fewer than {count} products matching {where}")
    return rows


@functools.cache
def plain_products():
    """Five products whose ASIN digits read as 0 (``B00`` + letter), so they never look like a list number."""
    rows = _products("asin ~ '^B00[A-Z]'", 5)
    for asin, *_ in rows:
        assert int(re.search(r"\d+", asin).group()) == 0, asin
    return rows


@functools.cache
def colliding_products():
    """Four products whose ASIN digits (``B001A...``, ``B0001G...``) all read as list number 1."""
    rows = _products("asin ~ '^B0*1[A-Z]'", 4)
    for asin, *_ in rows:
        assert int(re.search(r"\d+", asin).group()) == 1, asin
    assert len({asin for asin, *_ in rows}) == 4
    return rows


def _cart_calls(tokens: list[str], prefix: str, **args) -> AIMessage:
    return AIMessage(
        content="Oke kak.",
        tool_calls=[
            {"name": "add_to_cart", "args": {"ref_or_asin": token, **args}, "id": f"{prefix}{i}"}
            for i, token in enumerate(tokens)
        ],
    )


def _seed_registry(saver, thread_id: str, products) -> None:
    """Put products in the thread's saved registry as refs 1, 2, 3, ... (what a recommendation leaves behind)."""
    agent = copilot_agent.build_agent(
        ToolContext(db_url=DB_URL), None, fake=True, checkpointer=saver, thread_id=thread_id
    )
    shown = [
        {"ref": ref, "asin": asin, "title": title, "priceIdr": price}
        for ref, (asin, title, price) in enumerate(products, start=1)
    ]
    agent.update_state({"configurable": {"thread_id": thread_id}}, {"shown_products": shown})
