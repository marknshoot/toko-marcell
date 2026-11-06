"""A cart confirmation that comes back for a second question must not lose the first adds.

Live-site failure: four items were approved, the model then asked for one more, and only that last
item reached the cart, because the response to the second question carried no cart actions."""

import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

import copilot_agent  # noqa: F401
import pytest  # noqa: F401
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from multi_item_support import _cart_calls, plain_products
from test_copilot_cart_approval import _delete_rows, _resume, _rows, _script, _start, _thread_id

# ── A resumed run that pauses again keeps what it already did ────────────────

def test_a_second_confirmation_still_returns_the_adds_that_already_ran(monkeypatch):
    catalog = plain_products()
    first_four, fifth = [p[0] for p in catalog[:4]], catalog[4][0]
    saver, thread_id = InMemorySaver(), _thread_id()
    try:
        _script(monkeypatch, _cart_calls(first_four, "a"))
        card_one = _start("tambahkan semuanya ke keranjang", thread_id, saver)

        # After the four adds ran, the model asks for one more. That pauses the same turn again.
        _script(monkeypatch, _cart_calls([fifth], "b"))
        card_two = _resume(thread_id, saver, approve=True)

        _script(monkeypatch, AIMessage(content="Semua sudah masuk keranjang kak."))
        final = _resume(thread_id, saver, approve=True)
        rows = _rows(thread_id)
    finally:
        _delete_rows(thread_id)

    assert [i["asin"] for i in card_one["pending_approval"]["items"]] == first_four
    assert card_one["ui_actions"] == []

    assert [i["asin"] for i in card_two["pending_approval"]["items"]] == [fifth]
    assert sorted(a["asin"] for a in card_two["ui_actions"]) == sorted(first_four), (
        "the four adds ran before the second pause, so the browser must be told about them"
    )

    assert [a["asin"] for a in final["ui_actions"]] == [fifth]
    assert "pending_approval" not in final
    assert final["reply"] == "Semua sudah masuk keranjang kak."

    everything = [a["asin"] for a in card_two["ui_actions"] + final["ui_actions"]]
    assert sorted(everything) == sorted([*first_four, fifth]), "all five once, none twice"

    assert len(rows) == 1, "one row for the whole turn, however many times it paused"
    outcome, tool_names, llm_calls, _route = rows[0]
    assert outcome == "cart" and "add_to_cart" in tool_names
    assert llm_calls == 3, "one model call before each of the two pauses plus the final reply"


def test_declining_the_second_confirmation_still_returns_the_first_adds(monkeypatch):
    catalog = plain_products()
    first_two, third = [p[0] for p in catalog[:2]], catalog[2][0]
    saver, thread_id = InMemorySaver(), _thread_id()
    try:
        _script(monkeypatch, _cart_calls(first_two, "a"))
        _start("tambahkan keduanya ke keranjang", thread_id, saver)
        _script(monkeypatch, _cart_calls([third], "b"))
        card_two = _resume(thread_id, saver, approve=True)
        _script(monkeypatch, AIMessage(content="Baik kak, yang ketiga tidak jadi."))
        final = _resume(thread_id, saver, approve=False)
    finally:
        _delete_rows(thread_id)

    assert sorted(a["asin"] for a in card_two["ui_actions"]) == sorted(first_two)
    assert final["ui_actions"] == []
