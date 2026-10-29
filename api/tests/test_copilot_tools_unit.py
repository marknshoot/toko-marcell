"""Unit tests for the copilot v2 ToolContext (registry + ref resolution).

Pure logic (no DB) — part of the offline CI subset.
"""

import json
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

    def test_register_from_prior_turn_is_resolvable_but_not_a_current_card(self):
        ctx = _ctx()
        ref = ctx.register({"asin": "A1", "title": "one"}, from_current_turn=False)
        assert ctx.turn_products() == []
        assert ctx.resolve_ref("1") == "A1"

        # showing the same product again this turn promotes it to a card
        assert ctx.register({"asin": "A1", "title": "one"}) == ref
        assert [p["ref"] for p in ctx.turn_products()] == [ref]

    def test_turn_products_only_lists_this_turn(self):
        ctx = _ctx()
        ctx.register({"asin": "OLD", "title": "old"}, from_current_turn=False)
        ctx.register({"asin": "A1", "title": "one"})
        ctx.register({"asin": "A2", "title": "two"})
        assert [p["asin"] for p in ctx.turn_products()] == ["A1", "A2"]
        assert len(ctx.turn_products(limit=1)) == 1


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

    def test_resolve_ordinal_words(self):
        ctx = _ctx()
        for i in range(1, 7):
            ctx.register({"asin": f"A{i}", "title": str(i)})
        assert ctx.resolve_ref("yang kedua") == "A2"
        assert ctx.resolve_ref("pertama") == "A1"
        assert ctx.resolve_ref("yang ketiga dong") == "A3"
        assert ctx.resolve_ref("the second one") == "A2"
        assert ctx.resolve_ref("keenam") == "A6"

    def test_resolve_ordinal_without_registry_entry_falls_through(self):
        ctx = _ctx()
        ctx.register({"asin": "A1", "title": "one"})
        assert ctx.resolve_ref("yang kedua") == "yang kedua"

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


class TestFlagUnanswered:
    def test_sets_context_and_truncates_question(self):
        ctx = _ctx()
        flag = next(t for t in build_tools(ctx) if t.name == "flag_unanswered")

        result = flag.invoke({"question": "  " + "x" * 350 + "  ", "reason": "no evidence"})

        assert result == {"ok": True}
        assert ctx.unanswered is True
        assert ctx.unanswered_question == "x" * 300

    def test_blank_question_still_marks_the_turn(self):
        ctx = _ctx()
        flag = next(t for t in build_tools(ctx) if t.name == "flag_unanswered")

        assert flag.invoke({"question": "   ", "reason": "no evidence"}) == {"ok": True}
        assert ctx.unanswered is True
        assert ctx.unanswered_question is None

    def test_public_tool_names_match_build_tools(self):
        from routers.copilot import V2_TOOL_NAMES

        names = [t.name for t in build_tools(_ctx())]
        assert len(names) == 7
        assert "flag_unanswered" in names
        assert names == V2_TOOL_NAMES


class TestPromptScope:
    def test_prompt_covers_three_jobs_and_has_no_handoff_language(self):
        from copilot_agent import ADMIN_SYSTEM_PROMPT

        prompt = ADMIN_SYSTEM_PROMPT
        lowered = prompt.lower()

        assert "JOB 1 — SIZING" in prompt
        assert "JOB 2 — STORE POLICY" in prompt
        assert "JOB 3 — OUTFIT UNDER BUDGET" in prompt
        assert "recommend_size" in prompt
        assert "lookup_store_policy" in prompt
        assert "build_outfit" in prompt
        assert "search bar" in lowered
        assert "GROUNDING" in prompt
        assert "SECURITY" in prompt

        for forbidden in ("whatsapp", "review", "handoff", "human-agent", "human agent"):
            assert forbidden not in lowered


class TestCheckpointSeeding:
    class _FakeCheckpointer:
        def __init__(self, messages):
            self._messages = messages

        def get_tuple(self, config):
            from types import SimpleNamespace

            return SimpleNamespace(checkpoint={"channel_values": {"messages": self._messages}})

    def test_seed_registers_prior_turn_products(self):
        import copilot_agent
        from langchain_core.messages import ToolMessage

        messages = [
            ToolMessage(
                content=json.dumps([{"asin": "A1", "title": "one", "priceIdr": 100}]),
                tool_call_id="c1",
            ),
            ToolMessage(
                content=json.dumps({"asin": "A2", "title": "two", "priceIdr": 200}),
                tool_call_id="c2",
            ),
            ToolMessage(
                content=json.dumps({
                    "within_budget": True,
                    "items": {"top": {"asin": "A3", "title": "three", "priceIdr": 300}},
                }),
                tool_call_id="c3",
            ),
        ]
        ctx = _ctx()
        copilot_agent._seed_registry_from_checkpoint(
            ctx, self._FakeCheckpointer(messages), "thread-1"
        )

        # prior-turn products are resolvable, but not shown as this turn's cards
        assert ctx.turn_products() == []
        assert ctx.resolve_ref("nomor 1") == "A1"
        assert ctx.resolve_ref("yang kedua") == "A2"
        assert ctx.resolve_ref("yang ketiga") == "A3"

    def test_seed_is_a_noop_without_checkpointer_or_thread(self):
        import copilot_agent

        ctx = _ctx()
        copilot_agent._seed_registry_from_checkpoint(ctx, None, "thread-1")
        copilot_agent._seed_registry_from_checkpoint(ctx, self._FakeCheckpointer([]), None)
        assert ctx.registry == {}


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
