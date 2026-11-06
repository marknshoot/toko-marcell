"""Human approval before a cart add.

The graph pauses after the model asks for ``add_to_cart`` and before the tool runs. The shopper is
shown the item the *server* resolved (title and price read from the database, never from the
model or the browser) and answers yes or no through ``POST /copilot/chat/resume``.

Design notes:

* The pause is ``HumanInTheLoopMiddleware``'s. The question shown to the shopper is stored in the
  pending request itself, so the resume endpoint reads it from the saved thread and trusts nothing
  the browser sends except the yes/no.
* "Yes" is applied as an *edit* of the exact call (resolved ASIN, clamped quantity, size), so what
  the shopper saw is what runs even if the registry is gone by the time the answer arrives.
* A new message on a paused thread abandons the request. LangGraph drops the pause but leaves the
  model's tool call in the history without a result, which a provider would reject, so that
  message is replaced by plain text at the start of the next turn.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import agent_tools
from copilot_tools import ToolContext
from langchain.agents.middleware import AgentMiddleware, HumanInTheLoopMiddleware
from langchain_core.messages import AIMessage, ToolMessage

logger = logging.getLogger(__name__)

CART_TOOL = "add_to_cart"
REJECTED_BY_SHOPPER = "Pembeli membatalkan penambahan ke keranjang. Jangan coba lagi kecuali diminta."
CONFIRM_REPLY = "Mohon konfirmasi dulu ya kak:"
ABANDONED_NOTE = "(Permintaan menambahkan ke keranjang dibatalkan.)"


class NoPendingApproval(Exception):  # noqa: N818 - reads better than "...Error" at call sites
    """The thread has no cart approval waiting for an answer."""

    def __init__(self, thread_exists: bool):
        super().__init__("no pending approval")
        self.thread_exists = thread_exists


class CartApproval:
    """Resolves the item a pending ``add_to_cart`` call refers to, once per call.

    Resolution can depend on the request ("produk ini" means the page product the shopper is
    viewing), but a paused node is re-run from its start when the answer arrives, in a request
    that has no such context. So the answer is computed before the pause, saved in the thread
    state (``turn_carry["approval_items"]``) and read back from there on every later run.
    """

    def __init__(self, ctx: ToolContext):
        self._ctx = ctx
        self._items: dict[str, dict[str, Any] | None] = {}

    @staticmethod
    def _call_key(tool_call: dict[str, Any]) -> str:
        return str(tool_call.get("id") or id(tool_call))

    @staticmethod
    def _saved(state: Any) -> dict[str, Any]:
        carry = (state or {}).get("turn_carry") or {}
        return carry.get("approval_items") or {}

    def item_for(self, tool_call: dict[str, Any], state: Any = None) -> dict[str, Any] | None:
        key = self._call_key(tool_call)
        saved = self._saved(state)
        if key in saved:
            return saved[key]
        if key not in self._items:
            self._items[key] = self._resolve(tool_call.get("args") or {})
        return self._items[key]

    def items_for_message(self, message: Any) -> dict[str, dict[str, Any] | None]:
        """Resolve every cart call of a model message, keyed by tool-call id (JSON-serialisable)."""
        return {
            self._call_key(call): self.item_for(call)
            for call in getattr(message, "tool_calls", None) or []
            if call.get("name") == CART_TOOL
        }

    def _resolve(self, args: dict[str, Any]) -> dict[str, Any] | None:
        token = args.get("ref_or_asin")
        asin = self._ctx.resolve_ref(token) if token is not None else None
        if not asin:
            return None
        details = agent_tools.get_product_details(asin, db_url=self._ctx.db_url)
        if not isinstance(details, dict) or not details.get("asin"):
            return None
        try:
            qty = max(1, min(int(args.get("qty") or 1), 99))
        except (TypeError, ValueError):
            qty = 1
        return {
            "asin": details["asin"],
            "title": details.get("title"),
            "priceIdr": details.get("priceIdr"),
            "imageUrl": details.get("imageUrl"),
            "qty": qty,
            "size": args.get("size"),
        }

    # HumanInTheLoopMiddleware callbacks ------------------------------------------------------

    def when(self, request: Any) -> bool:
        """Pause only when the product exists. Otherwise the tool runs and reports "not found"."""
        return self.item_for(request.tool_call, request.state) is not None

    def describe(self, tool_call: dict[str, Any], state: Any, runtime: Any) -> str:  # noqa: ARG002
        return json.dumps(self.item_for(tool_call, state), ensure_ascii=False)


def cart_approval_middleware(ctx: ToolContext) -> tuple[HumanInTheLoopMiddleware, CartApproval]:
    approval = CartApproval(ctx)
    return HumanInTheLoopMiddleware(
        interrupt_on={
            CART_TOOL: {
                # "edit" is only ever sent by the server (see approval_decisions); the browser can
                # only say yes or no.
                "allowed_decisions": ["approve", "edit", "reject"],
                "description": approval.describe,
                "when": approval.when,
            }
        }
    ), approval


# ── Reading and answering a pending request ──────────────────────────────────

def pending_request(snapshot: Any) -> dict[str, Any] | None:
    """The pending approval request of a ``get_state`` snapshot, if the thread is paused."""
    for task in getattr(snapshot, "tasks", None) or []:
        for interrupt in getattr(task, "interrupts", None) or []:
            value = getattr(interrupt, "value", None)
            if isinstance(value, dict) and value.get("action_requests"):
                return value
    return None


def approval_from_result(result: dict[str, Any]) -> list[dict[str, Any]] | None:
    """The items awaiting approval if this graph result is a pause, else ``None``."""
    for interrupt in result.get("__interrupt__") or []:
        value = getattr(interrupt, "value", None)
        if isinstance(value, dict) and value.get("action_requests"):
            return pending_items(value)
    return None


def pending_items(request: dict[str, Any]) -> list[dict[str, Any]]:
    """The items the shopper is asked about, as stored in the pending request.

    The same product asked for twice (same ASIN and size) is shown once, with the quantities
    added up (at most 99), in the order each product first appeared. This only changes what the
    card lists: the request still holds one action per call, ``approval_decisions`` still answers
    each of them, and the cart receives the same total the card shows.
    """
    rows: dict[tuple[Any, Any], dict[str, Any]] = {}
    for action in request.get("action_requests", []):
        try:
            item = json.loads(action.get("description") or "null")
        except ValueError:
            item = None
        if not isinstance(item, dict):
            continue
        key = (item.get("asin"), item.get("size"))
        if key in rows:
            total = int(rows[key].get("qty") or 1) + int(item.get("qty") or 1)
            rows[key] = {**rows[key], "qty": min(total, 99)}
        else:
            rows[key] = dict(item)
    return list(rows.values())


def approval_decisions(request: dict[str, Any], *, approve: bool) -> list[dict[str, Any]]:
    """One decision per pending call, in order."""
    decisions: list[dict[str, Any]] = []
    for action in request.get("action_requests", []):
        if not approve:
            decisions.append({"type": "reject", "message": REJECTED_BY_SHOPPER})
            continue
        try:
            item = json.loads(action.get("description") or "null") or {}
        except ValueError:
            item = {}
        args = {**(action.get("args") or {})}
        # Run exactly what was shown: the server-resolved ASIN, quantity and size.
        args.update({"ref_or_asin": item.get("asin"), "qty": item.get("qty", 1), "size": item.get("size")})
        decisions.append({"type": "edit", "edited_action": {"name": action["name"], "args": args}})
    return decisions


# ── Abandoned requests ───────────────────────────────────────────────────────

def _dangling_tool_calls(messages: list[Any]) -> list[AIMessage]:
    """Model messages whose tool calls never got a result, excluding the newest message."""
    answered = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
    dangling: list[AIMessage] = []
    for message in messages[:-1]:
        if isinstance(message, AIMessage) and message.tool_calls:
            if any(call.get("id") not in answered for call in message.tool_calls):
                dangling.append(message)
    return dangling


class AbandonedApprovalMiddleware(AgentMiddleware):
    """Drop a tool call that was waiting for an answer the shopper never gave.

    Runs at the start of every turn that reaches the agent. If the previous turn paused for
    approval and the shopper typed something else instead, the unanswered tool call is replaced by
    a plain assistant note (same message id, so the reducer swaps it in place). The history stays
    valid for the model provider and keeps alternating user and assistant roles.
    """

    def __init__(self, thread_id: str | None):
        super().__init__()
        self._thread_id = thread_id

    def _cleanup(self, state: dict[str, Any]) -> dict[str, Any] | None:
        dangling = [m for m in _dangling_tool_calls(state.get("messages", [])) if m.id]
        if not dangling:
            return None
        logger.warning(
            "copilot.approval.abandoned",
            extra={
                "thread_id": self._thread_id,
                "asins": sorted(
                    {str(c.get("args", {}).get("ref_or_asin")) for m in dangling for c in m.tool_calls}
                ),
            },
        )
        return {"messages": [AIMessage(content=ABANDONED_NOTE, id=m.id) for m in dangling], "turn_carry": None}

    def before_agent(self, state, runtime):  # type: ignore[override]
        return self._cleanup(state)

    async def abefore_agent(self, state, runtime):  # type: ignore[override]
        return self._cleanup(state)
