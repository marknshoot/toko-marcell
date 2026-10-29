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


class TestSqlAndPoolHardening:
    def test_catalog_categories_uses_ttl_cache(self, monkeypatch):
        import contextlib

        import copilot_agent

        calls = []

        class _FakeCur:
            def execute(self, q):
                calls.append(q)

            def fetchall(self):
                return [("Pants",), ("Shirts",)]

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        class _FakeConn:
            def cursor(self):
                return _FakeCur()

        @contextlib.contextmanager
        def _fake_connect(_url):
            yield _FakeConn()

        copilot_agent._CATEGORIES_CACHE.clear()
        monkeypatch.setattr(copilot_agent, "_connect", _fake_connect)
        c1 = copilot_agent._catalog_categories("postgresql://cache-test")
        c2 = copilot_agent._catalog_categories("postgresql://cache-test")
        assert c1 == ["Pants", "Shirts"]
        assert c2 == ["Pants", "Shirts"]
        assert len(calls) == 1

    def test_get_product_reviews_caps_topic_keywords_at_5(self, monkeypatch):
        import contextlib

        executed = []

        class _FakeCur:
            def execute(self, sql, params=None):
                executed.append((sql, params))

            def fetchone(self):
                return (3, 4.5, 2, 1, 0, 0, 0)

            def fetchall(self):
                return [(1, 5.0, "Great", "Comfortable fit", "Buyer", True, "2025-01-01")]

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        class _FakeConn:
            def cursor(self):
                return _FakeCur()

        @contextlib.contextmanager
        def _fake_connect(_url):
            yield _FakeConn()

        monkeypatch.setattr(agent_tools, "_connect", _fake_connect)
        agent_tools.get_product_reviews("B01", topic="one two three four five six seven eight nine")
        # 2nd query is the topic-filtered SELECT: asin + (5 keywords * 2) + limit = 12 params max
        _, params = executed[1]
        assert len(params) == 12

    def test_score_policy_chunks_lexical_ranks_bilingual_keywords(self):
        import agent_tools

        chunks = [
            (1, "shipping", "3. Shipping & Delivery Guidelines (Pengiriman & Ongkir)", "Gratis ongkir Rp 300.000"),
            (2, "brand_sizing", "4. Birkenstock (Contoured Footwear)", "EU 41 cork footbed"),
        ]
        ranked = agent_tools.score_policy_chunks_lexical("birkenstock mending naik atau turun size", chunks)
        assert ranked and ranked[0][0] == 2

    def test_hypothetical_questions_strips_brand_section_number(self):
        from knowledge_seed import _hypothetical_questions

        chunk = {
            "category": "brand_sizing",
            "title": "4. Birkenstock (Contoured Footwear — e.g. Arizona, Boston)",
            "content": "x",
        }
        qs = _hypothetical_questions(chunk)
        assert 1 <= len(qs) <= 5
        assert any(q.startswith("Birkenstock") for q, _ in qs)

    def test_ragas_id_based_context_metrics(self):
        import sys
        import types

        for mod_name, cls_name in [
            ("langchain_community.chat_models.vertexai", "ChatVertexAI"),
            ("langchain_community.llms", "VertexAI"),
        ]:
            mod = sys.modules.get(mod_name) or types.ModuleType(mod_name)
            if not hasattr(mod, cls_name):
                setattr(mod, cls_name, type(cls_name, (), {}))
            sys.modules[mod_name] = mod

        from ragas import EvaluationDataset, SingleTurnSample, evaluate
        from ragas.metrics import IDBasedContextPrecision, IDBasedContextRecall

        sample = SingleTurnSample(
            user_input="ongkir ke surabaya berapa lama",
            retrieved_contexts=["Shipping & Delivery Guidelines"],
            reference_contexts=["Shipping & Delivery Guidelines"],
            retrieved_context_ids=["3"],
            reference_context_ids=["3"],
        )
        res = evaluate(
            dataset=EvaluationDataset(samples=[sample]),
            metrics=[IDBasedContextPrecision(), IDBasedContextRecall()],
            show_progress=False,
        )
        assert res.scores[0]["id_based_context_precision"] == 1.0
        assert res.scores[0]["id_based_context_recall"] == 1.0
