"""Unit tests for the copilot v2 ToolContext (registry + ref resolution).

Pure logic (no DB) — part of the offline CI subset.
"""

import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

import agent_tools
from copilot_tools import MAX_REGISTRY, ToolContext, build_tools


def _ctx(**kw):
    return ToolContext(db_url="postgresql://x", **kw)


class TestRegistry:
    def test_register_assigns_sequential_refs(self):
        ctx = _ctx()
        r1 = ctx.register({"asin": "A1", "title": "one"})
        r2 = ctx.register({"asin": "A2", "title": "two"})
        assert r1 == 1
        assert r2 == 2

    def test_register_dedupes_by_asin(self):
        ctx = _ctx()
        r1 = ctx.register({"asin": "A1", "title": "one"})
        r1b = ctx.register({"asin": "A1", "title": "one again"})
        assert r1 == r1b
        assert len(ctx.registry) == 1

    def test_registry_bounded(self):
        ctx = _ctx()
        for i in range(MAX_REGISTRY + 5):
            ctx.register({"asin": f"A{i}", "title": str(i)})
        assert len(ctx.registry) <= MAX_REGISTRY


class TestResolveRef:
    def test_resolve_numeric_ref(self):
        ctx = _ctx()
        ctx.register({"asin": "A1", "title": "one"})
        ctx.register({"asin": "A2", "title": "two"})
        assert ctx.resolve_ref("2") == "A2"
        assert ctx.resolve_ref(1) == "A1"

    def test_resolve_phrase_nomor(self):
        ctx = _ctx()
        ctx.register({"asin": "A1", "title": "one"})
        ctx.register({"asin": "A2", "title": "two"})
        assert ctx.resolve_ref("nomor 2") == "A2"
        assert ctx.resolve_ref("yang ke 1") == "A1"

    def test_resolve_this_uses_page_asin(self):
        ctx = _ctx(page_asin="PAGE1")
        assert ctx.resolve_ref("ini") == "PAGE1"
        assert ctx.resolve_ref("produk ini") == "PAGE1"
        assert ctx.resolve_ref("this") == "PAGE1"

    def test_resolve_literal_asin(self):
        ctx = _ctx()
        assert ctx.resolve_ref("B0001YRQHQ") == "B0001YRQHQ"

    def test_resolve_none(self):
        ctx = _ctx()
        assert ctx.resolve_ref(None) is None

    def test_resolve_unknown_ref_falls_through_to_literal(self):
        ctx = _ctx()
        assert ctx.resolve_ref("5") == "5"


class TestAddToCartAction:
    def test_action_carries_everything_the_local_cart_needs(self, monkeypatch):
        monkeypatch.setattr(
            agent_tools,
            "get_product_details",
            lambda asin, db_url=None: {
                "id": 5,
                "asin": asin,
                "title": "Volcom Chino",
                "priceIdr": 495800,
                "imageUrl": "https://example.com/chino.jpg",
            },
        )
        ctx = _ctx()
        add_to_cart = next(t for t in build_tools(ctx) if t.name == "add_to_cart")

        result = add_to_cart.invoke({"ref_or_asin": "B01", "qty": 2, "size": "M"})

        action = result["ui_action"]
        assert result["ok"] is True
        assert action["type"] == "add_to_cart"
        assert action["id"] == 5
        assert action["asin"] == "B01"
        assert action["qty"] == 2
        assert action["size"] == "M"
        assert action["priceIdr"] == 495800
        assert action["imageUrl"] == "https://example.com/chino.jpg"
        assert ctx.ui_actions == [action]
