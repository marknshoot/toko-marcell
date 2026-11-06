"""Two cart calls for the same product show as one row in the confirm card.

Live-site failure: the card listed the same sunhat twice. The row count is a display matter; the
number of decisions and the quantity that reaches the cart are unchanged."""

import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

import uuid

import copilot_agent
from copilot_approval import approval_decisions, pending_items
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from multi_item_support import plain_products
from test_copilot_cart_approval import (
    DB_URL,
    StreamingScripted,
    _delete_rows,
    _resume,
    _run,
    _script,
    _start,
    _thread_id,
)

# ── Identical rows are merged in the card ────────────────────────────────────

def _request(*items: dict) -> dict:
    """A pending approval request as stored in the thread: one action per cart call."""
    import json

    return {
        "action_requests": [
            {"name": "add_to_cart", "args": {"ref_or_asin": "x"}, "description": json.dumps(item)} for item in items
        ]
    }


def _item(asin: str, qty: int = 1, size: str | None = None) -> dict:
    return {"asin": asin, "title": f"T {asin}", "priceIdr": 1000, "imageUrl": None, "qty": qty, "size": size}


class TestPendingItemsMerge:
    def test_the_same_product_twice_becomes_one_row_with_the_summed_quantity(self):
        rows = pending_items(_request(_item("A", 1), _item("B", 1), _item("A", 2)))
        assert [(r["asin"], r["qty"]) for r in rows] == [("A", 3), ("B", 1)], "first-seen order is kept"

    def test_different_sizes_stay_separate(self):
        rows = pending_items(_request(_item("A", 1, "M"), _item("A", 1, "L"), _item("A", 2, "M")))
        assert [(r["asin"], r["size"], r["qty"]) for r in rows] == [("A", "M", 3), ("A", "L", 1)]

    def test_the_summed_quantity_is_capped_at_99(self):
        (row,) = pending_items(_request(_item("A", 60), _item("A", 60)))
        assert row["qty"] == 99

    def test_merging_does_not_change_the_number_of_decisions(self):
        request = _request(_item("A", 1), _item("A", 2), _item("B", 1))
        decisions = approval_decisions(request, approve=True)
        assert len(decisions) == 3 == len(request["action_requests"])
        assert [d["edited_action"]["args"]["qty"] for d in decisions] == [1, 2, 1]


def test_two_calls_for_one_product_show_one_row_and_add_that_quantity(monkeypatch):
    catalog = plain_products()
    (a, *_), (b, *_) = catalog[:2]
    message = AIMessage(
        content="Oke kak.",
        tool_calls=[
            {"name": "add_to_cart", "args": {"ref_or_asin": a}, "id": "m0"},
            {"name": "add_to_cart", "args": {"ref_or_asin": b}, "id": "m1"},
            {"name": "add_to_cart", "args": {"ref_or_asin": a, "qty": 2}, "id": "m2"},
        ],
    )
    saver, thread_id = InMemorySaver(), _thread_id()
    try:
        _script(monkeypatch, message)
        card = _start("tambahkan semuanya ke keranjang", thread_id, saver)
        _script(monkeypatch, AIMessage(content="Sudah kak."))
        done = _resume(thread_id, saver, approve=True)
    finally:
        _delete_rows(thread_id)

    assert [(i["asin"], i["qty"]) for i in card["pending_approval"]["items"]] == [(a, 3), (b, 1)]
    added: dict[str, int] = {}
    for action in done["ui_actions"]:
        added[action["asin"]] = added.get(action["asin"], 0) + action["qty"]
    assert added == {a: 3, b: 1}, "the cart receives exactly what the card promised"


def test_the_streamed_approval_event_uses_the_same_merged_rows(monkeypatch):
    catalog = plain_products()
    (a, *_), _ = catalog[:2]
    message = AIMessage(
        content="",
        tool_calls=[
            {"name": "add_to_cart", "args": {"ref_or_asin": a}, "id": "s0"},
            {"name": "add_to_cart", "args": {"ref_or_asin": a}, "id": "s1"},
        ],
    )
    monkeypatch.setattr(copilot_agent, "build_chat_model", lambda temperature=0.2, fake=None: StreamingScripted(script=[message]))
    saver, thread_id = InMemorySaver(), f"t-multi-{uuid.uuid4().hex[:8]}"

    async def collect():
        return [
            e
            async for e in copilot_agent.stream_turn(
                "tambahkan ke keranjang", db_url=DB_URL, fake=True, thread_id=thread_id, checkpointer=saver
            )
        ]

    try:
        events = _run(collect())
    finally:
        _delete_rows(thread_id)

    approval, done = events[-2], events[-1]
    assert approval["type"] == "approval_required"
    assert [(i["asin"], i["qty"]) for i in approval["items"]] == [(a, 2)]
    assert done["pending_approval"]["items"] == approval["items"]
