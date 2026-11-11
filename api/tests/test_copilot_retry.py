"""Retry a failed copilot turn by resuming its checkpoint, without duplicating history."""

import asyncio
import sys
from pathlib import Path
from typing import Any

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

import httpx
import openai
import pytest
from copilot_agent import Failure, LLMUnavailable, _classify_failure, stream_turn
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

DB_URL = "postgresql://toko:toko@localhost:5432/toko"
QUESTION = "ada celana chino hitam?"


def _rate_limit_error(retry_after: str = "7") -> openai.RateLimitError:
    request = httpx.Request("POST", "https://api.mistral.ai/v1/chat/completions")
    response = httpx.Response(429, request=request, headers={"retry-after": retry_after})
    return openai.RateLimitError("rate limited", response=response, body=None)


class _FlakyModel(GenericFakeChatModel):
    """Fake model that raises once, then answers. Calls are counted so a retry can be
    asserted not to reach the model when the checkpoint already has an answer."""

    error: Any = None
    calls: int = 0

    def __init__(self, messages, error):
        super().__init__(messages=iter(messages), error=error)

    def bind_tools(self, tools, **kwargs):  # noqa: ARG002
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls += 1
        self._maybe_raise()
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls += 1
        self._maybe_raise()
        yield from super()._stream(messages, stop=stop, run_manager=run_manager, **kwargs)

    def _maybe_raise(self):
        if self.error is not None:
            error, self.error = self.error, None
            raise error


@pytest.fixture
def patched_agent(monkeypatch):
    """No tools, no catalog query, and telemetry captured instead of written to Postgres."""
    recorded = []
    monkeypatch.setattr("copilot_turn.record_turn", lambda **kwargs: recorded.append(kwargs))
    monkeypatch.setattr("copilot_agent.build_tools", lambda ctx, categories=None: [])
    monkeypatch.setattr("copilot_agent._catalog_categories", lambda db_url: [])
    return recorded


async def _collect(turn):
    return [event async for event in turn]


def _run(*args, **kwargs):
    return asyncio.run(_collect(stream_turn(*args, **kwargs)))


def _done(events):
    return next(event for event in events if event["type"] == "done")


def test_classify_rate_limit_keeps_the_provider_retry_after():
    failure = _classify_failure(_rate_limit_error("7"))
    assert failure == Failure("provider_rate_limited", True, 7)


def test_classify_timeout_and_server_error_are_retryable():
    assert _classify_failure(openai.APITimeoutError(request=httpx.Request("POST", "https://x"))).retryable
    assert _classify_failure(openai.APIConnectionError(request=httpx.Request("POST", "https://x"))).code == (
        "provider_unavailable"
    )
    assert _classify_failure(RuntimeError("boom")).code == "internal_error"


def test_no_provider_is_not_retryable():
    failure = _classify_failure(LLMUnavailable("no key"))
    assert failure == Failure("llm_unavailable", False, None)


def test_retry_resumes_the_failed_checkpoint_without_a_second_human_message(patched_agent, monkeypatch):
    model = _FlakyModel([AIMessage(content="Chino hitam ada kak.")], _rate_limit_error("7"))
    monkeypatch.setattr("copilot_agent.build_chat_model", lambda temperature=0.2, fake=None: model)

    saver = InMemorySaver()
    thread_id = "t-retry-1"

    first = _run(
        QUESTION,
        db_url=DB_URL,
        session_id="s1",
        thread_id=thread_id,
        checkpointer=saver,
        fake=True,
    )
    first_done = _done(first)
    assert first_done["error"] is True
    assert first_done["error_code"] == "provider_rate_limited"
    assert first_done["retryable"] is True
    assert first_done["retry_after"] == 7
    assert first_done["partial"] is False
    assert patched_agent[-1]["is_retry"] is False

    second = _run(
        "",
        db_url=DB_URL,
        session_id="s1",
        thread_id=thread_id,
        checkpointer=saver,
        fake=True,
        retry=True,
    )
    second_done = _done(second)
    assert second_done["error"] is False
    assert second_done["reply"] == "Chino hitam ada kak."
    assert patched_agent[-1]["is_retry"] is True

    # The checkpoint must hold exactly one HumanMessage: a retry resumes the graph, it does
    # not append the prompt again (which would double the context the model reads).
    saved = saver.get_tuple({"configurable": {"thread_id": thread_id}})
    messages = saved.checkpoint["channel_values"]["messages"]
    human = [m for m in messages if isinstance(m, HumanMessage)]
    assert len(human) == 1
    assert human[0].content == QUESTION
    assert [m.content for m in messages if isinstance(m, AIMessage)] == ["Chino hitam ada kak."]


def test_retry_of_an_already_finished_turn_does_not_call_the_model(patched_agent, monkeypatch):
    model = _FlakyModel([AIMessage(content="Chino hitam ada kak.")], _rate_limit_error())
    monkeypatch.setattr("copilot_agent.build_chat_model", lambda temperature=0.2, fake=None: model)

    saver = InMemorySaver()
    thread_id = "t-retry-2"
    _run(QUESTION, db_url=DB_URL, session_id="s1", thread_id=thread_id, checkpointer=saver, fake=True)
    _run("", db_url=DB_URL, session_id="s1", thread_id=thread_id, checkpointer=saver, fake=True, retry=True)
    calls_after_success = model.calls

    again = _run(
        "",
        db_url=DB_URL,
        session_id="s1",
        thread_id=thread_id,
        checkpointer=saver,
        fake=True,
        retry=True,
    )
    done = _done(again)
    assert done["error"] is False
    assert done["reply"] == "Chino hitam ada kak."
    assert model.calls == calls_after_success


def test_retry_of_an_unknown_thread_is_reported_as_not_found(patched_agent, monkeypatch):
    model = _FlakyModel([AIMessage(content="tidak dipakai")], None)
    monkeypatch.setattr("copilot_agent.build_chat_model", lambda temperature=0.2, fake=None: model)

    events = _run(
        "",
        db_url=DB_URL,
        session_id="s1",
        thread_id="t-never-seen",
        checkpointer=InMemorySaver(),
        fake=True,
        retry=True,
    )
    done = _done(events)
    assert done["error"] is True
    assert done["error_code"] == "thread_not_found"
    assert done["retryable"] is False
    assert model.calls == 0
