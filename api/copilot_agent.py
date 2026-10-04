"""Copilot v2 agent (design §1).

LangChain v1 ``create_agent`` (runs on LangGraph) with:

  - a deterministic pre-agent guardrail (``guardrail.classify``) that short-
    circuits greetings / off-topic / injection with ZERO LLM calls;
  - the v2 tool layer (``copilot_tools.build_tools``);
  - a context-injection middleware that, before each model call, prepends an
    authoritative block: the pinned/page product (re-read from DB), the
    shown-products registry ("[Produk dalam percakapan ini] [1] …"), and recent
    browsing context from the events table;
  - a tool-loop cap (``ModelCallLimitMiddleware``) so a turn can't spin forever.

The agent is built per-request because the tools close over a per-request
``ToolContext`` (registry, page/pinned ASIN, DB url). The checkpointer (memory)
is wired in a later slice; here the agent runs statelessly per call and the
router passes recent history explicitly.
"""

import logging
import time
from typing import Any

import psycopg
from copilot_llm import build_chat_model, llm_available
from copilot_tools import ToolContext, build_tools
from guardrail import classify
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, ModelCallLimitMiddleware
from langchain_core.messages import AIMessage, HumanMessage

logger = logging.getLogger(__name__)

# Max model→tool iterations in a single turn (design §1).
MAX_MODEL_CALLS = 3


ADMIN_SYSTEM_PROMPT = """You are Admin Toko Marcell, an expert in-store stylist and customer-service admin for Toko Marcell (Jakarta, Indonesia).

VOICE
- Warm, polite, consultative. Mirror the shopper's language: reply in Indonesian to Indonesian, English to English. Indonesian e-commerce tone ("Halo kak!", "mimin").
- Enthusiastically highlight craftsmanship, durability, and value in Rupiah. Never badmouth a product. Gently guide toward styling and checkout.

TOOLS (call them — don't guess)
- Product availability / style / outfit / budget → search_catalog.
- Fabric, specs, exact price, comparisons → get_product_details.
- Social proof / sizing reality / durability → get_product_reviews (topic: fit/size/fabric/durability).
- Size advice from TB/BB → recommend_size (it layers store chart + brand cut + review fit; it is advisory, never a guarantee).
- Full outfit under a budget → build_outfit (the total is verified in code to be within budget).
- Shipping / returns / QRIS demo / size chart → lookup_store_policy.
- Shopper confirms they want an item → add_to_cart (server validates price; it returns a cart action).

REFERENCE RESOLUTION
- The context block lists the products shown so far as "[1] …", "[2] …" and the product the shopper is viewing. Resolve "yang kedua / nomor 2 / yang Dickies / itu / produk ini" to the matching ASIN from that block or the page product. If genuinely ambiguous, ask ONE short clarifying question.

SEARCH LANGUAGE
- The catalog and embeddings are English. Write tool queries in ENGLISH catalog vocabulary even when the shopper writes Indonesian (e.g. "jaket hujan" → "waterproof rain jacket"). Reply in the shopper's language.

GROUNDING
- Every product you mention MUST come from a tool result. Never invent brands, ASINs, stock, discounts, or prices. Prices always in Indonesian Rupiah from the DB.
- Checkout is a portfolio demo: QRIS is simulated, there is NO shipping/tracking. Never promise a tracking number.
- Sizing: present the store chart as "panduan umum toko" (not an official brand chart); fold in brand cut rules and the product's review fit signal. Never state a size as a certainty.
"""

_REGISTRY_HEADER = "[Produk dalam percakapan ini]"


def _read_product_block(cur, asin: str, label: str) -> str | None:
    cur.execute(
        "SELECT asin, title, brand, price_idr FROM products WHERE asin = %s",
        (asin,),
    )
    row = cur.fetchone()
    if not row:
        return None
    return f"{label}: {row[1]} — {row[2] or ''} (ASIN {row[0]}, Rp {row[3]:,})"


def _browsing_context(cur, session_id: str | None) -> str:
    if not session_id:
        return ""
    try:
        cur.execute(
            """
            SELECT e.event_type, p.title, p.asin, p.price_idr
            FROM events e LEFT JOIN products p ON e.asin = p.asin
            WHERE e.session_id = %s AND e.event_type IN ('view_product', 'add_to_cart')
            ORDER BY e.id DESC LIMIT 3
            """,
            (session_id,),
        )
        rows = cur.fetchall()
    except Exception:
        return ""
    if not rows:
        return ""
    lines = ["[Riwayat jelajah terakhir]"]
    for r in rows:
        if r[1]:
            lines.append(f"- {r[0]}: {r[1]} (ASIN {r[2]}, Rp {r[3]:,})")
    return "\n".join(lines)


def build_context_block(ctx: ToolContext, session_id: str | None) -> str:
    """Assemble the authoritative context string injected before the model call."""
    parts: list[str] = []
    with psycopg.connect(ctx.db_url) as conn:
        with conn.cursor() as cur:
            if ctx.page_asin:
                blk = _read_product_block(cur, ctx.page_asin, "[Produk yang sedang dilihat]")
                if blk:
                    parts.append(blk)
            for a in ctx.referenced_asins[:3]:
                blk = _read_product_block(cur, a, "[Produk yang dirujuk]")
                if blk:
                    parts.append(blk)
            if ctx.registry:
                reg_lines = [_REGISTRY_HEADER]
                for ref in sorted(ctx.registry):
                    p = ctx.registry[ref]
                    reg_lines.append(f"[{ref}] {p.get('title','')} — {p.get('brand') or ''} (ASIN {p.get('asin')}, Rp {p.get('priceIdr',0):,})")
                parts.append("\n".join(reg_lines))
            browse = _browsing_context(cur, session_id)
            if browse:
                parts.append(browse)
    return "\n\n".join(parts)


class ContextInjectionMiddleware(AgentMiddleware):
    """Prepend the authoritative context block to the system prompt each model call."""

    def __init__(self, ctx: ToolContext, session_id: str | None):
        super().__init__()
        self._ctx = ctx
        self._session_id = session_id

    def wrap_model_call(self, request, handler):  # type: ignore[override]
        block = build_context_block(self._ctx, self._session_id)
        if block:
            request = request.override(
                system_prompt=f"{request.system_prompt or ADMIN_SYSTEM_PROMPT}\n\n{block}"
            )
        return handler(request)

    async def awrap_model_call(self, request, handler):  # type: ignore[override]
        block = build_context_block(self._ctx, self._session_id)
        if block:
            request = request.override(
                system_prompt=f"{request.system_prompt or ADMIN_SYSTEM_PROMPT}\n\n{block}"
            )
        return await handler(request)


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


def _catalog_categories(db_url: str) -> list[str]:
    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT DISTINCT category FROM products WHERE category <> '' ORDER BY category")
                return [r[0] for r in cur.fetchall()]
    except Exception:
        return []


def build_agent(ctx: ToolContext, session_id: str | None, *, fake: bool | None = None, checkpointer=None):
    """Construct a per-request compiled agent."""
    model = build_chat_model(temperature=0.2, fake=fake)
    if model is None:
        return None
    tools = build_tools(ctx, _catalog_categories(ctx.db_url))
    middleware = [
        ContextInjectionMiddleware(ctx, session_id),
        TrimHistoryMiddleware(),
        ModelCallLimitMiddleware(run_limit=MAX_MODEL_CALLS, exit_behavior="end"),
    ]
    return create_agent(
        model,
        tools,
        system_prompt=ADMIN_SYSTEM_PROMPT,
        middleware=middleware,
        checkpointer=checkpointer,
    )


def _extract_reply(result: dict[str, Any]) -> str:
    msgs = result.get("messages", [])
    for m in reversed(msgs):
        if isinstance(m, AIMessage) and m.content:
            if isinstance(m.content, str):
                return m.content
            if isinstance(m.content, list):
                return "".join(p if isinstance(p, str) else p.get("text", "") for p in m.content)
    return ""


async def run_turn(
    message: str,
    *,
    db_url: str,
    session_id: str | None = None,
    page_asin: str | None = None,
    referenced_asins: list[str] | None = None,
    history: list[dict[str, str]] | None = None,
    fake: bool | None = None,
    checkpointer=None,
    thread_id: str | None = None,
) -> dict[str, Any]:
    """Run one copilot v2 turn.

    1. Deterministic guardrail → canned reply (zero LLM) if it fires.
    2. Otherwise invoke the create_agent agent with the tools + context middleware.
    """
    started = time.perf_counter()

    label, canned = classify(message)
    if label is not None:
        return {
            "reply": canned,
            "products": [],
            "citations": [],
            "suggestions": [],
            "ui_actions": [],
            "tool_calls": [],
            "guardrail": label,
            "took_ms": round((time.perf_counter() - started) * 1000, 2),
        }

    ctx = ToolContext(
        db_url=db_url,
        page_asin=page_asin,
        referenced_asins=referenced_asins or [],
    )
    agent = build_agent(ctx, session_id, fake=fake, checkpointer=checkpointer)
    if agent is None:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=503,
            detail="Layanan asisten AI sedang tidak tersedia sementara waktu. Silakan coba kembali nanti.",
        )

    # Build the input messages. With a checkpointer, prior turns are restored by
    # thread_id, so we pass only the new human message; otherwise we pass history.
    input_messages: list[Any] = []
    if checkpointer is None and history:
        for h in history[-6:]:
            if h["role"] == "user":
                input_messages.append(HumanMessage(content=h["content"]))
            elif h["role"] == "assistant":
                input_messages.append(AIMessage(content=h["content"]))
    input_messages.append(HumanMessage(content=message))

    invoke_cfg = {"configurable": {"thread_id": thread_id}} if (checkpointer and thread_id) else {}

    try:
        if checkpointer is not None:
            # The sync PostgresSaver doesn't implement async methods, so run the
            # synchronous graph in a worker thread to keep the route async.
            import asyncio
            result = await asyncio.to_thread(
                agent.invoke, {"messages": input_messages}, invoke_cfg
            )
        else:
            result = await agent.ainvoke({"messages": input_messages}, config=invoke_cfg)
    except Exception as e:
        logger.error("copilot agent error: %s", e)
        from fastapi import HTTPException
        raise HTTPException(
            status_code=502,
            detail="Asisten AI sedang mengalami kendala jaringan. Silakan coba beberapa saat lagi ya!",
        ) from None

    reply = _extract_reply(result) or "Maaf kak, mimin belum nemu jawabannya. Boleh ulangi ya?"

    # Record activity for TTL cleanup (design §2).
    if checkpointer is not None and thread_id:
        try:
            from copilot_memory import touch_thread
            touch_thread(thread_id, db_url=db_url)
        except Exception:
            pass

    # Collect tool_calls from the message trace.
    tool_calls = []
    for m in result.get("messages", []):
        for tc in getattr(m, "tool_calls", None) or []:
            tool_calls.append({"name": tc.get("name"), "args": tc.get("args") or {}})

    # Products come from the registry (what the tools showed), capped at 6.
    products = []
    for ref in sorted(ctx.registry):
        p = ctx.registry[ref]
        products.append({"ref": ref, **p})
    products = products[:6]

    took_ms = round((time.perf_counter() - started) * 1000, 2)
    return {
        "reply": reply,
        "products": products,
        "citations": [],
        "suggestions": _suggestions_from_tools(tool_calls),
        "ui_actions": ctx.ui_actions,
        "tool_calls": tool_calls,
        "guardrail": None,
        "took_ms": took_ms,
    }


def _suggestions_from_tools(tool_calls: list[dict]) -> list[dict]:
    """Deterministic follow-up chips based on which tools ran (design §UX 7)."""
    names = {tc["name"] for tc in tool_calls}
    out: list[dict] = []
    if "search_catalog" in names or "build_outfit" in names:
        out.append({"label": "Yang mirip tapi lebih murah", "prompt": "Ada yang mirip tapi lebih murah?"})
    if "get_product_details" in names or "search_catalog" in names:
        out.append({"label": "Cocok dipadukan dengan apa?", "prompt": "Ini cocok dipadukan dengan apa?"})
    if "recommend_size" not in names:
        out.append({"label": "Ukuran saya pas yang mana?", "prompt": "Ukuran saya pas yang mana? TB 170 BB 65"})
    return out[:3]


def is_llm_available() -> bool:
    return llm_available()
