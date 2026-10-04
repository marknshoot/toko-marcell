"""Event tracking endpoints."""

import logging

from db import get_conn
from fastapi import APIRouter, Query
from schemas import EventIn

logger = logging.getLogger(__name__)

router = APIRouter()


def rate(numerator, denominator):
    """None instead of a fake 0.0 when there is no denominator yet."""
    if not denominator:
        return None
    return round(numerator / denominator, 4)


@router.post("/events", status_code=201)
def create_event(event: EventIn):
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
    """Funnel KPIs, computed from the events table."""
    window = "created_at > now() - make_interval(days => %s)"

    with get_conn() as conn:
        with conn.cursor() as cur:
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

    viewed = sessions_by_step.get("view_product", 0)
    added = sessions_by_step.get("add_to_cart", 0)
    started = sessions_by_step.get("checkout_start", 0)
    purchased = sessions_by_step.get("purchase_mock", 0)
    searched = sessions_by_step.get("search", 0)

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
        "caveats": [
            "search_to_pdp is a session-level proxy (search + view_product in one session)",
            "purchase_mock is browser-asserted until checkout is confirmed server-side (B5)",
        ],
    }
