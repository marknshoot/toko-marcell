"""Token-gated operational and business KPI dashboard endpoint.

Every number here is derived from the seeded/simulated demo database. The
endpoint deliberately labels that in ``metadata`` and never calls the result
real revenue. Authentication accepts the token as an ``X-Admin-Token`` header
or a ``token`` query parameter, compared in constant time against
``settings.ADMIN_TOKEN``.
"""

import hmac
from datetime import UTC, datetime

from config import get_settings
from db import get_conn
from fastapi import APIRouter, HTTPException, Query, Request, Response

router = APIRouter(prefix="/admin", tags=["admin"])

DEMO_CAVEAT = (
    "All metrics are derived from seeded/simulated demo data; not real customer revenue."
)
COHORT_CAVEAT = (
    "Observational comparison; copilot usage is self-selected, not a randomized trial."
)


def _rate(numerator: int | float, denominator: int | float) -> float | None:
    """Return ``None`` instead of a fake 0.0 when there is no denominator."""
    if not denominator:
        return None
    return round(numerator / denominator, 4)


def _require_admin(request: Request, token: str | None) -> None:
    settings = get_settings()
    configured = settings.ADMIN_TOKEN or ""
    if not configured:
        raise HTTPException(
            status_code=503,
            detail="Admin dashboard is disabled (ADMIN_TOKEN not configured).",
        )

    provided = request.headers.get("X-Admin-Token") or token or ""
    if not hmac.compare_digest(provided.encode("utf-8"), configured.encode("utf-8")):
        raise HTTPException(status_code=403, detail="Invalid admin token.")


def _cohort_block(
    sessions: int,
    add_sessions: int,
    order_sessions: int,
    order_count: int,
    revenue: int,
) -> dict:
    return {
        "sessions": sessions,
        "add_to_cart_rate": _rate(add_sessions, sessions),
        "conversion_rate": _rate(order_sessions, sessions),
        "aov_idr": round(revenue / order_count, 2) if order_count else None,
    }


@router.get("/kpi")
def admin_kpi(
    request: Request,
    response: Response,
    since_days: int = Query(30, ge=1, le=365),
    token: str | None = Query(None, description="Admin token (or use X-Admin-Token)"),
):
    """Return synthetic demo KPIs for the requested window.

    Auth failures are ``503`` when the deployment has no token configured and
    ``403`` when the supplied token is missing or wrong.
    """
    _require_admin(request, token)
    response.headers["Cache-Control"] = "no-store"

    window = "created_at > now() - make_interval(days => %s)"

    with get_conn() as conn:
        with conn.cursor() as cur:
            # ── Session funnel ───────────────────────────────────────────────
            cur.execute(
                f"""
                SELECT
                    COUNT(DISTINCT session_id) AS sessions,
                    COUNT(DISTINCT session_id) FILTER (
                        WHERE event_type = 'view_product'
                    ) AS view_product_sessions,
                    COUNT(DISTINCT session_id) FILTER (
                        WHERE event_type = 'add_to_cart'
                    ) AS add_to_cart_sessions,
                    COUNT(DISTINCT session_id) FILTER (
                        WHERE event_type = 'checkout_start'
                    ) AS checkout_start_sessions,
                    COUNT(DISTINCT session_id) FILTER (
                        WHERE event_type = 'search'
                    ) AS search_sessions
                FROM events WHERE {window}
                """,
                (since_days,),
            )
            (
                sessions,
                view_sessions,
                cart_sessions,
                checkout_sessions,
                search_sessions,
            ) = cur.fetchone()

            # ── Confirmed orders (revenue-grade only) ────────────────────────
            cur.execute(
                f"""
                SELECT
                    COUNT(*) FILTER (WHERE status = 'paid') AS order_count,
                    COALESCE(SUM(total_idr) FILTER (WHERE status = 'paid'), 0) AS revenue_idr,
                    AVG(total_idr) FILTER (WHERE status = 'paid') AS aov_idr,
                    COUNT(DISTINCT session_id) FILTER (
                        WHERE status = 'paid' AND session_id IS NOT NULL
                    ) AS order_sessions
                FROM orders WHERE {window}
                """,
                (since_days,),
            )
            order_count, revenue_idr, aov_idr, order_sessions = cur.fetchone()

            # ── Search health ────────────────────────────────────────────────
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

            cur.execute(
                f"""
                SELECT query, COUNT(*) AS count
                FROM events
                WHERE event_type = 'search'
                  AND results_count = 0
                  AND query IS NOT NULL
                  AND btrim(query) <> ''
                  AND {window}
                GROUP BY query
                ORDER BY count DESC, query ASC
                LIMIT 20
                """,
                (since_days,),
            )
            top_zero_result_queries = [
                {"query": row[0], "count": int(row[1])} for row in cur.fetchall()
            ]

            cur.execute(
                f"""
                SELECT query, COUNT(*) AS count
                FROM events
                WHERE event_type = 'search'
                  AND query IS NOT NULL
                  AND btrim(query) <> ''
                  AND {window}
                GROUP BY query
                ORDER BY count DESC, query ASC
                LIMIT 20
                """,
                (since_days,),
            )
            top_queries = [
                {"query": row[0], "count": int(row[1])} for row in cur.fetchall()
            ]

            # ── Cohorts: copilot users vs everyone else (descriptive) ────────
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
                ),
                cohort_stats AS (
                    SELECT
                        cs.is_copilot,
                        COUNT(*) AS sessions,
                        COUNT(*) FILTER (
                            WHERE EXISTS (
                                SELECT 1 FROM events e
                                WHERE e.session_id = cs.session_id
                                  AND e.event_type = 'add_to_cart'
                                  AND e.created_at > now() - make_interval(days => %s)
                            )
                        ) AS add_sessions
                    FROM cohort_sessions cs
                    GROUP BY cs.is_copilot
                ),
                cohort_revenue AS (
                    SELECT
                        cs.is_copilot,
                        COUNT(DISTINCT o.session_id) AS order_sessions,
                        COUNT(o.id) AS order_count,
                        COALESCE(SUM(o.total_idr), 0) AS revenue_idr
                    FROM cohort_sessions cs
                    JOIN orders o ON o.session_id = cs.session_id
                    WHERE o.status = 'paid'
                      AND o.created_at > now() - make_interval(days => %s)
                    GROUP BY cs.is_copilot
                )
                SELECT
                    s.is_copilot,
                    s.sessions,
                    s.add_sessions,
                    COALESCE(r.order_sessions, 0),
                    COALESCE(r.order_count, 0),
                    COALESCE(r.revenue_idr, 0)
                FROM cohort_stats s
                LEFT JOIN cohort_revenue r USING (is_copilot)
                """,
                (since_days, since_days, since_days, since_days),
            )
            cohort_rows = cur.fetchall()

            # ── Copilot quality ──────────────────────────────────────────────
            cur.execute(
                f"""
                SELECT
                    COUNT(*) AS total_turns,
                    COUNT(*) FILTER (WHERE outcome = 'answered') AS answered_turns,
                    COUNT(*) FILTER (WHERE outcome = 'guardrail') AS guardrail_turns,
                    COUNT(*) FILTER (WHERE outcome = 'unanswered') AS unanswered_turns,
                    percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50_latency,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_latency
                FROM copilot_turns WHERE {window}
                """,
                (since_days,),
            )
            (
                total_turns,
                answered_turns,
                guardrail_turns,
                unanswered_turns,
                p50_latency,
                p95_latency,
            ) = cur.fetchone()

            cur.execute(
                f"""
                WITH final_turns AS (
                    SELECT DISTINCT ON (thread_id) thread_id, outcome
                    FROM copilot_turns
                    WHERE thread_id IS NOT NULL
                      AND thread_id <> ''
                      AND {window}
                    ORDER BY thread_id, created_at DESC, id DESC
                )
                SELECT
                    COUNT(*) FILTER (
                        WHERE outcome IN ('answered', 'unanswered')
                    ) AS eligible,
                    COUNT(*) FILTER (WHERE outcome = 'answered') AS answered
                FROM final_turns
                """,
                (since_days,),
            )
            deflection_eligible, deflection_answered = cur.fetchone()

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

    cohorts = {"copilot_sessions": {}, "non_copilot_sessions": {}}
    for (
        is_copilot,
        cohort_session_count,
        add_sessions,
        cohort_order_sessions,
        cohort_order_count,
        cohort_revenue,
    ) in cohort_rows:
        key = "copilot_sessions" if is_copilot else "non_copilot_sessions"
        cohorts[key] = _cohort_block(
            cohort_session_count,
            add_sessions,
            cohort_order_sessions,
            cohort_order_count,
            cohort_revenue,
        )
    for key in ("copilot_sessions", "non_copilot_sessions"):
        cohorts[key].setdefault("sessions", 0)
        cohorts[key].setdefault("add_to_cart_rate", None)
        cohorts[key].setdefault("conversion_rate", None)
        cohorts[key].setdefault("aov_idr", None)
    cohorts["caveat"] = COHORT_CAVEAT

    return {
        "metadata": {
            "since_days": since_days,
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "synthetic": True,
            "environment": "demo",
            "caveat": DEMO_CAVEAT,
        },
        "revenue": {
            "order_count": int(order_count),
            "total_revenue_idr": int(revenue_idr or 0),
            "aov_idr": round(float(aov_idr), 2) if aov_idr is not None else None,
        },
        "funnel": {
            "sessions": int(sessions),
            "view_product_sessions": int(view_sessions),
            "add_to_cart_sessions": int(cart_sessions),
            "checkout_start_sessions": int(checkout_sessions),
            "order_sessions": int(order_sessions),
            "rates": {
                "view_to_cart": _rate(cart_sessions, view_sessions),
                "cart_to_checkout": _rate(checkout_sessions, cart_sessions),
                "checkout_to_order": _rate(order_sessions, checkout_sessions),
                "overall_conversion": _rate(order_sessions, sessions),
            },
        },
        "cohorts": cohorts,
        "search_health": {
            "total_searches": int(searches),
            "zero_result_searches": int(zero_result),
            "zero_result_rate": _rate(zero_result, searches),
            "search_to_pdp_rate": _rate(search_to_pdp_sessions, search_sessions),
            "top_zero_result_queries": top_zero_result_queries,
            "top_queries": top_queries,
        },
        "copilot_quality": {
            "total_turns": int(total_turns),
            "answered_turns": int(answered_turns),
            "guardrail_turns": int(guardrail_turns),
            "unanswered_turns": int(unanswered_turns),
            "unanswered_rate": _rate(unanswered_turns, total_turns),
            "deflection_eligible_conversations": int(deflection_eligible),
            "deflection_answered_conversations": int(deflection_answered),
            "deflection_rate": _rate(deflection_answered, deflection_eligible),
            "latency_p50_ms": round(float(p50_latency), 2) if p50_latency is not None else None,
            "latency_p95_ms": round(float(p95_latency), 2) if p95_latency is not None else None,
            "thumbs_up": int(thumbs_up),
            "thumbs_down": int(thumbs_down),
            "thumbs_up_rate": _rate(thumbs_up, thumbs_up + thumbs_down),
        },
    }
