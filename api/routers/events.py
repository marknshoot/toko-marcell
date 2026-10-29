"""Event tracking endpoints."""

import logging

from db import get_conn
from fastapi import APIRouter, HTTPException, Query, Request
from rate_limiter import check_write_rate_limit
from schemas import EventIn

from routers.copilot import _get_client_ip

logger = logging.getLogger(__name__)

router = APIRouter()


def rate(numerator, denominator):
    """None instead of a fake 0.0 when there is no denominator yet."""
    if not denominator:
        return None
    return round(numerator / denominator, 4)


def cost_rate(total_cost_usd, denominator):
    """Money-safe division: keep 8 decimals and unknown cost as ``None``."""
    if total_cost_usd is None or not denominator:
        return None
    return round(float(total_cost_usd) / denominator, 8)


@router.post("/events", status_code=201)
def create_event(event: EventIn, request: Request):
    allowed, retry_after = check_write_rate_limit(_get_client_ip(request))
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many events. Please slow down.",
            headers={"Retry-After": str(retry_after)},
        )
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


@router.get("/events/summary")
def events_summary(since_days: int = Query(30, ge=1, le=365)):
    """Funnel, revenue, cohort and copilot-quality KPIs.

    The event funnel keeps its original keys. Revenue/AOV come only from the
    server-side ``orders`` table; copilot quality comes from ``copilot_turns``
    and ``copilot_feedback`` (phase 9).
    """
    window = "created_at > now() - make_interval(days => %s)"

    with get_conn() as conn:
        with conn.cursor() as cur:
            # ── Event funnel (unchanged) ─────────────────────────────────────
            cur.execute(
                f"""
                SELECT event_type, COUNT(*), COUNT(DISTINCT session_id)
                FROM events WHERE {window}
                GROUP BY event_type
                """,
                (since_days,),
            )
            by_type = {}
            sessions_by_step = {}
            events_total = 0
            for event_type, count, sessions in cur.fetchall():
                by_type[event_type] = count
                sessions_by_step[event_type] = sessions
                events_total += count

            cur.execute(
                f"""
                SELECT COUNT(*), COUNT(*) FILTER (WHERE results_count = 0)
                FROM events WHERE event_type = 'search' AND {window}
                """,
                (since_days,),
            )
            searches, zero_result = cur.fetchone()

            cur.execute(
                f"""
                SELECT COUNT(*) FROM (
                    SELECT session_id FROM events
                    WHERE event_type = 'search' AND {window}
                    INTERSECT
                    SELECT session_id FROM events
                    WHERE event_type = 'view_product' AND {window}
                ) AS searched_and_viewed
                """,
                (since_days, since_days),
            )
            search_to_pdp_sessions = cur.fetchone()[0]

            # ── Revenue: server-confirmed orders only ────────────────────────
            cur.execute(
                f"""
                SELECT
                    COUNT(*) FILTER (WHERE status = 'paid') AS order_count,
                    COALESCE(SUM(total_idr) FILTER (WHERE status = 'paid'), 0) AS revenue_idr,
                    AVG(total_idr) FILTER (WHERE status = 'paid') AS aov_idr
                FROM orders WHERE {window}
                """,
                (since_days,),
            )
            order_count, revenue_idr, aov_idr = cur.fetchone()

            # ── Cohorts: copilot users vs everyone else (descriptive) ────────
            cur.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = 'orders' AND column_name = 'session_id'
                )
                """
            )
            orders_have_session = cur.fetchone()[0]
            if orders_have_session:
                purchase_exists = """
                    EXISTS (
                        SELECT 1 FROM orders o
                        WHERE o.session_id = cs.session_id
                          AND o.status = 'paid'
                          AND o.created_at > now() - make_interval(days => %s)
                    )
                """
                purchase_source = "orders"
            else:
                purchase_exists = """
                    EXISTS (
                        SELECT 1 FROM events p
                        WHERE p.session_id = cs.session_id
                          AND p.event_type = 'purchase_mock'
                          AND p.created_at > now() - make_interval(days => %s)
                    )
                """
                purchase_source = "purchase_mock"

            cur.execute(
                f"""
                WITH copilot_sessions AS (
                    SELECT DISTINCT session_id FROM events
                    WHERE event_type = 'copilot_message' AND {window}
                ),
                all_sessions AS (
                    SELECT DISTINCT session_id FROM events WHERE {window}
                ),
                cohort_sessions AS (
                    SELECT s.session_id,
                           EXISTS (
                               SELECT 1 FROM copilot_sessions c
                               WHERE c.session_id = s.session_id
                           ) AS is_copilot
                    FROM all_sessions s
                )
                SELECT
                    is_copilot,
                    COUNT(*) AS sessions,
                    COUNT(*) FILTER (
                        WHERE EXISTS (
                            SELECT 1 FROM events e
                            WHERE e.session_id = cs.session_id
                              AND e.event_type = 'add_to_cart'
                              AND e.created_at > now() - make_interval(days => %s)
                        )
                    ) AS sessions_with_add_to_cart,
                    COUNT(*) FILTER (WHERE {purchase_exists}) AS purchase_sessions
                FROM cohort_sessions cs
                GROUP BY is_copilot
                """,
                (since_days, since_days, since_days, since_days),
            )
            cohort_rows = cur.fetchall()

            # ── Copilot quality: turns + explicit feedback ───────────────────
            cur.execute(
                f"""
                SELECT
                    COUNT(*) AS turns,
                    COUNT(*) FILTER (WHERE outcome = 'unanswered') AS unanswered,
                    COUNT(*) FILTER (WHERE outcome = 'guardrail') AS guardrail,
                    AVG(latency_ms) AS avg_latency_ms,
                    percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50_latency_ms,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_latency_ms
                FROM copilot_turns WHERE {window}
                """,
                (since_days,),
            )
            turns, unanswered, guardrail, avg_latency, p50_latency, p95_latency = cur.fetchone()

            cur.execute(
                f"""
                SELECT
                    COUNT(*) FILTER (WHERE rating = 1) AS thumbs_up,
                    COUNT(*) FILTER (WHERE rating = -1) AS thumbs_down
                FROM copilot_feedback WHERE {window}
                """,
                (since_days,),
            )
            thumbs_up, thumbs_down = cur.fetchone()

            # ── Unit economics (phase 13) ────────────────────────────────────
            cur.execute(
                f"""
                SELECT
                    SUM(cost_usd) AS total_cost_usd,
                    COUNT(*) AS turns,
                    COUNT(DISTINCT thread_id) AS conversations,
                    COUNT(DISTINCT thread_id) FILTER (
                        WHERE outcome IN ('answered', 'cart')
                    ) AS resolved_conversations
                FROM copilot_turns WHERE {window}
                """,
                (since_days,),
            )
            total_cost, cost_turns, conversations, resolved_conversations = cur.fetchone()

            cur.execute(
                """
                SELECT COUNT(*) FROM orders o
                WHERE o.status = 'paid'
                  AND o.session_id IS NOT NULL
                  AND o.created_at > now() - make_interval(days => %s)
                  AND EXISTS (
                      SELECT 1 FROM copilot_turns ct
                      WHERE ct.session_id = o.session_id
                        AND ct.created_at > now() - make_interval(days => %s)
                  )
                """,
                (since_days, since_days),
            )
            assisted_orders = cur.fetchone()[0]

    viewed = sessions_by_step.get("view_product", 0)
    added = sessions_by_step.get("add_to_cart", 0)
    started = sessions_by_step.get("checkout_start", 0)
    purchased = sessions_by_step.get("purchase_mock", 0)
    searched = sessions_by_step.get("search", 0)

    # ── Copilot funnel (Phase 5) ─────────────────────────────────────────────
    copilot_messages = by_type.get("copilot_message", 0)
    copilot_clicks = by_type.get("copilot_product_click", 0)
    copilot_adds = by_type.get("copilot_add_to_cart", 0)
    total_adds = by_type.get("add_to_cart", 0)

    # ── Cohorts (Phase 9) ────────────────────────────────────────────────────
    cohort_data = {
        "copilot": {"sessions": 0, "sessions_with_add_to_cart": 0, "purchase_sessions": 0},
        "non_copilot": {"sessions": 0, "sessions_with_add_to_cart": 0, "purchase_sessions": 0},
    }
    for is_copilot, sessions, add_sessions, purchase_sessions in cohort_rows:
        key = "copilot" if is_copilot else "non_copilot"
        cohort_data[key] = {
            "sessions": sessions,
            "sessions_with_add_to_cart": add_sessions,
            "purchase_sessions": purchase_sessions,
        }
    for key in ("copilot", "non_copilot"):
        c = cohort_data[key]
        c["add_to_cart_rate"] = rate(c["sessions_with_add_to_cart"], c["sessions"])
        c["purchase_rate"] = rate(c["purchase_sessions"], c["sessions"])

    cohort_caveat = (
        "Cohorts are correlational and self-selected: sessions that open the copilot differ from "
        "sessions that do not, and there is no holdout/control group. "
        f"Purchase sessions are matched on {purchase_source}."
    )
    cohort_data["purchase_source"] = purchase_source
    cohort_data["caveat"] = cohort_caveat

    return {
        "since_days": since_days,
        "events_total": events_total,
        "by_type": by_type,
        "sessions_by_step": sessions_by_step,
        "rates": {
            "view_to_cart": rate(added, viewed),
            "cart_to_checkout": rate(started, added),
            "checkout_to_purchase": rate(purchased, started),
            "view_to_purchase": rate(purchased, viewed),
            "search_to_pdp": rate(search_to_pdp_sessions, searched),
        },
        "search": {
            "total": searches,
            "zero_result": zero_result,
            "zero_result_rate": rate(zero_result, searches),
        },
        "copilot": {
            "messages": copilot_messages,
            "product_clicks": copilot_clicks,
            "add_to_cart": copilot_adds,
            "total_add_to_cart": total_adds,
            # Share of all add-to-cart events that were copilot-assisted.
            "assisted_add_to_cart_rate": rate(copilot_adds, total_adds),
        },
        "revenue": {
            "order_count": order_count,
            "revenue_idr": int(revenue_idr or 0),
            "aov_idr": round(float(aov_idr), 2) if aov_idr is not None else None,
        },
        "cohorts": cohort_data,
        "unit_economics": {
            "total_cost_usd": round(float(total_cost), 8) if total_cost is not None else None,
            "avg_cost_per_turn_usd": cost_rate(total_cost, cost_turns),
            "cost_per_conversation_usd": cost_rate(total_cost, conversations),
            "cost_per_resolved_conversation_usd": cost_rate(
                total_cost, resolved_conversations
            ),
            "cost_per_assisted_order_usd": cost_rate(total_cost, assisted_orders),
        },
        "copilot_quality": {
            "turns": turns,
            "unanswered_rate": rate(unanswered, turns),
            "guardrail_rate": rate(guardrail, turns),
            "avg_latency_ms": round(float(avg_latency), 2) if avg_latency is not None else None,
            "p50_latency_ms": round(float(p50_latency), 2) if p50_latency is not None else None,
            "p95_latency_ms": round(float(p95_latency), 2) if p95_latency is not None else None,
            "thumbs_up": thumbs_up,
            "thumbs_down": thumbs_down,
            "thumbs_up_rate": rate(thumbs_up, thumbs_up + thumbs_down),
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
