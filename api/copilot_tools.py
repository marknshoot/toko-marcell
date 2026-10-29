"""Copilot v2 tool layer (design §5).

LangChain ``@tool``-decorated functions the agent binds. They wrap the
deterministic logic in ``agent_tools.py`` / ``sizing.py`` and add:

  - a per-request **context** (``ToolContext``) holding the shown-products
    registry, the pinned/page ASIN, and the DB url, so tools can resolve
    "ref"/registry references and record what they showed;
  - ``build_outfit`` whose total is summed **in code** and verified ``<= budget``;
  - ``add_to_cart`` which returns a server-validated ``ui_action`` (never trusts
    a client price).

The chat image tool (``search_by_image``) is deliberately **absent** — the v2
copilot is text-only (design §4).

The tools are built per-request by ``build_tools(ctx)`` so each closes over the
right ``ToolContext``.
"""

import logging
from dataclasses import dataclass, field

import agent_tools
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

MAX_REGISTRY = 10

# Ordinal words shoppers use instead of "nomor 2" (Indonesian + English).
_ORDINAL_REFS: dict[str, int] = {
    "pertama": 1,
    "first": 1,
    "kedua": 2,
    "second": 2,
    "ketiga": 3,
    "third": 3,
    "keempat": 4,
    "fourth": 4,
    "kelima": 5,
    "fifth": 5,
    "keenam": 6,
    "sixth": 6,
}


@dataclass
class ToolContext:
    """Per-request state shared across tool calls within one turn."""

    db_url: str
    page_asin: str | None = None
    referenced_asins: list[str] = field(default_factory=list)
    # Set by flag_unanswered when the tool evidence cannot answer honestly.
    unanswered: bool = False
    unanswered_question: str | None = None
    # registry: ref(int) -> product dict (title, asin, priceIdr, ...)
    registry: dict[int, dict] = field(default_factory=dict)
    # refs shown during the *current* turn (what the response cards should show)
    _turn_refs: set[int] = field(default_factory=set)
    # ui_actions accumulated by tools (e.g. add_to_cart)
    ui_actions: list[dict] = field(default_factory=list)
    _next_ref: int = 1

    def register(self, product: dict, *, from_current_turn: bool = True) -> int:
        """Add a product to the registry (if not already present) and return its ref.

        ``from_current_turn=False`` is used when seeding the registry from a
        previous turn's checkpoint: the ref stays resolvable, but it is not
        reported back as a product card for *this* turn.
        """
        asin = product.get("asin")
        for ref, p in self.registry.items():
            if p.get("asin") == asin:
                if from_current_turn:
                    self._turn_refs.add(ref)
                return ref
        if len(self.registry) >= MAX_REGISTRY:
            # drop the lowest ref to keep the window bounded
            oldest = min(self.registry)
            del self.registry[oldest]
            self._turn_refs.discard(oldest)
        ref = self._next_ref
        self._next_ref += 1
        self.registry[ref] = product
        if from_current_turn:
            self._turn_refs.add(ref)
        return ref

    def turn_products(self, limit: int = 6) -> list[dict]:
        """Products shown during this turn (not merely rehydrated from history)."""
        return [
            {"ref": ref, **self.registry[ref]}
            for ref in sorted(self._turn_refs)
            if ref in self.registry
        ][:limit]

    def resolve_ref(self, token: str | int) -> str | None:
        """Resolve a 'ref' number, ASIN, or page/pinned reference to an ASIN."""
        if token is None:
            return None
        # already an ASIN present in the registry or looks like one
        if isinstance(token, str):
            t = token.strip().lower()
            if t in ("ini", "produk ini", "this", "yang ini") and self.page_asin:
                return self.page_asin
            # numeric ref like "2" / "nomor 2"
            import re
            m = re.search(r"\d+", t)
            if m:
                ref = int(m.group())
                if ref in self.registry:
                    return self.registry[ref]["asin"]
            else:
                # ordinal word like "yang kedua" / "the second one" / "pertama"
                for word, word_ref in _ORDINAL_REFS.items():
                    if re.search(rf"\b{re.escape(word)}\b", t) and word_ref in self.registry:
                        return self.registry[word_ref]["asin"]
        if isinstance(token, int) and token in self.registry:
            return self.registry[token]["asin"]
        # fall through: treat as literal ASIN/id
        return str(token)


def _slim(product: dict) -> dict:
    """Compact product projection for registry + response cards."""
    return {
        "id": product.get("id"),
        "asin": product.get("asin"),
        "title": product.get("title", ""),
        "priceIdr": product.get("priceIdr", 0),
        "brand": product.get("brand"),
        "imageUrl": product.get("imageUrl"),
        "avgRating": product.get("avgRating"),
        "ratingCount": product.get("ratingCount", 0),
        "category": product.get("category", ""),
        "department": product.get("department", ""),
    }


# Real catalog category enum is injected into the tool description at build time
# (design §5: category = enum of real catalog categories).

def build_tools(ctx: ToolContext, category_enum: list[str] | None = None):
    """Construct the per-request LangChain tools bound to *ctx*."""

    cat_hint = ""
    if category_enum:
        cat_hint = " Allowed categories: " + ", ".join(category_enum[:40]) + "."

    _search_desc = (
        "Search the Toko Marcell catalog (hybrid BM25 + multilingual ID/EN dense vector). "
        "Include English catalog terms (or bilingual ID+EN terms) so both lexical BM25 and "
        "multilingual vector retrieval match. Use for 'do you have…', style, outfit, and budget queries."
        + cat_hint
    )

    @tool(description=_search_desc)
    def search_catalog(
        query: str,
        category: str | None = None,
        department: str | None = None,
        price_max: int | None = None,
        limit: int = 4,
    ) -> list[dict]:
        results = agent_tools.search_catalog(
            query=query, category=category, department=department,
            price_max=price_max, limit=limit, db_url=ctx.db_url,
        )
        out = []
        for p in results:
            ref = ctx.register(_slim(p))
            out.append({"ref": ref, **_slim(p)})
        return out

    @tool
    def get_product_details(ref_or_asin_or_title: str) -> dict:
        """Get full specs + exact IDR price for a product. Accepts a registry
        'ref' number ("2", "nomor 2"), "ini"/"produk ini" (the page product), an
        ASIN/id, or a title/brand fragment. Returns the matched title for
        confirmation."""
        token = ref_or_asin_or_title
        asin = ctx.resolve_ref(token)
        res = agent_tools.get_product_details(asin, db_url=ctx.db_url)
        if isinstance(res, dict) and "error" in res and token and not str(token).isdigit():
            # title/brand fallback: BM25 top-1
            hits = agent_tools.search_catalog(query=str(token), limit=1, db_url=ctx.db_url)
            if hits:
                res = agent_tools.get_product_details(hits[0]["asin"], db_url=ctx.db_url)
        if isinstance(res, dict) and res.get("asin"):
            ref = ctx.register(_slim(res))
            res["ref"] = ref
        return res

    @tool
    def recommend_size(
        height_cm: float | None = None,
        weight_kg: float | None = None,
        foot_length_cm: float | None = None,
        waist_cm: float | None = None,
        head_circumference_cm: float | None = None,
        ref_or_asin: str | None = None,
        category: str | None = None,
        brand: str | None = None,
    ) -> dict:
        """Suggest a size using the measurement the category actually needs:
        height (TB, cm) + weight (BB, kg) for tops/bottoms/dress, foot length
        (cm) for shoes, waist (cm) for belts, or head circumference (cm) for
        hats. Pass a ref/ASIN to tie it to a specific product. Always advisory,
        never a guarantee. Unsupported categories return applicable=false
        instead of a guessed S/M/L."""
        asin = ctx.resolve_ref(ref_or_asin) if ref_or_asin else None
        return agent_tools.recommend_size(
            height_cm=height_cm,
            weight_kg=weight_kg,
            foot_length_cm=foot_length_cm,
            waist_cm=waist_cm,
            head_circumference_cm=head_circumference_cm,
            asin=asin,
            category=category,
            brand=brand,
            db_url=ctx.db_url,
        )

    @tool
    def build_outfit(query: str, budget_idr: int, department: str | None = None) -> dict:
        """Assemble a top + bottom + shoes outfit whose TOTAL is <= budget_idr.
        The total is computed and verified in code. Write the query in English
        catalog vocabulary (e.g. 'smart casual wedding guest')."""
        return _build_outfit_impl(ctx, query, budget_idr, department)

    @tool
    def lookup_store_policy(query: str) -> list[dict]:
        """Answer store policy questions: shipping, 7-day returns, QRIS demo, and
        the master size chart. Returns authored policy chunks."""
        return agent_tools.lookup_store_policy(query=query, limit=3, db_url=ctx.db_url)

    @tool
    def add_to_cart(ref_or_asin: str, qty: int = 1, size: str | None = None) -> dict:
        """Add a product to the shopper's cart. The server re-reads the price from
        the DB (never trusts a client price) and returns a ui_action the browser
        executes. Confirm the product with the shopper first."""
        asin = ctx.resolve_ref(ref_or_asin)
        details = agent_tools.get_product_details(asin, db_url=ctx.db_url)
        if not isinstance(details, dict) or not details.get("asin"):
            return {"error": f"Produk '{ref_or_asin}' tidak ditemukan, jadi belum bisa masuk keranjang."}
        q = max(1, min(int(qty or 1), 99))
        action = {
            "type": "add_to_cart",
            "id": details.get("id"),
            "asin": details["asin"],
            "qty": q,
            "size": size,
            "title": details.get("title"),
            "priceIdr": details.get("priceIdr"),
            "imageUrl": details.get("imageUrl"),
        }
        ctx.ui_actions.append(action)
        return {"ok": True, "ui_action": action}

    @tool
    def flag_unanswered(question: str, reason: str) -> dict:
        """Call this when the available tool evidence cannot answer the shopper's
        question. `question` is the shopper's question as asked; `reason` is a
        short internal note. The turn is logged as unanswered, and the reply must
        say you are unsure and suggest rephrasing or checking the product page or
        store policy. Never guess to fill the gap."""
        ctx.unanswered = True
        cleaned = (question or "").strip()
        ctx.unanswered_question = cleaned[:300] or None
        logger.info("[copilot] unanswered turn flagged: %s", (reason or "").strip()[:160])
        return {"ok": True}

    return [
        search_catalog,
        get_product_details,
        recommend_size,
        build_outfit,
        lookup_store_policy,
        add_to_cart,
        flag_unanswered,
    ]


def _build_outfit_impl(ctx: ToolContext, query: str, budget_idr: int, department: str | None) -> dict:
    """Greedy top+bottom+shoes under budget, total summed & verified in code."""
    slots = [
        ("top", f"{query} top shirt"),
        ("bottom", f"{query} pants trousers"),
        ("shoes", f"{query} shoes sneakers"),
    ]
    per_slot_budget = max(1, budget_idr // 3)
    chosen: dict[str, dict] = {}
    total = 0
    for slot, q in slots:
        remaining = budget_idr - total
        cap = min(per_slot_budget * 2, remaining)
        hits = agent_tools.search_catalog(
            query=q, department=department, price_max=cap, limit=4, db_url=ctx.db_url,
        )
        pick = None
        for h in hits:
            if total + (h.get("priceIdr") or 0) <= budget_idr:
                pick = h
                break
        if pick:
            ref = ctx.register(_slim(pick))
            chosen[slot] = {"ref": ref, **_slim(pick)}
            total += pick.get("priceIdr") or 0

    within = total <= budget_idr and len(chosen) >= 2
    return {
        "within_budget": within,
        "total_idr": total,
        "budget_idr": budget_idr,
        "items": chosen,
        "note": (
            f"Total Rp {total:,} (budget Rp {budget_idr:,})." if within
            else "Belum ketemu kombinasi lengkap di dalam budget — coba naikkan budget sedikit kak."
        ),
    }
