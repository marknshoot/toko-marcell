"""Offline unit tests for copilot telemetry helpers (api/copilot_metrics.py)."""

import sys
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

import copilot_metrics


class TestDeriveOutcome:
    def test_plain_answer(self):
        assert copilot_metrics.derive_outcome(None, [], False) == "answered"
        assert copilot_metrics.derive_outcome(None, ["search_catalog"], False) == "answered"

    def test_guardrail(self):
        assert copilot_metrics.derive_outcome("injection", ["search_catalog"], False) == "guardrail"

    def test_error_wins(self):
        assert copilot_metrics.derive_outcome("off_topic", ["add_to_cart"], True) == "error"

    def test_cart(self):
        assert copilot_metrics.derive_outcome(None, ["search_catalog", "add_to_cart"], False) == "cart"

    def test_unanswered_flag_for_phase_10(self):
        assert copilot_metrics.derive_outcome(None, ["flag_unanswered"], False) == "unanswered"

    def test_none_tool_names(self):
        assert copilot_metrics.derive_outcome(None, None, False) == "answered"


class TestTokenExtraction:
    def test_sums_usage_across_ai_messages(self):
        messages = [
            HumanMessage(content="question"),
            AIMessage(
                content="tool call",
                usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            ),
            AIMessage(
                content="final",
                usage_metadata={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
            ),
        ]
        assert copilot_metrics.extract_token_usage(messages) == (13, 7)

    def test_none_when_usage_absent(self):
        messages = [HumanMessage(content="question"), AIMessage(content="answer")]
        assert copilot_metrics.extract_token_usage(messages) == (None, None)
        assert copilot_metrics.extract_token_usage([]) == (None, None)
        assert copilot_metrics.extract_token_usage(None) == (None, None)

    def test_zero_output_tokens_is_preserved(self):
        messages = [
            AIMessage(
                content="answer",
                usage_metadata={"input_tokens": 4, "output_tokens": 0, "total_tokens": 4},
            )
        ]
        assert copilot_metrics.extract_token_usage(messages) == (4, 0)


class TestRecordTurnNeverRaises:
    def test_db_failure_is_swallowed(self, monkeypatch):
        def _boom(_db_url):
            raise RuntimeError("database unavailable")

        monkeypatch.setattr(copilot_metrics, "_connect", _boom)

        # No exception even though the connection helper always fails.
        copilot_metrics.record_turn(
            thread_id="t1",
            session_id="s1",
            db_url="postgresql://unreachable",
            outcome="answered",
            tool_names=["search_catalog"],
            latency_ms=12.5,
            input_tokens=10,
            output_tokens=4,
            model="test",
        )

    def test_invalid_outcome_is_rejected_before_db(self, monkeypatch):
        called = False

        def _should_not_connect(_db_url):
            nonlocal called
            called = True
            raise AssertionError("must not touch the DB for an invalid outcome")

        monkeypatch.setattr(copilot_metrics, "_connect", _should_not_connect)

        copilot_metrics.record_turn(
            thread_id="t1",
            session_id="s1",
            db_url="postgresql://unreachable",
            outcome="bogus",
        )
        assert called is False
