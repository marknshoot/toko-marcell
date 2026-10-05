"""Unit tests for the v2 copilot Pydantic schemas (api/schemas.py).

Pure validation, no DB/LLM — part of the offline CI subset.
"""

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

from schemas import (
    CopilotChatV2In,
    CopilotChatV2Out,
    CopilotContextV2,
    CopilotProductV2,
)


class TestRequestV2:
    def test_minimal_valid(self):
        m = CopilotChatV2In(thread_id="abc-123", message="ada celana chino?")
        assert m.thread_id == "abc-123"
        assert m.context is None
        assert m.session_id is None

    def test_with_context(self):
        m = CopilotChatV2In(
            thread_id="t1",
            message="yang ini ada ukuran L?",
            context={"page_asin": "B01", "referenced_asins": ["B02", "B03"]},
        )
        assert m.context.page_asin == "B01"
        assert len(m.context.referenced_asins) == 2

    def test_extra_forbidden(self):
        with pytest.raises(ValidationError):
            CopilotChatV2In(thread_id="t1", message="hi", image_url="data:...")

    def test_message_required_nonempty(self):
        with pytest.raises(ValidationError):
            CopilotChatV2In(thread_id="t1", message="")

    def test_message_max_length(self):
        with pytest.raises(ValidationError):
            CopilotChatV2In(thread_id="t1", message="x" * 2001)

    def test_referenced_asins_max_3(self):
        with pytest.raises(ValidationError):
            CopilotContextV2(referenced_asins=["a", "b", "c", "d"])

    def test_thread_id_required(self):
        with pytest.raises(ValidationError):
            CopilotChatV2In(message="hi")


class TestResponseV2:
    def test_minimal_response(self):
        out = CopilotChatV2Out(reply="Halo kak!", thread_id="t1", took_ms=12.3)
        assert out.products == []
        assert out.guardrail is None

    def test_full_response(self):
        out = CopilotChatV2Out(
            reply="Ini kak",
            thread_id="t1",
            products=[{
                "ref": 1, "asin": "B01", "title": "Chino", "priceIdr": 199000,
            }],
            citations=[{"type": "product", "title": "Chino", "ref": "B01"}],
            suggestions=[{"label": "Mirip lebih murah", "prompt": "yang mirip tapi lebih murah"}],
            ui_actions=[{"type": "add_to_cart", "asin": "B01", "qty": 1, "title": "Chino", "priceIdr": 199000}],
            tool_calls=[{"name": "search_catalog", "args": {"query": "chino"}}],
            guardrail=None,
            took_ms=50.0,
        )
        assert out.products[0].ref == 1
        assert out.ui_actions[0].type == "add_to_cart"
        assert out.citations[0].type == "product"

    def test_products_max_6(self):
        with pytest.raises(ValidationError):
            CopilotChatV2Out(
                reply="x", thread_id="t1", took_ms=1.0,
                products=[{"ref": i, "asin": f"B{i}", "title": "t", "priceIdr": 1} for i in range(7)],
            )

    def test_guardrail_enum(self):
        out = CopilotChatV2Out(reply="x", thread_id="t1", took_ms=1.0, guardrail="greeting")
        assert out.guardrail == "greeting"
        with pytest.raises(ValidationError):
            CopilotChatV2Out(reply="x", thread_id="t1", took_ms=1.0, guardrail="bogus")

    def test_product_fit_optional(self):
        p = CopilotProductV2(ref=1, asin="B01", title="t", priceIdr=1, fit={"label": "runs_small"})
        assert p.fit["label"] == "runs_small"
        p2 = CopilotProductV2(ref=2, asin="B02", title="t", priceIdr=1)
        assert p2.fit is None
