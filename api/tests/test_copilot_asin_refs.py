"""A product ASIN is not a list number.

Catalog ASINs such as ``B001A6E4EQ`` contain digits. They were read as list number 1 (30% of the
catalog collides with one of the numbers 1 to 10), so approved items turned into other products."""

import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

import re

import pytest
from copilot_tools import ToolContext
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from multi_item_support import _cart_calls, _seed_registry, colliding_products
from test_copilot_cart_approval import _delete_rows, _resume, _script, _start, _thread_id

# ── ASINs are not list numbers ───────────────────────────────────────────────

@pytest.mark.parametrize("how_the_model_names_them", ["by list number", "by ASIN"])
def test_the_items_that_are_added_are_the_items_on_the_card(monkeypatch, how_the_model_names_them):
    catalog = colliding_products()
    """Every ASIN below reads as list number 1; before the fix they all collapsed onto product 1."""
    asins = [p[0] for p in catalog]
    tokens = [str(n) for n in range(1, 5)] if how_the_model_names_them == "by list number" else asins
    saver, thread_id = InMemorySaver(), _thread_id()
    try:
        _seed_registry(saver, thread_id, catalog)
        _script(monkeypatch, _cart_calls(tokens, "c"))
        card = _start("tambahkan semuanya ke keranjang", thread_id, saver)
        _script(monkeypatch, AIMessage(content="Sudah masuk keranjang kak."))
        done = _resume(thread_id, saver, approve=True)
    finally:
        _delete_rows(thread_id)

    assert [i["asin"] for i in card["pending_approval"]["items"]] == asins, "the card shows four different products"
    assert sorted(a["asin"] for a in done["ui_actions"]) == sorted(asins), "and those four are what is added"


class TestResolveRefLeavesAsinsAlone:
    @staticmethod
    def _ctx() -> ToolContext:
        ctx = ToolContext(db_url="postgresql://unused@localhost:1/unused")
        ctx.register({"asin": "B0001GZA12", "title": "one"})  # ref 1
        ctx.register({"asin": "B0001LMS5I", "title": "two"})  # ref 2
        ctx.register({"asin": "B007ABCDEF", "title": "three"})  # ref 3
        return ctx

    def test_a_registered_asin_resolves_to_itself_not_to_the_list_number_in_it(self):
        ctx = self._ctx()
        assert ctx.resolve_ref("B0001LMS5I") == "B0001LMS5I"  # its digits spell 1
        assert ctx.resolve_ref("B007ABCDEF") == "B007ABCDEF"  # its digits spell 7

    def test_the_lookup_ignores_case_and_spaces_and_returns_the_catalog_spelling(self):
        assert self._ctx().resolve_ref("  b0001lms5i ") == "B0001LMS5I"

    def test_an_asin_that_is_not_registered_is_returned_as_given(self):
        ctx = self._ctx()
        assert ctx.resolve_ref("B002AIMIFK") == "B002AIMIFK"  # digits spell 2, which is registered
        assert ctx.resolve_ref("B001A6E4EQ") == "B001A6E4EQ"  # digits spell 1, which is registered

    def test_list_numbers_and_words_still_work(self):
        ctx = self._ctx()
        assert ctx.resolve_ref("2") == "B0001LMS5I"
        assert ctx.resolve_ref(3) == "B007ABCDEF"
        assert ctx.resolve_ref("nomor 1") == "B0001GZA12"
        assert ctx.resolve_ref("ref 2") == "B0001LMS5I"
        assert ctx.resolve_ref("yang kedua") == "B0001LMS5I"
        assert ctx.resolve_ref("the third one") == "B007ABCDEF"

    def test_the_page_product_still_wins_for_ini(self):
        ctx = ToolContext(db_url="postgresql://unused@localhost:1/unused", page_asin="B009PAGE00")
        assert ctx.resolve_ref("produk ini") == "B009PAGE00"

    def test_a_short_id_equal_to_a_registered_asin_resolves_to_it(self):
        ctx = ToolContext(db_url="postgresql://unused@localhost:1/unused")
        ctx.register({"asin": "A1", "title": "one"})
        ctx.register({"asin": "B01", "title": "two"})
        assert ctx.resolve_ref("B01") == "B01"


# ── What the model reads after the shopper says yes ──────────────────────────

def test_the_results_the_model_reads_after_approval_match_the_items_it_asked_for(monkeypatch):
    """The model is shown what ran. If those results contradict its own request, it can try again.

    Live-site trace: after four items were approved the model asked for one more. Before the ASIN
    fix, three of the four results it was shown named one product while describing another.
    """
    import copilot_agent
    from test_copilot_cart_approval import StreamingScripted

    catalog = colliding_products()
    asins = [p[0] for p in catalog]
    seen: list = []

    def scripted(*messages):
        class Recording(StreamingScripted):
            def _generate(self, msgs, stop=None, run_manager=None, **kwargs):
                seen.append(list(msgs))
                return super()._generate(msgs, stop, run_manager, **kwargs)

        monkeypatch.setattr(copilot_agent, "build_chat_model", lambda temperature=0.2, fake=None: Recording(script=list(messages)))

    saver, thread_id = InMemorySaver(), _thread_id()
    try:
        _seed_registry(saver, thread_id, catalog)
        scripted(_cart_calls([str(n) for n in range(1, 5)], "c"))
        _start("tambahkan semuanya ke keranjang", thread_id, saver)
        seen.clear()
        scripted(AIMessage(content="Sudah masuk keranjang kak."))
        _resume(thread_id, saver, approve=True)
    finally:
        _delete_rows(thread_id)

    results = [str(m.content) for m in seen[0] if type(m).__name__ == "ToolMessage" and "Executed instead" in str(m.content)]
    assert len(results) == 4
    executed = [re.search(r'"ref_or_asin": "([^"]+)"', text).group(1) for text in results]
    described = [re.search(r'"ui_action": \{[^}]*?"asin": "([^"]+)"', text).group(1) for text in results]
    assert executed == described, "each result describes the product it says was added"
    assert sorted(described) == sorted(asins)
