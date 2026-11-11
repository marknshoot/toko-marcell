"""The authoritative ``<store_context>`` block injected before each model call.

Holds the page/pinned products (re-read from the DB), the shown-products registry
("[1] …") that makes follow-ups like "yang kedua" resolvable, and recent browsing
from the events table.  Also owns the input sanitizer: shopper text must never be
able to forge one of these headers.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import catalog_repo
from copilot_tools import ToolContext, slim_product
from db import connect as _connect
from langchain_core.messages import ToolMessage

logger = logging.getLogger(__name__)

_REGISTRY_HEADER = "[Produk dalam percakapan ini]"

_RESERVED_CONTEXT_RE = re.compile(
    r"(?:\[\s*(?:Produk dalam percakapan ini|Produk yang sedang dilihat|Produk yang dirujuk|Riwayat jelajah terakhir)\s*\]|</?store_context>)",
    re.IGNORECASE,
)


def sanitize_user_message(message: str) -> str:
    """Strip reserved context headers/tags so user input cannot spoof system context."""
    if not message:
        return ""
    return _RESERVED_CONTEXT_RE.sub("", message).strip()


def _iter_product_dicts(value: Any):
    """Yield product-shaped dicts (asin + title) from a tool result of any shape.

    Handles the list returned by ``search_catalog``, the dict returned by
    ``get_product_details``, and the ``items`` dict returned by ``build_outfit``
    (also when the tool message content is a JSON string).
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return
    if isinstance(value, dict):
        if value.get("asin") and value.get("title"):
            yield value
            return
        for nested in value.values():
            yield from _iter_product_dicts(nested)
    elif isinstance(value, list | tuple):
        for nested in value:
            yield from _iter_product_dicts(nested)


def _seed_registry_from_checkpoint(
    ctx: ToolContext,
    checkpointer: Any,
    thread_id: str | None,
) -> None:
    """Re-register products shown in earlier turns so their refs stay resolvable.

    The checkpointer restores the conversation for the model, but the per-request
    ``ToolContext`` starts empty — so "nomor 2" / "yang kedua" would fail on a
    follow-up turn. We replay the prior ``ToolMessage`` results into the
    registry without marking them as part of the current turn.
    """
    if checkpointer is None or not thread_id:
        return
    try:
        state = checkpointer.get_tuple({"configurable": {"thread_id": thread_id}})
    except Exception as e:
        logger.info("[copilot] checkpoint seed skipped: %s", e)
        return
    if not state:
        return

    values = (getattr(state, "checkpoint", None) or {}).get("channel_values") or {}
    for msg in values.get("messages") or []:
        if not isinstance(msg, ToolMessage):
            continue
        for item in _iter_product_dicts(getattr(msg, "content", None)):
            ctx.register(slim_product(item), from_current_turn=False)


def _read_product_block(cur, asin: str, label: str) -> str | None:
    product = catalog_repo.fetch_brief(cur, asin)
    if product is None:
        return None
    return (
        f"{label}: {product['title']} — {product['brand'] or ''} (ASIN {product['asin']}, Rp {product['priceIdr']:,})"
    )


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


def build_static_context(ctx: ToolContext, session_id: str | None) -> str:
    """The part of the context block that needs the database: the viewed page product, the
    products the shopper attached, and recent browsing. Built once per turn."""
    parts: list[str] = []
    if ctx.page_asin or ctx.referenced_asins or session_id:
        with _connect(ctx.db_url) as conn:
            with conn.cursor() as cur:
                if ctx.page_asin:
                    blk = _read_product_block(cur, ctx.page_asin, "[Produk yang sedang dilihat]")
                    if blk:
                        parts.append(blk)
                for a in ctx.referenced_asins[:3]:
                    blk = _read_product_block(cur, a, "[Produk yang dirujuk]")
                    if blk:
                        parts.append(blk)
                browse = _browsing_context(cur, session_id)
                if browse:
                    parts.append(browse)
    return "\n\n".join(parts)


def build_registry_block(registry: dict[int, dict]) -> str:
    """The shown-products list ("[1] ... [2] ..."). No database access, so it can be rebuilt on
    every model call: a tool may add products between two calls in the same turn."""
    if not registry:
        return ""
    reg_lines = [_REGISTRY_HEADER]
    for ref in sorted(registry):
        p = registry[ref]
        reg_lines.append(
            f"[{ref}] {p.get('title', '')} — {p.get('brand') or ''} (ASIN {p.get('asin')}, Rp {p.get('priceIdr', 0):,})"
        )
    return "\n".join(reg_lines)


def compose_context(static: str, registry_block: str) -> str:
    return "\n\n".join(part for part in (static, registry_block) if part)


def build_context_block(ctx: ToolContext, session_id: str | None) -> str:
    """Assemble the authoritative context string injected before the model call."""
    return compose_context(build_static_context(ctx, session_id), build_registry_block(ctx.registry))
