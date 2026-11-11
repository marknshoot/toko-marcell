"""Graph state and the stand-in chat model used when no provider is configured."""

from __future__ import annotations

from typing import Annotated, NotRequired

from langchain.agents import AgentState
from langchain.agents.middleware.types import PrivateStateAttr
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.outputs import ChatResult
from langgraph.channels.untracked_value import UntrackedValue


class CopilotState(AgentState):
    """Graph state: the agent's messages plus what the copilot adds per turn."""

    # The guardrail verdict for the current turn (``None`` = on-topic). Overwritten every turn.
    guardrail: NotRequired[str | None]
    # The products shown so far, with the numeric refs the shopper and the model use
    # ("[2]", "yang kedua"): ``[{"ref": 1, "asin": ..., ...}]``. Saved with the thread.
    shown_products: NotRequired[list[dict]]
    # The page-product and browsing part of the context block, built once per turn. Per-turn
    # scratch: not saved with the thread and not part of the graph's input or output.
    turn_context: NotRequired[Annotated[str, UntrackedValue, PrivateStateAttr]]
    # Work done before a cart-approval pause (model calls, tokens, time), saved with the thread so
    # the request that resumes the turn can record one complete row. ``None`` outside a pause.
    turn_carry: NotRequired[dict | None]


class LLMUnavailable(RuntimeError):  # noqa: N818 - reads better than "...Error" at call sites
    """Raised when a message reaches the model but no LLM provider is configured."""


class _UnavailableChatModel(BaseChatModel):
    """Stand-in model used when no provider is configured.

    The graph must still be buildable without one: the guardrail is a node, so a greeting
    can be answered with a canned reply even though no model exists. A message that does
    reach the model fails with ``LLMUnavailable``, which the turn maps to the same 503 as before.
    """

    @property
    def _llm_type(self) -> str:
        return "unavailable"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        raise LLMUnavailable("no LLM provider is configured")

    def bind_tools(self, tools, **kwargs):  # noqa: ARG002
        return self
