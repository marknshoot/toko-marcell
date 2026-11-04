"""Per-turn model usage accounting (calls and tokens), including the guardrail."""

from __future__ import annotations

import threading
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler


class LLMCallCounter(BaseCallbackHandler):
    """Counts chat-model invocations and sums token usage for one turn.

    One instance is created per turn and passed to both the guardrail classifier and the
    agent, so its numbers are turn-scoped by construction (unlike ``result["messages"]``,
    which holds the whole checkpointed thread). Every model start is counted, including
    provider-fallback attempts, which matches how a provider's quota is consumed.
    Callbacks fire from the agent's worker thread, so updates take a lock.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self._saw_usage = False

    def on_chat_model_start(self, serialized: dict[str, Any], messages: Any, **kwargs: Any) -> None:
        with self._lock:
            self.calls += 1

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        for generations in getattr(response, "generations", []) or []:
            for generation in generations:
                message = getattr(generation, "message", None)
                usage = getattr(message, "usage_metadata", None)
                if not isinstance(usage, dict):
                    continue
                with self._lock:
                    self._saw_usage = True
                    self.input_tokens += int(usage.get("input_tokens") or 0)
                    self.output_tokens += int(usage.get("output_tokens") or 0)

    @property
    def usage(self) -> tuple[int | None, int | None]:
        """``(input, output)`` tokens, or ``(None, None)`` if the provider reported nothing."""
        if not self._saw_usage:
            return (None, None)
        return (self.input_tokens, self.output_tokens)
