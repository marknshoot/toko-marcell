"""Agent middleware: the graph's guardrail, memory and context hooks.

Each class owns one concern and runs at a documented point of the turn:

    GuardrailMiddleware        before_agent   canned reply for greeting/off-topic/injection
    RegistryMiddleware         before/after   shown-products registry persists across turns
    ContextInjectionMiddleware wrap_model     prepend <store_context> to the system prompt
    TrimHistoryMiddleware      wrap_model     token-budget history trimming
    TurnCarryMiddleware        after_model    save usage before a cart-approval pause
    LoopCapMiddleware          before_model   bound model→tool iterations
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from copilot_approval import CART_TOOL, CartApproval
from copilot_context import (
    _seed_registry_from_checkpoint,
    build_registry_block,
    build_static_context,
    compose_context,
)
from copilot_prompts import _LOOP_CAP_MARKER, ADMIN_SYSTEM_PROMPT, LOOP_CAP_REPLY
from copilot_state import CopilotState
from copilot_tools import ToolContext
from copilot_turn import _Turn, message_text
from guardrail import classify, trim_guardrail_history
from langchain.agents.middleware import AgentMiddleware, ModelCallLimitMiddleware
from langchain.agents.middleware.types import hook_config
from langchain_core.messages import AIMessage, HumanMessage

logger = logging.getLogger(__name__)


def _guardrail_history(messages: list[Any]) -> list[dict[str, str]]:
    """Recent user/assistant text from the graph state, trimmed for the classifier.

    Same shape the checkpoint reader used to produce: human and assistant messages with text,
    tool traffic skipped.
    """
    history: list[dict[str, str]] = []
    for m in messages:
        if isinstance(m, HumanMessage):
            role = "user"
        elif isinstance(m, AIMessage):
            role = "assistant"
        else:
            continue
        text = message_text(m)
        if text.strip():
            history.append({"role": role, "content": text})
    return trim_guardrail_history(history)


class GuardrailMiddleware(AgentMiddleware):
    """First node of the graph: classify the newest message with the existing guardrail.

    Uses the same ``classify`` as before (LLM classifier with regex fallback; nothing here
    decides anything the old code did not). A label ends the run with the canned reply.
    """

    state_schema = CopilotState

    def __init__(self, *, use_llm: bool):
        super().__init__()
        self._use_llm = use_llm

    def _verdict(self, state: dict[str, Any]) -> dict[str, Any]:
        messages = state["messages"]
        label, canned = classify(
            message_text(messages[-1]),
            history=_guardrail_history(messages[:-1]),
            use_llm=self._use_llm,
        )
        if label is None:
            return {"guardrail": None}
        return {"guardrail": label, "messages": [AIMessage(content=canned)], "jump_to": "end"}

    @hook_config(can_jump_to=["end"])
    def before_agent(self, state, runtime):  # type: ignore[override]
        return self._verdict(state)

    @hook_config(can_jump_to=["end"])
    async def abefore_agent(self, state, runtime):  # type: ignore[override]
        # classify may make a blocking model call, so keep it off the event loop.
        return await asyncio.to_thread(self._verdict, state)


class RegistryMiddleware(AgentMiddleware):
    """Keep the shown-products registry in the graph state, so refs survive between turns.

    Before the agent runs, restore the registry from ``state["shown_products"]``. Threads saved
    before this field existed have no such key; for those the registry is rebuilt once by
    replaying earlier tool results (the old behaviour) and then saved like any other.
    After the agent finishes, save the registry back. Guardrail turns skip both steps: they exit
    before this node's seed and must not overwrite the saved registry with an empty one.
    """

    state_schema = CopilotState

    def __init__(self, ctx: ToolContext, checkpointer: Any, thread_id: str | None):
        super().__init__()
        self._ctx = ctx
        self._checkpointer = checkpointer
        self._thread_id = thread_id

    def _seed(self, state: dict[str, Any]) -> None:
        saved = state.get("shown_products")
        if saved is not None:
            self._ctx.seed(saved)
        elif len(state.get("messages", [])) > 1:
            # A thread that predates the state field. A brand-new thread (only the current
            # message) has nothing to replay and costs no checkpoint read.
            _seed_registry_from_checkpoint(self._ctx, self._checkpointer, self._thread_id)

    def before_agent(self, state, runtime):  # type: ignore[override]
        self._seed(state)
        return None

    async def abefore_agent(self, state, runtime):  # type: ignore[override]
        await asyncio.to_thread(self._seed, state)
        return None

    def after_agent(self, state, runtime):  # type: ignore[override]
        if state.get("guardrail"):
            return None
        return {"shown_products": self._ctx.snapshot(), "turn_carry": None}

    async def aafter_agent(self, state, runtime):  # type: ignore[override]
        return self.after_agent(state, runtime)


class TurnCarryMiddleware(AgentMiddleware):
    """Save what the turn has used so far, just before a cart-approval pause.

    An approval ends one HTTP request and the answer arrives in another, so the request that
    resumes the turn would otherwise record only its own model calls and time. This must be
    listed AFTER the approval middleware: ``after_model`` hooks run in reverse list order, and the
    pause happens inside the approval middleware's hook.
    """

    state_schema = CopilotState

    def __init__(self, ctx: ToolContext, turn: _Turn, approval: CartApproval):
        super().__init__()
        self._ctx = ctx
        self._turn = turn
        self._approval = approval

    def after_model(self, state, runtime):  # type: ignore[override]
        last_ai = next((m for m in reversed(state["messages"]) if isinstance(m, AIMessage)), None)
        if not last_ai or not any(call.get("name") == CART_TOOL for call in last_ai.tool_calls):
            return None
        # The resolved items are saved here, before the pause, because the approval hook is
        # re-run from its start when the answer arrives (see CartApproval).
        carry = {**self._turn.totals(), "approval_items": self._approval.items_for_message(last_ai)}
        return {"turn_carry": carry, "shown_products": self._ctx.snapshot()}

    async def aafter_model(self, state, runtime):  # type: ignore[override]
        return self.after_model(state, runtime)


class LoopCapMiddleware(ModelCallLimitMiddleware):
    """The model-call cap, with a shopper-facing reply instead of the library's English text.

    ``ModelCallLimitMiddleware`` builds its exit message in a module-level function, so the text
    can only be changed by overriding ``before_model``. The message is tagged so the turn can
    be recognised (and recorded as unanswered) without matching on its wording.
    """

    @hook_config(can_jump_to=["end"])
    def before_model(self, state, runtime):  # type: ignore[override]
        update = super().before_model(state, runtime)
        if update and update.get("jump_to") == "end":
            update["messages"] = [AIMessage(content=LOOP_CAP_REPLY, additional_kwargs={_LOOP_CAP_MARKER: True})]
        return update

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state, runtime):  # type: ignore[override]
        return self.before_model(state, runtime)


class ContextInjectionMiddleware(AgentMiddleware):
    """Prepend the authoritative context block to the system prompt each model call.

    The database part (page product, browsing history) is read once, before the agent starts,
    and kept in the graph state for the rest of the turn. Only the registry part is rebuilt per
    model call, from memory.
    """

    state_schema = CopilotState

    def __init__(self, ctx: ToolContext, session_id: str | None):
        super().__init__()
        self._ctx = ctx
        self._session_id = session_id

    def before_agent(self, state, runtime):  # type: ignore[override]
        return {"turn_context": build_static_context(self._ctx, self._session_id)}

    async def abefore_agent(self, state, runtime):  # type: ignore[override]
        return {"turn_context": await asyncio.to_thread(build_static_context, self._ctx, self._session_id)}

    def _with_context(self, request):
        static = request.state.get("turn_context")
        if static is None:  # the hook did not run (middleware used on its own)
            static = build_static_context(self._ctx, self._session_id)
        block = compose_context(static, build_registry_block(self._ctx.registry))
        if not block:
            return request
        return request.override(
            system_prompt=f"{request.system_prompt or ADMIN_SYSTEM_PROMPT}\n\n<store_context>\n{block}\n</store_context>"
        )

    def wrap_model_call(self, request, handler):  # type: ignore[override]
        return handler(self._with_context(request))

    async def awrap_model_call(self, request, handler):  # type: ignore[override]
        return await handler(self._with_context(request))


# Token budget for the rolling history kept in the prompt (design §2: trim by
# token budget, not a fixed turn count).
HISTORY_TOKEN_BUDGET = 2000


class TrimHistoryMiddleware(AgentMiddleware):
    """Trim the message history to a token budget before each model call.

    Only meaningful when a checkpointer restores long histories; harmless
    otherwise. Uses a cheap char/4 token estimate so no tokenizer is required.
    """

    def _trim(self, request):
        try:
            from langchain_core.messages import trim_messages

            def _len(msgs):
                return sum(len(str(getattr(m, "content", ""))) for m in msgs) // 4

            trimmed = trim_messages(
                request.messages,
                max_tokens=HISTORY_TOKEN_BUDGET,
                token_counter=_len,
                strategy="last",
                include_system=False,
                allow_partial=False,
                start_on="human",
            )
            if trimmed and len(trimmed) < len(request.messages):
                return request.override(messages=trimmed)
        except Exception:
            pass
        return request

    def wrap_model_call(self, request, handler):  # type: ignore[override]
        return handler(self._trim(request))

    async def awrap_model_call(self, request, handler):  # type: ignore[override]
        return await handler(self._trim(request))
