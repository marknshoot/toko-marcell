"""Offline unit tests for copilot telemetry helpers (api/copilot_metrics.py)."""

import sys
from pathlib import Path
from types import SimpleNamespace

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

    def test_cart_wins_over_unanswered_flag(self):
        assert (
            copilot_metrics.derive_outcome(None, ["flag_unanswered", "add_to_cart"], False)
            == "cart"
        )

    def test_unanswered_flag(self):
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


class TestCostEstimate:
    def _set_prices(self, monkeypatch, input_price=0.15, output_price=0.60):
        monkeypatch.setattr(
            copilot_metrics,
            "get_settings",
            lambda: SimpleNamespace(
                LLM_INPUT_COST_PER_MILLION=input_price,
                LLM_OUTPUT_COST_PER_MILLION=output_price,
            ),
        )

    def test_one_million_input_tokens(self, monkeypatch):
        self._set_prices(monkeypatch)
        assert copilot_metrics.estimate_cost_usd(1_000_000, 0) == 0.15

    def test_one_million_output_tokens(self, monkeypatch):
        self._set_prices(monkeypatch)
        assert copilot_metrics.estimate_cost_usd(0, 1_000_000) == 0.60

    def test_mixed_token_counts(self, monkeypatch):
        self._set_prices(monkeypatch)
        assert copilot_metrics.estimate_cost_usd(1_000, 500) == 0.00045

    def test_one_missing_side_is_treated_as_zero(self, monkeypatch):
        self._set_prices(monkeypatch)
        assert copilot_metrics.estimate_cost_usd(1_000, None) == 0.00015
        assert copilot_metrics.estimate_cost_usd(None, 1_000) == 0.0006
        assert copilot_metrics.estimate_cost_usd(0, None) == 0.0

    def test_both_missing_is_unknown_not_free(self, monkeypatch):
        self._set_prices(monkeypatch)
        assert copilot_metrics.estimate_cost_usd(None, None) is None

    def test_configured_prices_are_used(self, monkeypatch):
        self._set_prices(monkeypatch, input_price=1.0, output_price=2.0)
        assert copilot_metrics.estimate_cost_usd(1_000_000, 1_000_000) == 3.0


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


class TestRecordTurnUnanswered:
    def test_question_is_truncated_and_passed_to_insert(self, monkeypatch):
        import contextlib

        captured = {}

        class _FakeCursor:
            def execute(self, sql, params):
                captured["sql"] = sql
                captured["params"] = params

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        class _FakeConn:
            def cursor(self):
                return _FakeCursor()

            def commit(self):
                pass

        @contextlib.contextmanager
        def _fake_connect(_db_url):
            yield _FakeConn()

        monkeypatch.setattr(copilot_metrics, "_connect", _fake_connect)

        copilot_metrics.record_turn(
            thread_id="t1",
            session_id="s1",
            db_url="postgresql://unreachable",
            outcome="unanswered",
            tool_names=["flag_unanswered"],
            unanswered_question="  " + "q" * 350 + "  ",
        )

        assert "unanswered_question" in captured["sql"]
        assert captured["params"][-1] == "q" * 300

    def test_blank_question_is_stored_as_null(self, monkeypatch):
        import contextlib

        captured = {}

        class _FakeCursor:
            def execute(self, sql, params):
                captured["params"] = params

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        class _FakeConn:
            def cursor(self):
                return _FakeCursor()

            def commit(self):
                pass

        @contextlib.contextmanager
        def _fake_connect(_db_url):
            yield _FakeConn()

        monkeypatch.setattr(copilot_metrics, "_connect", _fake_connect)

        copilot_metrics.record_turn(
            thread_id="t1",
            session_id=None,
            db_url="postgresql://unreachable",
            outcome="unanswered",
            tool_names=["flag_unanswered"],
            unanswered_question="   ",
        )

        assert captured["params"][-1] is None


class TestRecordTurnCost:
    def test_cost_is_derived_when_tokens_are_reported(self, monkeypatch):
        import contextlib

        captured = {}

        class _FakeCursor:
            def execute(self, sql, params):
                captured["params"] = params

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        class _FakeConn:
            def cursor(self):
                return _FakeCursor()

            def commit(self):
                pass

        @contextlib.contextmanager
        def _fake_connect(_db_url):
            yield _FakeConn()

        monkeypatch.setattr(copilot_metrics, "_connect", _fake_connect)
        monkeypatch.setattr(
            copilot_metrics,
            "get_settings",
            lambda: SimpleNamespace(
                LLM_INPUT_COST_PER_MILLION=0.15,
                LLM_OUTPUT_COST_PER_MILLION=0.60,
            ),
        )

        copilot_metrics.record_turn(
            thread_id="t1",
            session_id="s1",
            db_url="postgresql://unreachable",
            outcome="answered",
            input_tokens=1_000_000,
            output_tokens=0,
            model="test-model",
        )

        # INSERT column order ends with model, cost_usd, unanswered_question.
        assert captured["params"][9] == 0.15
