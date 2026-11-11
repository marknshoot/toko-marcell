"""Event tracking and the funnel/KPI summary endpoint."""

import analytics
from db import get_conn
from fastapi import APIRouter, Query, Request
from rate_limiter import check_write_rate_limit
from schemas import EventIn
from web import client_ip, rate_limit_error

router = APIRouter()

# Backwards-compatible names: older code/tests import these from this module.
rate = analytics.rate
cost_rate = analytics.cost_rate


@router.post("/events", status_code=201)
def create_event(event: EventIn, request: Request):
    allowed, retry_after = check_write_rate_limit(client_ip(request))
    if not allowed:
        raise rate_limit_error("Too many events. Please slow down.", retry_after)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO events
                    (session_id, event_type, asin, query, results_count, qty, price_idr)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    event.session_id,
                    event.event_type,
                    event.asin,
                    event.query,
                    event.results_count,
                    event.qty,
                    event.price_idr,
                ),
            )
            event_id = cur.fetchone()[0]
            conn.commit()

    return {"id": event_id, "event_type": event.event_type}


def _cohort_block(
    sessions: int,
    add_sessions: int,
    purchase_sessions: int,
) -> dict[str, object]:
    return {
        "sessions": sessions,
        "sessions_with_add_to_cart": add_sessions,
        "purchase_sessions": purchase_sessions,
        "add_to_cart_rate": analytics.rate(add_sessions, sessions),
        "purchase_rate": analytics.rate(purchase_sessions, sessions),
    }


@router.get("/events/summary")
def events_summary(since_days: int = Query(30, ge=1, le=365)):
    """Funnel, revenue, cohort and copilot-quality KPIs.

    The event funnel keeps its original keys. Revenue/AOV come only from the
    server-side ``orders`` table; copilot quality comes from ``copilot_turns``
    and ``copilot_feedback``.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            funnel = analytics.event_funnel(cur, since_days)
            search = analytics.search_health(cur, since_days, top_queries=False)
            revenue = analytics.order_revenue(cur, since_days)
            turn_stats = analytics.copilot_turn_stats(cur, since_days)
            feedback = analytics.copilot_feedback_stats(cur, since_days)
            assisted_orders = analytics.copilot_assisted_orders(cur, since_days)
            cohort_rows, purchase_source = analytics.session_cohorts_by_purchase(cur, since_days)

    by_type = funnel["by_type"]
    sessions_by_step = funnel["sessions_by_step"]
    viewed = sessions_by_step.get("view_product", 0)
    added = sessions_by_step.get("add_to_cart", 0)
    started = sessions_by_step.get("checkout_start", 0)
    purchased = sessions_by_step.get("purchase_mock", 0)

    cohorts: dict[str, object] = {
        "copilot": _cohort_block(0, 0, 0),
        "non_copilot": _cohort_block(0, 0, 0),
    }
    for is_copilot, sessions, add_sessions, purchase_sessions in cohort_rows:
        key = "copilot" if is_copilot else "non_copilot"
        cohorts[key] = _cohort_block(sessions, add_sessions, purchase_sessions)

    cohort_caveat = (
        "Cohorts are correlational and self-selected: sessions that open the copilot differ from "
        "sessions that do not, and there is no holdout/control group. "
        f"Purchase sessions are matched on {purchase_source}."
    )
    cohorts["purchase_source"] = purchase_source
    cohorts["caveat"] = cohort_caveat

    copilot_messages = by_type.get("copilot_message", 0)
    copilot_adds = by_type.get("copilot_add_to_cart", 0)
    total_adds = by_type.get("add_to_cart", 0)

    return {
        "since_days": since_days,
        "events_total": funnel["events_total"],
        "by_type": by_type,
        "sessions_by_step": sessions_by_step,
        "rates": {
            "view_to_cart": analytics.rate(added, viewed),
            "cart_to_checkout": analytics.rate(started, added),
            "checkout_to_purchase": analytics.rate(purchased, started),
            "view_to_purchase": analytics.rate(purchased, viewed),
            "search_to_pdp": search["search_to_pdp_rate"],
        },
        "search": {
            "total": search["total_searches"],
            "zero_result": search["zero_result_searches"],
            "zero_result_rate": search["zero_result_rate"],
        },
        "copilot": {
            "messages": copilot_messages,
            "product_clicks": by_type.get("copilot_product_click", 0),
            "add_to_cart": copilot_adds,
            "total_add_to_cart": total_adds,
            # Share of all add-to-cart events that were copilot-assisted.
            "assisted_add_to_cart_rate": analytics.rate(copilot_adds, total_adds),
        },
        "revenue": {
            "order_count": revenue["order_count"],
            "revenue_idr": revenue["revenue_idr"],
            "aov_idr": revenue["aov_idr"],
        },
        "cohorts": cohorts,
        "unit_economics": {
            "total_cost_usd": (
                round(turn_stats["total_cost_usd"], 8) if turn_stats["total_cost_usd"] is not None else None
            ),
            "avg_cost_per_turn_usd": analytics.cost_rate(turn_stats["total_cost_usd"], turn_stats["total_turns"]),
            "cost_per_conversation_usd": analytics.cost_rate(turn_stats["total_cost_usd"], turn_stats["conversations"]),
            "cost_per_resolved_conversation_usd": analytics.cost_rate(
                turn_stats["total_cost_usd"], turn_stats["resolved_conversations"]
            ),
            "cost_per_assisted_order_usd": analytics.cost_rate(turn_stats["total_cost_usd"], assisted_orders),
        },
        "copilot_quality": {
            "turns": turn_stats["total_turns"],
            "unanswered_rate": analytics.rate(turn_stats["unanswered_turns"], turn_stats["total_turns"]),
            "guardrail_rate": analytics.rate(turn_stats["guardrail_turns"], turn_stats["total_turns"]),
            "avg_latency_ms": turn_stats["avg_latency_ms"],
            "p50_latency_ms": turn_stats["p50_latency_ms"],
            "p95_latency_ms": turn_stats["p95_latency_ms"],
            "thumbs_up": feedback["thumbs_up"],
            "thumbs_down": feedback["thumbs_down"],
            "thumbs_up_rate": analytics.rate(feedback["thumbs_up"], feedback["thumbs_up"] + feedback["thumbs_down"]),
        },
        "caveats": [
            "search_to_pdp is a session-level proxy (search + view_product in one session)",
            "revenue/aov use only server-confirmed orders; purchase_mock is a directional "
            "funnel event and remains client-assertable",
            "copilot.assisted_add_to_cart_rate = copilot_add_to_cart / all add_to_cart; "
            "a copilot-driven add fires BOTH add_to_cart (from the cart) and copilot_add_to_cart, "
            "so the rate is the copilot-attributed share of total adds, not an independent funnel",
            "unit_economics uses configured per-million token prices, not provider invoices; "
            "cost is null when no turn reported token usage, and pre-agent guardrail tokens are "
            "not included",
            cohort_caveat,
        ],
    }
