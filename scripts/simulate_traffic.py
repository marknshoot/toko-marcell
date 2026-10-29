#!/usr/bin/env python3
"""Deterministic synthetic traffic simulator for the Toko Marcell demo.

The simulator writes realistic multi-step journeys into the same tables the API
serves: ``events``, ``orders``/``order_items``, ``copilot_turns`` and
``copilot_feedback``. It is deliberately honest about what it is:

* every session id starts with ``sim_sess_`` and every order with ``sim_ord_``;
* every copilot thread starts with ``sim_thr_``;
* all output is **synthetic**, not real customer traffic or revenue;
* ``--dry-run`` generates and validates the same data in memory, but never
  writes to Postgres.

Cohorts (defaults: 45% baseline, 25% sizing, 15% policy, 15% outfit) exercise
the four shopping jobs from the case study. Probabilities are seeded, so the
same seed produces the same counts, basket values and summary table.

Usage::

    python scripts/simulate_traffic.py --dry-run --sessions 100 --seed 42
    python scripts/simulate_traffic.py --reset-sim-only --sessions 300 --seed 42

``DATABASE_URL`` is used when ``--db-url`` is omitted. In dry-run mode the
catalog is read from Postgres when available and otherwise falls back to a
small embedded apparel catalog, so the command still works offline.
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

try:
    import psycopg
except ImportError:  # pragma: no cover - import guard for --help / unit tests
    psycopg = None

SESSION_PREFIX = "sim_sess_"
ORDER_PREFIX = "sim_ord_"
THREAD_PREFIX = "sim_thr_"
SIM_MODEL = "simulated-model"
COHORT_NAMES = ("baseline", "sizing", "policy", "outfit")
COHORT_LABELS = {
    "baseline": "Baseline / search-only",
    "sizing": "Copilot: sizing",
    "policy": "Copilot: policy",
    "outfit": "Copilot: outfit",
}
COHORT_WEIGHTS = (0.45, 0.25, 0.15, 0.15)
OUTFIT_BUDGETS = (750_000, 1_000_000, 1_250_000, 1_500_000, 2_000_000)

SEARCH_QUERIES = (
    "kemeja pria lengan panjang",
    "women's summer dress",
    "celana jeans slim fit",
    "running shoes men",
    "jaket anti air",
    "linen shirt oversized",
    "sepatu lari empuk",
    "black work pants",
    "batik couple set",
    "everyday sneakers",
)

POLICY_QUERIES = (
    "Berapa lama pengiriman ke Surabaya?",
    "Apa syarat retur dan tukar ukuran?",
    "Bisa bayar pakai QRIS?",
    "How long does shipping take to Jakarta?",
    "What is the return policy?",
    "Is QRIS payment available?",
)

OUTFIT_STYLES = (
    ("kantor", "office"),
    ("kasual akhir pekan", "weekend casual"),
    ("smart casual", "smart casual"),
    ("formal", "formal"),
)

OUTFIT_SLOTS = {
    "Men": (
        ("Shirts", "T-Shirts", "Polos", "Dress Shirts", "Casual Button-Down Shirts"),
        ("Pants", "Jeans", "Shorts", "Active Pants"),
        ("Shoes", "Fashion Sneakers", "Boots", "Loafers & Slip-Ons"),
    ),
    "Women": (
        ("Blouses & Button-Down Shirts", "Dresses", "Knits & Tees", "Tops", "Cardigans"),
        ("Pants", "Jeans", "Leggings", "Shorts", "Jumpsuits"),
        ("Shoes", "Fashion Sneakers", "Flats", "Heeled Sandals", "Boots"),
    ),
}

SIZING_CATEGORIES = {
    "tops": {
        "Shirts",
        "T-Shirts",
        "Polos",
        "Dress Shirts",
        "Blouses & Button-Down Shirts",
        "Knits & Tees",
        "Tops",
        "Sweaters",
        "Cardigans",
    },
    "bottoms": {"Pants", "Jeans", "Shorts", "Active Pants", "Leggings"},
    "footwear": {
        "Shoes",
        "Fashion Sneakers",
        "Boots",
        "Sandals",
        "Flats",
        "Loafers & Slip-Ons",
        "Heeled Sandals",
    },
}


@dataclass(frozen=True)
class Product:
    asin: str
    title: str
    brand: str
    price_idr: int
    department: str
    category: str


@dataclass(frozen=True)
class SimEvent:
    session_id: str
    event_type: str
    created_at: datetime
    asin: str | None = None
    query: str | None = None
    results_count: int | None = None
    qty: int | None = None
    price_idr: int | None = None


@dataclass(frozen=True)
class SimOrderItem:
    asin: str
    title: str
    qty: int
    unit_price_idr: int


@dataclass(frozen=True)
class SimOrder:
    token: str
    session_id: str
    total_idr: int
    item_count: int
    created_at: datetime
    items: tuple[SimOrderItem, ...]


@dataclass(frozen=True)
class SimTurn:
    thread_id: str
    session_id: str
    outcome: str
    tool_names: tuple[str, ...]
    latency_ms: float
    input_tokens: int
    output_tokens: int
    cost_usd: float
    created_at: datetime


@dataclass(frozen=True)
class SimFeedback:
    thread_id: str
    session_id: str
    rating: int
    created_at: datetime


@dataclass
class Simulation:
    seed: int
    sessions: int
    catalog: tuple[Product, ...]
    catalog_source: str
    session_cohorts: dict[str, str]
    events: list[SimEvent] = field(default_factory=list)
    orders: list[SimOrder] = field(default_factory=list)
    turns: list[SimTurn] = field(default_factory=list)
    feedback: list[SimFeedback] = field(default_factory=list)
    outfit_budgets: dict[str, int] = field(default_factory=dict)

    def stats(self) -> dict:
        """Return deterministic, display-ready aggregate metrics."""
        sessions_by_cohort = {name: 0 for name in COHORT_NAMES}
        for cohort in self.session_cohorts.values():
            sessions_by_cohort[cohort] += 1

        add_sessions: dict[str, set[str]] = {name: set() for name in COHORT_NAMES}
        for event in self.events:
            if event.event_type == "add_to_cart":
                cohort = self.session_cohorts.get(event.session_id)
                if cohort:
                    add_sessions[cohort].add(event.session_id)

        orders_by_cohort: dict[str, list[SimOrder]] = {name: [] for name in COHORT_NAMES}
        for order in self.orders:
            cohort = self.session_cohorts.get(order.session_id)
            if cohort:
                orders_by_cohort[cohort].append(order)

        policy_answered = {
            turn.session_id
            for turn in self.turns
            if self.session_cohorts.get(turn.session_id) == "policy"
            and turn.outcome in {"answered", "cart"}
        }

        cohorts = {}
        for name in COHORT_NAMES:
            cohort_sessions = sessions_by_cohort[name]
            cohort_orders = orders_by_cohort[name]
            order_sessions = {order.session_id for order in cohort_orders}
            revenue = sum(order.total_idr for order in cohort_orders)
            cohorts[name] = {
                "sessions": cohort_sessions,
                "add_to_cart_sessions": len(add_sessions[name]),
                "orders": len(cohort_orders),
                "order_sessions": len(order_sessions),
                "conversion_rate": _ratio(len(order_sessions), cohort_sessions),
                "add_to_cart_rate": _ratio(len(add_sessions[name]), cohort_sessions),
                "revenue_idr": revenue,
                "aov_idr": _ratio(revenue, len(cohort_orders)) if cohort_orders else None,
                "revenue_per_session_idr": _ratio(revenue, cohort_sessions),
            }

        assisted = [name for name in COHORT_NAMES if name != "baseline"]
        assisted_sessions = sum(sessions_by_cohort[name] for name in assisted)
        assisted_add_sessions = set().union(*(add_sessions[name] for name in assisted))
        assisted_orders = [order for name in assisted for order in orders_by_cohort[name]]
        assisted_revenue = sum(order.total_idr for order in assisted_orders)
        total_cost = sum(turn.cost_usd for turn in self.turns)
        outfit_orders = orders_by_cohort["outfit"]
        thread_outcomes: dict[str, set[str]] = {}
        for turn in self.turns:
            thread_outcomes.setdefault(turn.thread_id, set()).add(turn.outcome)
        conversations = len(thread_outcomes)
        resolved_conversations = sum(
            1
            for outcomes in thread_outcomes.values()
            if outcomes & {"answered", "cart"}
        )

        return {
            "sessions": self.sessions,
            "orders": len(self.orders),
            "revenue_idr": sum(order.total_idr for order in self.orders),
            "aov_idr": (
                _ratio(sum(order.total_idr for order in self.orders), len(self.orders))
                if self.orders
                else None
            ),
            "copilot_turns": len(self.turns),
            "copilot_threads": conversations,
            "resolved_conversations": resolved_conversations,
            "feedback": len(self.feedback),
            "thumbs_up": sum(1 for vote in self.feedback if vote.rating == 1),
            "thumbs_down": sum(1 for vote in self.feedback if vote.rating == -1),
            "total_cost_usd": round(total_cost, 8),
            "cost_per_turn_usd": round(total_cost / len(self.turns), 8) if self.turns else None,
            "cost_per_conversation_usd": (
                round(total_cost / conversations, 8) if conversations else None
            ),
            "cost_per_resolved_conversation_usd": (
                round(total_cost / resolved_conversations, 8)
                if resolved_conversations
                else None
            ),
            "cost_per_assisted_order_usd": (
                round(total_cost / len(assisted_orders), 8) if assisted_orders else None
            ),
            "avg_items_per_order": (
                round(
                    sum(order.item_count for order in self.orders) / len(self.orders),
                    2,
                )
                if self.orders
                else None
            ),
            "assisted_sessions": assisted_sessions,
            "assisted_add_to_cart_sessions": len(assisted_add_sessions),
            "assisted_add_to_cart_rate": _ratio(
                len(assisted_add_sessions), assisted_sessions
            ),
            "assisted_orders": len(assisted_orders),
            "assisted_revenue_idr": assisted_revenue,
            "cohorts": cohorts,
            "policy_sessions": sessions_by_cohort["policy"],
            "policy_answered_sessions": len(policy_answered),
            "policy_deflection_rate": _ratio(
                len(policy_answered), sessions_by_cohort["policy"]
            ),
            "outfit_orders": len(outfit_orders),
            "outfit_multi_item_orders": sum(
                1 for order in outfit_orders if order.item_count >= 2
            ),
            "outfit_budget_violations": sum(
                1
                for order in outfit_orders
                if order.total_idr > self.outfit_budgets.get(order.session_id, 0)
            ),
        }


# Small offline catalog used only when Postgres is unavailable in --dry-run.
FALLBACK_CATALOG = (
    Product("FB00000001", "Men's Cotton Oxford Shirt", "Demo", 249_000, "Men", "Shirts"),
    Product("FB00000002", "Men's Slim Fit Chinos", "Demo", 279_000, "Men", "Pants"),
    Product("FB00000003", "Men's Everyday Sneakers", "Demo", 399_000, "Men", "Shoes"),
    Product("FB00000004", "Men's Linen Blend Shirt", "Demo", 229_000, "Men", "Shirts"),
    Product("FB00000005", "Men's Straight Leg Jeans", "Demo", 349_000, "Men", "Jeans"),
    Product("FB00000006", "Men's Leather Loafers", "Demo", 489_000, "Men", "Loafers & Slip-Ons"),
    Product("FB00000007", "Men's Basic Crew T-Shirt", "Demo", 149_000, "Men", "T-Shirts"),
    Product("FB00000008", "Women's Midi Wrap Dress", "Demo", 329_000, "Women", "Dresses"),
    Product("FB00000009", "Women's High Waist Trousers", "Demo", 289_000, "Women", "Pants"),
    Product("FB00000010", "Women's Low Heel Sandals", "Demo", 359_000, "Women", "Heeled Sandals"),
    Product("FB00000011", "Women's Relaxed Linen Shirt", "Demo", 239_000, "Women", "Blouses & Button-Down Shirts"),
    Product("FB00000012", "Women's Straight Leg Jeans", "Demo", 339_000, "Women", "Jeans"),
    Product("FB00000013", "Women's Canvas Sneakers", "Demo", 299_000, "Women", "Fashion Sneakers"),
    Product("FB00000014", "Women's Knit Cardigan", "Demo", 219_000, "Women", "Cardigans"),
)


def _ratio(numerator: int | float, denominator: int | float) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _idr(value: int | float | None) -> str:
    return "n/a" if value is None else f"{int(value):,}"


def _usd(value: float | None) -> str:
    return "n/a" if value is None else f"${value:.6f}"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _cohort_counts(sessions: int) -> dict[str, int]:
    raw = [int(sessions * weight) for weight in COHORT_WEIGHTS]
    remainder = sessions - sum(raw)
    for index in range(remainder):
        raw[index % len(raw)] += 1
    return dict(zip(COHORT_NAMES, raw, strict=True))


def _cost_usd(input_tokens: int, output_tokens: int) -> float:
    input_price = float(os.getenv("LLM_INPUT_COST_PER_MILLION", "0.15"))
    output_price = float(os.getenv("LLM_OUTPUT_COST_PER_MILLION", "0.60"))
    return round(
        (input_tokens * input_price + output_tokens * output_price) / 1_000_000,
        8,
    )


def load_catalog(db_url: str) -> tuple[tuple[Product, ...], str]:
    """Read apparel products from Postgres, falling back for offline dry-runs."""
    if psycopg is None:
        return FALLBACK_CATALOG, "embedded fallback"

    try:
        with psycopg.connect(db_url, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT asin, title, COALESCE(brand, ''), price_idr, department, category
                    FROM products
                    WHERE department IN ('Men', 'Women')
                      AND price_idr > 0
                      AND asin IS NOT NULL
                    ORDER BY asin
                    """
                )
                products = tuple(
                    Product(
                        asin=row[0],
                        title=row[1],
                        brand=row[2],
                        price_idr=int(row[3]),
                        department=row[4],
                        category=row[5] or "",
                    )
                    for row in cur.fetchall()
                )
        if len(products) >= 10:
            return products, "Postgres"
    except Exception:
        pass
    return FALLBACK_CATALOG, "embedded fallback"


class _Builder:
    def __init__(
        self,
        *,
        seed: int,
        catalog: tuple[Product, ...],
        catalog_source: str,
        anchor: datetime,
    ):
        self.rng = random.Random(seed)
        self.catalog = catalog
        self.catalog_source = catalog_source
        self.anchor = anchor
        self.session_index = 0
        self.order_index = 0
        self.thread_index = 0
        self.simulation = Simulation(
            seed=seed,
            sessions=0,
            catalog=catalog,
            catalog_source=catalog_source,
            session_cohorts={},
        )

    def build(self, sessions: int) -> Simulation:
        counts = _cohort_counts(sessions)
        assignments = [
            cohort for cohort in COHORT_NAMES for _ in range(counts[cohort])
        ]
        self.rng.shuffle(assignments)
        self.simulation.sessions = sessions

        for cohort in assignments:
            sid = self._new_session_id()
            self.simulation.session_cohorts[sid] = cohort
            start = self._start_time()
            if cohort == "baseline":
                self._baseline(sid, start)
            elif cohort == "sizing":
                self._sizing(sid, start)
            elif cohort == "policy":
                self._policy(sid, start)
            else:
                self._outfit(sid, start)

        return self.simulation

    def _new_session_id(self) -> str:
        self.session_index += 1
        return f"{SESSION_PREFIX}{self.session_index:04d}"

    def _new_thread_id(self) -> str:
        self.thread_index += 1
        return f"{THREAD_PREFIX}{self.thread_index:05d}"

    def _start_time(self) -> datetime:
        minutes_ago = self.rng.randint(60, 14 * 24 * 60)
        return self.anchor - timedelta(minutes=minutes_ago)

    def _event(
        self,
        session_id: str,
        event_type: str,
        at: datetime,
        *,
        asin: str | None = None,
        query: str | None = None,
        results_count: int | None = None,
        qty: int | None = None,
        price_idr: int | None = None,
    ) -> None:
        self.simulation.events.append(
            SimEvent(
                session_id=session_id,
                event_type=event_type,
                created_at=at,
                asin=asin,
                query=_truncate(query, 200) if query else None,
                results_count=results_count,
                qty=qty,
                price_idr=price_idr,
            )
        )

    def _order(
        self,
        session_id: str,
        lines: list[tuple[Product, int]],
        at: datetime,
    ) -> SimOrder:
        self.order_index += 1
        items = tuple(
            SimOrderItem(
                asin=product.asin,
                title=product.title,
                qty=qty,
                unit_price_idr=product.price_idr,
            )
            for product, qty in lines
        )
        order = SimOrder(
            token=f"{ORDER_PREFIX}{self.order_index:05d}",
            session_id=session_id,
            total_idr=sum(item.qty * item.unit_price_idr for item in items),
            item_count=sum(item.qty for item in items),
            created_at=at,
            items=items,
        )
        self.simulation.orders.append(order)
        self._event(
            session_id,
            "purchase_mock",
            at,
            qty=order.item_count,
            price_idr=order.total_idr,
        )
        return order

    def _turn(
        self,
        thread_id: str,
        session_id: str,
        at: datetime,
        *,
        tools: tuple[str, ...],
        outcome: str,
        latency_range: tuple[int, int],
        token_range: tuple[int, int, int, int],
    ) -> None:
        latency_ms = float(self.rng.randint(*latency_range))
        input_tokens = self.rng.randint(token_range[0], token_range[1])
        output_tokens = self.rng.randint(token_range[2], token_range[3])
        self.simulation.turns.append(
            SimTurn(
                thread_id=thread_id,
                session_id=session_id,
                outcome=outcome,
                tool_names=tools,
                latency_ms=latency_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=_cost_usd(input_tokens, output_tokens),
                created_at=at,
            )
        )

    def _maybe_feedback(
        self,
        thread_id: str,
        session_id: str,
        at: datetime,
        *,
        up_probability: float,
    ) -> None:
        if self.rng.random() >= 0.55:
            return
        rating = 1 if self.rng.random() < up_probability else -1
        self.simulation.feedback.append(
            SimFeedback(
                thread_id=thread_id,
                session_id=session_id,
                rating=rating,
                created_at=at + timedelta(seconds=self.rng.randint(2, 20)),
            )
        )

    def _pick_product(self, *, categories: set[str] | None = None) -> Product:
        pool = [
            product
            for product in self.catalog
            if categories is None or product.category in categories
        ] or list(self.catalog)
        return self.rng.choice(pool)

    def _baseline(self, sid: str, start: datetime) -> None:
        rng = self.rng
        at = start
        query = rng.choice(SEARCH_QUERIES)
        self._event(
            sid,
            "search",
            at,
            query=query,
            results_count=rng.randint(12, 32),
        )
        at += timedelta(seconds=rng.randint(8, 50))
        product = self._pick_product()
        self._event(
            sid,
            "view_product",
            at,
            asin=product.asin,
            price_idr=product.price_idr,
        )

        if rng.random() >= 0.38:
            return
        at += timedelta(seconds=rng.randint(20, 120))
        self._event(
            sid,
            "add_to_cart",
            at,
            asin=product.asin,
            qty=1,
            price_idr=product.price_idr,
        )
        if rng.random() >= 0.55:
            return
        at += timedelta(seconds=rng.randint(20, 100))
        self._event(
            sid,
            "checkout_start",
            at,
            qty=1,
            price_idr=product.price_idr,
        )
        if rng.random() >= 0.55:
            return
        at += timedelta(seconds=rng.randint(10, 60))
        self._order(sid, [(product, 1)], at)

    def _sizing(self, sid: str, start: datetime) -> None:
        rng = self.rng
        at = start
        product = self._pick_product(
            categories=SIZING_CATEGORIES["tops"]
            | SIZING_CATEGORIES["bottoms"]
            | SIZING_CATEGORIES["footwear"]
        )
        self._event(
            sid,
            "view_product",
            at,
            asin=product.asin,
            price_idr=product.price_idr,
        )
        at += timedelta(seconds=rng.randint(15, 70))
        thread_id = self._new_thread_id()
        query = f"Ukuran apa yang cocok untuk {product.title}?"
        self._event(sid, "copilot_message", at, query=query)
        self._turn(
            thread_id,
            sid,
            at + timedelta(seconds=1),
            tools=("recommend_size", "get_product_details"),
            outcome="answered",
            latency_range=(900, 1800),
            token_range=(1400, 2400, 120, 280),
        )
        at += timedelta(seconds=rng.randint(4, 20))
        self._event(
            sid,
            "copilot_product_click",
            at,
            asin=product.asin,
            price_idr=product.price_idr,
        )

        if rng.random() < 0.58:
            at += timedelta(seconds=rng.randint(10, 50))
            self._event(
                sid,
                "add_to_cart",
                at,
                asin=product.asin,
                qty=1,
                price_idr=product.price_idr,
            )
            if rng.random() < 0.72:
                at += timedelta(seconds=rng.randint(20, 90))
                self._event(
                    sid,
                    "checkout_start",
                    at,
                    qty=1,
                    price_idr=product.price_idr,
                )
                if rng.random() < 0.72:
                    at += timedelta(seconds=rng.randint(10, 50))
                    self._order(sid, [(product, 1)], at)
        self._maybe_feedback(thread_id, sid, at, up_probability=0.86)

    def _policy(self, sid: str, start: datetime) -> None:
        rng = self.rng
        at = start
        product = self._pick_product()
        self._event(
            sid,
            "view_product",
            at,
            asin=product.asin,
            price_idr=product.price_idr,
        )
        at += timedelta(seconds=rng.randint(10, 60))
        thread_id = self._new_thread_id()
        query = rng.choice(POLICY_QUERIES)
        self._event(sid, "copilot_message", at, query=query)
        self._turn(
            thread_id,
            sid,
            at + timedelta(seconds=1),
            tools=("lookup_store_policy",),
            outcome="answered",
            latency_range=(650, 1400),
            token_range=(900, 1600, 80, 200),
        )
        at += timedelta(seconds=rng.randint(5, 25))

        if rng.random() < 0.12:
            self._event(
                sid,
                "copilot_product_click",
                at,
                asin=product.asin,
                price_idr=product.price_idr,
            )
            at += timedelta(seconds=rng.randint(10, 40))
            self._event(
                sid,
                "add_to_cart",
                at,
                asin=product.asin,
                qty=1,
                price_idr=product.price_idr,
            )
            if rng.random() < 0.80:
                at += timedelta(seconds=rng.randint(20, 80))
                self._event(
                    sid,
                    "checkout_start",
                    at,
                    qty=1,
                    price_idr=product.price_idr,
                )
                if rng.random() < 0.80:
                    at += timedelta(seconds=rng.randint(10, 45))
                    self._order(sid, [(product, 1)], at)
        self._maybe_feedback(thread_id, sid, at, up_probability=0.92)

    def _pick_outfit(
        self, department: str, budget: int
    ) -> list[Product]:
        rng = self.rng
        slots = OUTFIT_SLOTS[department]
        chosen: list[Product] = []
        remaining = budget

        for index, categories in enumerate(slots):
            slots_left = len(slots) - index
            max_price = (
                remaining
                if slots_left == 1
                else int(remaining / slots_left * 1.15)
            )
            pool = [
                product
                for product in self.catalog
                if product.department == department
                and product.category in categories
                and product not in chosen
                and product.price_idr <= max_price
            ]
            if not pool:
                pool = [
                    product
                    for product in self.catalog
                    if product.department == department
                    and product not in chosen
                    and product.price_idr <= remaining
                ]
            if not pool:
                break
            product = rng.choice(pool)
            chosen.append(product)
            remaining -= product.price_idr

        if len(chosen) < 2:
            cheapest = sorted(
                (
                    product
                    for product in self.catalog
                    if product.department == department and product not in chosen
                ),
                key=lambda product: (product.price_idr, product.asin),
            )
            for product in cheapest:
                if product.price_idr <= remaining:
                    chosen.append(product)
                    remaining -= product.price_idr
                if len(chosen) >= 2:
                    break

        while len(chosen) > 2 and sum(product.price_idr for product in chosen) > budget:
            chosen.pop()

        if len(chosen) < 2:
            for product in sorted(self.catalog, key=lambda item: item.price_idr):
                if product not in chosen and product.price_idr <= remaining:
                    chosen.append(product)
                    remaining -= product.price_idr
                if len(chosen) >= 2:
                    break

        return chosen

    def _outfit(self, sid: str, start: datetime) -> None:
        rng = self.rng
        at = start
        department = rng.choice(("Men", "Women"))
        budget = rng.choice(OUTFIT_BUDGETS)
        self.simulation.outfit_budgets[sid] = budget
        style_id, style_en = rng.choice(OUTFIT_STYLES)
        items = self._pick_outfit(department, budget)
        query = (
            f"Outfit {style_id} {department.lower()} di bawah Rp{budget:,}"
            if rng.random() < 0.55
            else f"Build a {style_en} outfit for {department.lower()} under Rp{budget:,}"
        )
        thread_id = self._new_thread_id()
        self._event(sid, "copilot_message", at, query=query)
        self._turn(
            thread_id,
            sid,
            at + timedelta(seconds=1),
            tools=("search_catalog", "build_outfit"),
            outcome="answered",
            latency_range=(1400, 2800),
            token_range=(1800, 3200, 180, 400),
        )

        at += timedelta(seconds=rng.randint(8, 30))
        for product in items:
            self._event(
                sid,
                "copilot_product_click",
                at,
                asin=product.asin,
                price_idr=product.price_idr,
            )
            at += timedelta(seconds=rng.randint(3, 12))

        if rng.random() >= 0.62:
            self._maybe_feedback(thread_id, sid, at, up_probability=0.80)
            return

        at += timedelta(seconds=rng.randint(10, 40))
        for product in items:
            self._event(
                sid,
                "copilot_add_to_cart",
                at,
                asin=product.asin,
                qty=1,
                price_idr=product.price_idr,
            )
            self._event(
                sid,
                "add_to_cart",
                at,
                asin=product.asin,
                qty=1,
                price_idr=product.price_idr,
            )
            at += timedelta(seconds=rng.randint(3, 10))
        self._turn(
            thread_id,
            sid,
            at + timedelta(seconds=1),
            tools=("add_to_cart",),
            outcome="cart",
            latency_range=(500, 1200),
            token_range=(1200, 2200, 100, 250),
        )

        if rng.random() < 0.74:
            at += timedelta(seconds=rng.randint(20, 70))
            total_qty = len(items)
            total_idr = sum(product.price_idr for product in items)
            self._event(
                sid,
                "checkout_start",
                at,
                qty=total_qty,
                price_idr=total_idr,
            )
            if rng.random() < 0.80:
                at += timedelta(seconds=rng.randint(10, 50))
                self._order(sid, [(product, 1) for product in items], at)
        self._maybe_feedback(thread_id, sid, at, up_probability=0.80)


def generate_simulation(
    *,
    seed: int,
    sessions: int,
    catalog: tuple[Product, ...],
    catalog_source: str,
    anchor: datetime,
) -> Simulation:
    if sessions < 1:
        raise ValueError("sessions must be at least 1")
    return _Builder(
        seed=seed,
        catalog=catalog,
        catalog_source=catalog_source,
        anchor=anchor,
    ).build(sessions)


def validate_simulation(sim: Simulation) -> None:
    """Fail loudly on broken referential or business invariants."""
    if len(sim.session_cohorts) != sim.sessions:
        raise ValueError("session count does not match cohort assignments")
    for session_id in sim.session_cohorts:
        if not session_id.startswith(SESSION_PREFIX):
            raise ValueError(f"session id without sim prefix: {session_id}")

    for event in sim.events:
        if not event.session_id.startswith(SESSION_PREFIX):
            raise ValueError(f"event session id without sim prefix: {event.session_id}")
        if event.session_id not in sim.session_cohorts:
            raise ValueError(f"event references unknown session: {event.session_id}")

    seen_tokens: set[str] = set()
    for order in sim.orders:
        if not order.token.startswith(ORDER_PREFIX):
            raise ValueError(f"order token without sim prefix: {order.token}")
        if order.token in seen_tokens:
            raise ValueError(f"duplicate order token: {order.token}")
        seen_tokens.add(order.token)
        if order.session_id not in sim.session_cohorts:
            raise ValueError(f"order references unknown session: {order.session_id}")
        if order.item_count != sum(item.qty for item in order.items):
            raise ValueError(f"order item_count mismatch: {order.token}")
        if order.total_idr != sum(
            item.qty * item.unit_price_idr for item in order.items
        ):
            raise ValueError(f"order total mismatch: {order.token}")
        if sim.session_cohorts[order.session_id] == "outfit":
            if order.item_count < 2:
                raise ValueError(f"outfit order is not multi-item: {order.token}")
            budget = sim.outfit_budgets.get(order.session_id)
            if budget is None or order.total_idr > budget:
                raise ValueError(f"outfit order exceeds budget: {order.token}")

    seen_threads: set[str] = set()
    for turn in sim.turns:
        if not turn.thread_id.startswith(THREAD_PREFIX):
            raise ValueError(f"thread id without sim prefix: {turn.thread_id}")
        if turn.session_id not in sim.session_cohorts:
            raise ValueError(f"turn references unknown session: {turn.session_id}")
        if turn.outcome not in {"answered", "guardrail", "unanswered", "cart", "error"}:
            raise ValueError(f"invalid turn outcome: {turn.outcome}")
        if turn.cost_usd < 0:
            raise ValueError("turn cost cannot be negative")
        seen_threads.add(turn.thread_id)

    for feedback in sim.feedback:
        if feedback.thread_id not in seen_threads:
            raise ValueError(f"feedback references unknown thread: {feedback.thread_id}")
        if feedback.rating not in (-1, 1):
            raise ValueError(f"invalid feedback rating: {feedback.rating}")


def _print_summary(sim: Simulation, *, dry_run: bool) -> None:
    stats = sim.stats()
    print("Toko Marcell — synthetic traffic simulator")
    print("=" * 78)
    print("SYNTHETIC / SIMULATION DATA — not real customers or revenue.")
    print(
        f"Seed: {sim.seed}  |  Sessions: {sim.sessions}  |  "
        f"Catalog: {sim.catalog_source} ({len(sim.catalog):,} products)"
    )
    print("-" * 78)
    print(f"Generated sessions       {stats['sessions']:>10,}")
    print(f"Orders                   {stats['orders']:>10,}")
    print(f"Revenue (IDR)            {_idr(stats['revenue_idr']):>10}")
    print(f"AOV (IDR)                {_idr(stats['aov_idr']):>10}")
    print(f"Copilot turns            {stats['copilot_turns']:>10,}")
    print(f"Copilot threads          {stats['copilot_threads']:>10,}")
    print(f"Feedback votes           {stats['feedback']:>10,}")
    print(f"Token cost, est. (USD)   {_usd(stats['total_cost_usd']):>10}")
    print(
        f"Cost / assisted order    "
        f"{_usd(stats['cost_per_assisted_order_usd']):>10}"
    )
    print(f"Policy deflection        {_pct(stats['policy_deflection_rate']):>10}")
    print(
        f"Outfit budget violations {stats['outfit_budget_violations']:>10,}"
    )
    print()
    print(
        f"{'Cohort':<24} {'Sessions':>8} {'Orders':>7} {'Conv.':>7} "
        f"{'Add-to-cart':>11} {'Revenue IDR':>13} {'AOV IDR':>11}"
    )
    print("-" * 90)
    for name in COHORT_NAMES:
        cohort = stats["cohorts"][name]
        print(
            f"{COHORT_LABELS[name]:<24} {cohort['sessions']:>8,} "
            f"{cohort['orders']:>7,} {_pct(cohort['conversion_rate']):>7} "
            f"{_pct(cohort['add_to_cart_rate']):>11} "
            f"{_idr(cohort['revenue_idr']):>13} {_idr(cohort['aov_idr']):>11}"
        )
    print("-" * 90)
    baseline = stats["cohorts"]["baseline"]
    assisted_sessions = stats["assisted_sessions"]
    assisted_aov = (
        stats["assisted_revenue_idr"] / stats["assisted_orders"]
        if stats["assisted_orders"]
        else None
    )
    assisted_conversion = _ratio(stats["assisted_orders"], assisted_sessions)
    print(
        f"{'Copilot-assisted total':<24} {assisted_sessions:>8,} "
        f"{stats['assisted_orders']:>7,} {_pct(assisted_conversion):>7} "
        f"{_pct(stats['assisted_add_to_cart_rate']):>11} "
        f"{_idr(stats['assisted_revenue_idr']):>13} {_idr(assisted_aov):>11}"
    )
    print(
        f"Baseline vs assisted: conversion {_pct(baseline['conversion_rate'])} → "
        f"{_pct(assisted_conversion)}, AOV {_idr(baseline['aov_idr'])} → "
        f"{_idr(assisted_aov)} IDR"
    )
    print()
    if dry_run:
        print("Dry run: no database rows written.")
    else:
        print("Simulation validation passed.")


def _existing_sim_counts(cur) -> dict[str, int]:
    queries = {
        "events": "SELECT COUNT(*) FROM events WHERE session_id LIKE 'sim_sess_%'",
        "orders": (
            "SELECT COUNT(*) FROM orders "
            "WHERE token LIKE 'sim_ord_%' OR session_id LIKE 'sim_sess_%'"
        ),
        "copilot_turns": (
            "SELECT COUNT(*) FROM copilot_turns "
            "WHERE thread_id LIKE 'sim_thr_%' OR session_id LIKE 'sim_sess_%'"
        ),
        "copilot_feedback": (
            "SELECT COUNT(*) FROM copilot_feedback "
            "WHERE thread_id LIKE 'sim_thr_%' OR session_id LIKE 'sim_sess_%'"
        ),
    }
    counts = {}
    for table, query in queries.items():
        cur.execute(query)
        counts[table] = int(cur.fetchone()[0])
    return counts


def _reset_sim_rows(cur) -> dict[str, int]:
    counts = {}
    statements = {
        "copilot_feedback": (
            "DELETE FROM copilot_feedback "
            "WHERE thread_id LIKE 'sim_thr_%' OR session_id LIKE 'sim_sess_%'"
        ),
        "copilot_turns": (
            "DELETE FROM copilot_turns "
            "WHERE thread_id LIKE 'sim_thr_%' OR session_id LIKE 'sim_sess_%'"
        ),
        "events": "DELETE FROM events WHERE session_id LIKE 'sim_sess_%'",
        "orders": (
            "DELETE FROM orders "
            "WHERE token LIKE 'sim_ord_%' OR session_id LIKE 'sim_sess_%'"
        ),
    }
    for table, statement in statements.items():
        cur.execute(statement)
        counts[table] = int(cur.rowcount)
    return counts


def write_simulation(
    *,
    db_url: str,
    sim: Simulation,
    reset_sim_only: bool,
) -> dict[str, int]:
    if psycopg is None:
        raise RuntimeError("psycopg is required to write simulation rows")

    with psycopg.connect(db_url, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            reset_counts = {}
            if reset_sim_only:
                reset_counts = _reset_sim_rows(cur)
            else:
                existing = _existing_sim_counts(cur)
                if any(existing.values()):
                    raise RuntimeError(
                        "existing sim_* rows found; rerun with --reset-sim-only "
                        f"({existing})"
                    )

            cur.executemany(
                """
                INSERT INTO events
                    (session_id, event_type, asin, query, results_count, qty,
                     price_idr, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        event.session_id,
                        event.event_type,
                        event.asin,
                        event.query,
                        event.results_count,
                        event.qty,
                        event.price_idr,
                        event.created_at,
                    )
                    for event in sim.events
                ],
            )

            for order in sim.orders:
                cur.execute(
                    """
                    INSERT INTO orders
                        (token, total_idr, item_count, status, session_id, created_at)
                    VALUES (%s, %s, %s, 'paid', %s, %s)
                    RETURNING id
                    """,
                    (
                        order.token,
                        order.total_idr,
                        order.item_count,
                        order.session_id,
                        order.created_at,
                    ),
                )
                order_id = cur.fetchone()[0]
                cur.executemany(
                    """
                    INSERT INTO order_items
                        (order_id, asin, title, qty, unit_price_idr)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    [
                        (
                            order_id,
                            item.asin,
                            item.title,
                            item.qty,
                            item.unit_price_idr,
                        )
                        for item in order.items
                    ],
                )

            cur.executemany(
                """
                INSERT INTO copilot_turns
                    (thread_id, session_id, outcome, tool_names, latency_ms,
                     input_tokens, output_tokens, cost_usd, model, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        turn.thread_id,
                        turn.session_id,
                        turn.outcome,
                        list(turn.tool_names),
                        turn.latency_ms,
                        turn.input_tokens,
                        turn.output_tokens,
                        turn.cost_usd,
                        SIM_MODEL,
                        turn.created_at,
                    )
                    for turn in sim.turns
                ],
            )

            cur.executemany(
                """
                INSERT INTO copilot_feedback
                    (thread_id, session_id, rating, created_at)
                VALUES (%s, %s, %s, %s)
                """,
                [
                    (
                        feedback.thread_id,
                        feedback.session_id,
                        feedback.rating,
                        feedback.created_at,
                    )
                    for feedback in sim.feedback
                ],
            )
        conn.commit()

    return reset_counts


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate deterministic synthetic traffic for the Toko Marcell demo."
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--sessions", type=_positive_int, default=300)
    parser.add_argument(
        "--db-url",
        default=None,
        help="Postgres URL (default: DATABASE_URL env var)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="generate and validate in memory without writing to Postgres",
    )
    parser.add_argument(
        "--reset-sim-only",
        action="store_true",
        help="delete prior sim_* rows before inserting this run",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.dry_run and args.reset_sim_only:
        print("--dry-run and --reset-sim-only cannot be combined", file=sys.stderr)
        return 2

    db_url = args.db_url or os.getenv("DATABASE_URL") or "postgresql://toko:toko@localhost:5432/toko"
    catalog, source = load_catalog(db_url)
    if source != "Postgres" and not args.dry_run:
        print(
            "Could not read the catalog from Postgres. Start the database or use "
            "--dry-run with the embedded fallback catalog.",
            file=sys.stderr,
        )
        return 1

    anchor = datetime.now(UTC) - timedelta(hours=2)
    sim = generate_simulation(
        seed=args.seed,
        sessions=args.sessions,
        catalog=catalog,
        catalog_source=source,
        anchor=anchor,
    )
    validate_simulation(sim)
    _print_summary(sim, dry_run=args.dry_run)

    if args.dry_run:
        return 0

    try:
        reset_counts = write_simulation(
            db_url=db_url,
            sim=sim,
            reset_sim_only=args.reset_sim_only,
        )
    except Exception as exc:
        print(f"Write failed: {exc}", file=sys.stderr)
        return 1

    if reset_counts:
        print(
            "Reset prior sim rows: "
            + ", ".join(
                f"{table}={count}" for table, count in reset_counts.items()
            )
        )
    print(
        f"Wrote {len(sim.events):,} events, {len(sim.orders):,} orders, "
        f"{len(sim.turns):,} turns and {len(sim.feedback):,} feedback rows."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
