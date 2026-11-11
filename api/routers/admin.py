"""Token-gated operational and business KPI dashboard endpoint.

Every number here is derived from the seeded/simulated demo database. The
endpoint deliberately labels that in ``metadata`` and never calls the result
real revenue. Authentication accepts a Bearer JWT issued by ``/auth/login``
(Argon2id + rotating refresh cookie) and still accepts the legacy
``X-Admin-Token`` header or ``token`` query parameter compared in constant time
against ``settings.ADMIN_TOKEN``.

The SQL lives in :mod:`analytics` (shared with ``/events/summary``); this module
shapes the response.
"""

from datetime import UTC, datetime

import analytics
from auth import AdminPrincipal, get_current_admin
from db import get_conn
from fastapi import APIRouter, Depends, Query, Request, Response

router = APIRouter(prefix="/admin", tags=["admin"])

# Backwards-compatible dependency name used by older integrations/tests.
get_admin_token = get_current_admin

DEMO_CAVEAT = "All metrics are derived from seeded/simulated demo data; not real customer revenue."
COHORT_CAVEAT = "Observational comparison; copilot usage is self-selected, not a randomized trial."


def _session_funnel(cur, since_days: int) -> dict[str, int]:
    """Distinct sessions per funnel step (plus total sessions)."""
    cur.execute(
        f"""
        SELECT
            COUNT(DISTINCT session_id) AS sessions,
            COUNT(DISTINCT session_id) FILTER (WHERE event_type = 'view_product') AS view_sessions,
            COUNT(DISTINCT session_id) FILTER (WHERE event_type = 'add_to_cart') AS cart_sessions,
            COUNT(DISTINCT session_id) FILTER (WHERE event_type = 'checkout_start') AS checkout_sessions
        FROM events WHERE {analytics.WINDOW}
        """,
        (since_days,),
    )
    sessions, view_sessions, cart_sessions, checkout_sessions = cur.fetchone()
    return {
        "sessions": int(sessions),
        "view_sessions": int(view_sessions),
        "cart_sessions": int(cart_sessions),
        "checkout_sessions": int(checkout_sessions),
    }


def _cohort_block(sessions: int, add_sessions: int, order_sessions: int, order_count: int, revenue: int):
    return {
        "sessions": sessions,
        "add_to_cart_rate": analytics.rate(add_sessions, sessions),
        "conversion_rate": analytics.rate(order_sessions, sessions),
        "aov_idr": round(revenue / order_count, 2) if order_count else None,
    }


@router.get("/kpi")
def admin_kpi(
    request: Request,
    response: Response,
    since_days: int = Query(30, ge=1, le=365),
    admin: AdminPrincipal = Depends(get_admin_token),  # noqa: B008
):
    """Return synthetic demo KPIs for the requested window.

    Missing/invalid credentials return ``401``.  The legacy static token is
    still accepted for scripts, while the dashboard uses a Bearer JWT.
    """
    response.headers["Cache-Control"] = "no-store"

    with get_conn() as conn:
        with conn.cursor() as cur:
            funnel = _session_funnel(cur, since_days)
            revenue = analytics.order_revenue(cur, since_days)
            search = analytics.search_health(cur, since_days)
            turns = analytics.copilot_turn_stats(cur, since_days)
            feedback = analytics.copilot_feedback_stats(cur, since_days)
            assisted_orders = analytics.copilot_assisted_orders(cur, since_days)
            deflection = analytics.copilot_deflection(cur, since_days)
            cohort_rows = analytics.copilot_cohorts(cur, since_days)

    cohorts: dict[str, object] = {
        "copilot_sessions": _cohort_block(0, 0, 0, 0, 0),
        "non_copilot_sessions": _cohort_block(0, 0, 0, 0, 0),
    }
    for (
        is_copilot,
        sessions,
        add_sessions,
        order_sessions,
        order_count,
        cohort_revenue,
    ) in cohort_rows:
        key = "copilot_sessions" if is_copilot else "non_copilot_sessions"
        cohorts[key] = _cohort_block(
            int(sessions), int(add_sessions), int(order_sessions), int(order_count), int(cohort_revenue)
        )
    cohorts["caveat"] = COHORT_CAVEAT

    sessions = funnel["sessions"]
    cart_sessions = funnel["cart_sessions"]
    checkout_sessions = funnel["checkout_sessions"]
    order_sessions = revenue["order_sessions"]
    total_turns = turns["total_turns"]
    unanswered = turns["unanswered_turns"]
    thumbs_up, thumbs_down = feedback["thumbs_up"], feedback["thumbs_down"]

    return {
        "metadata": {
            "since_days": since_days,
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "synthetic": True,
            "environment": "demo",
            "caveat": DEMO_CAVEAT,
        },
        "revenue": {
            "order_count": revenue["order_count"],
            "total_revenue_idr": revenue["revenue_idr"],
            "aov_idr": revenue["aov_idr"],
        },
        "funnel": {
            "sessions": sessions,
            "view_product_sessions": funnel["view_sessions"],
            "add_to_cart_sessions": cart_sessions,
            "checkout_start_sessions": checkout_sessions,
            "order_sessions": order_sessions,
            "rates": {
                "view_to_cart": analytics.rate(cart_sessions, funnel["view_sessions"]),
                "cart_to_checkout": analytics.rate(checkout_sessions, cart_sessions),
                "checkout_to_order": analytics.rate(order_sessions, checkout_sessions),
                "overall_conversion": analytics.rate(order_sessions, sessions),
            },
        },
        "cohorts": cohorts,
        "unit_economics": {
            "total_cost_usd": (round(turns["total_cost_usd"], 8) if turns["total_cost_usd"] is not None else None),
            "avg_cost_per_turn_usd": analytics.cost_rate(turns["total_cost_usd"], total_turns),
            "cost_per_conversation_usd": analytics.cost_rate(turns["total_cost_usd"], turns["conversations"]),
            "cost_per_resolved_conversation_usd": analytics.cost_rate(
                turns["total_cost_usd"], turns["resolved_conversations"]
            ),
            "cost_per_assisted_order_usd": analytics.cost_rate(turns["total_cost_usd"], assisted_orders),
        },
        "search_health": {
            "total_searches": search["total_searches"],
            "zero_result_searches": search["zero_result_searches"],
            "zero_result_rate": search["zero_result_rate"],
            "search_to_pdp_rate": search["search_to_pdp_rate"],
            "top_zero_result_queries": search["top_zero_result_queries"],
            # The dashboard table predates the zero_result flag: keep its shape.
            "top_queries": [{"query": row["query"], "count": row["count"]} for row in search["top_queries"]],
        },
        "copilot_quality": {
            "total_turns": total_turns,
            "answered_turns": turns["answered_turns"],
            "guardrail_turns": turns["guardrail_turns"],
            "unanswered_turns": unanswered,
            "unanswered_rate": analytics.rate(unanswered, total_turns),
            "deflection_eligible_conversations": deflection["eligible_conversations"],
            "deflection_answered_conversations": deflection["answered_conversations"],
            "deflection_rate": deflection["deflection_rate"],
            "latency_p50_ms": turns["p50_latency_ms"],
            "latency_p95_ms": turns["p95_latency_ms"],
            "thumbs_up": thumbs_up,
            "thumbs_down": thumbs_down,
            "thumbs_up_rate": analytics.rate(thumbs_up, thumbs_up + thumbs_down),
        },
    }
