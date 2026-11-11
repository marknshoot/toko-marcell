"""Shared KPI queries for the analytics endpoints.

``GET /events/summary`` and ``GET /admin/kpi`` answer overlapping questions about
the same tables (search health, copilot quality, unit economics, assisted
orders).  That SQL used to be copy-pasted in both routers, which meant two places
to fix and two numbers that could silently drift.

Every function here takes an open cursor and a day window and returns raw values
(no response shaping); the routers decide which keys to expose.  ``None`` is used
for "unknown" — a rate with no denominator is not 0.
"""

from __future__ import annotations

from typing import Any

# Predicate every query interpolates for its window. Parameterised, so user input
# never reaches the SQL text; the days value still binds as a normal parameter.
WINDOW = "created_at > now() - make_interval(days => %s)"


# ── Numbers ──────────────────────────────────────────────────────────────────


def rate(numerator: int | float | None, denominator: int | float | None) -> float | None:
    """A 4-decimal ratio, or ``None`` when there is no denominator yet."""
    if not denominator:
        return None
    return round((numerator or 0) / denominator, 4)


def cost_rate(total_cost_usd: float | None, denominator: int | None) -> float | None:
    """Money-safe division: unknown cost stays ``None``, 8 decimals otherwise."""
    if total_cost_usd is None or not denominator:
        return None
    return round(float(total_cost_usd) / denominator, 8)


def percent_or_none(value: Any) -> float | None:
    """Round a SQL aggregate to 2 decimals, keeping SQL NULL as ``None``."""
    return round(float(value), 2) if value is not None else None


# ── Search ───────────────────────────────────────────────────────────────────


def search_health(cur, since_days: int, *, top_queries: bool = True) -> dict[str, Any]:
    """Search volume, zero-result rate, session-level search→PDP and top queries."""
    cur.execute(
        f"""
        SELECT COUNT(*), COUNT(*) FILTER (WHERE results_count = 0)
        FROM events WHERE event_type = 'search' AND {WINDOW}
        """,
        (since_days,),
    )
    searches, zero_result = cur.fetchone()

    cur.execute(
        f"""
        SELECT COUNT(DISTINCT session_id)
        FROM events
        WHERE event_type = 'search' AND session_id IS NOT NULL AND {WINDOW}
        """,
        (since_days,),
    )
    search_sessions = cur.fetchone()[0]

    cur.execute(
        f"""
        SELECT COUNT(*) FROM (
            SELECT session_id FROM events
            WHERE event_type = 'search' AND {WINDOW}
            INTERSECT
            SELECT session_id FROM events
            WHERE event_type = 'view_product' AND {WINDOW}
        ) AS searched_and_viewed
        """,
        (since_days, since_days),
    )
    search_to_pdp_sessions = cur.fetchone()[0]

    result: dict[str, Any] = {
        "total_searches": int(searches),
        "zero_result_searches": int(zero_result),
        "zero_result_rate": rate(zero_result, searches),
        "search_sessions": int(search_sessions),
        "search_to_pdp_sessions": int(search_to_pdp_sessions),
        "search_to_pdp_rate": rate(search_to_pdp_sessions, search_sessions),
        "top_zero_result_queries": [],
        "top_queries": [],
    }
    if not top_queries:
        return result

    cur.execute(
        f"""
        SELECT query, COUNT(*) AS count
        FROM events
        WHERE event_type = 'search' AND {WINDOW}
          AND results_count = 0
          AND query IS NOT NULL AND btrim(query) <> ''
        GROUP BY query
        ORDER BY COUNT(*) DESC, query ASC
        LIMIT 20
        """,
        (since_days,),
    )
    result["top_zero_result_queries"] = [{"query": query, "count": int(count)} for query, count in cur.fetchall()]

    cur.execute(
        f"""
        SELECT query, COUNT(*) AS count,
               COALESCE(BOOL_OR(results_count = 0), FALSE) AS zero_result
        FROM events
        WHERE event_type = 'search' AND {WINDOW}
          AND query IS NOT NULL AND btrim(query) <> ''
        GROUP BY query
        ORDER BY COUNT(*) DESC, query ASC
        LIMIT 20
        """,
        (since_days,),
    )
    result["top_queries"] = [
        {"query": query, "count": int(count), "zero_result": bool(zero_result_flag)}
        for query, count, zero_result_flag in cur.fetchall()
    ]
    return result


# ── Events / orders ──────────────────────────────────────────────────────────


def event_funnel(cur, since_days: int) -> dict[str, Any]:
    """Event counts and distinct sessions per event type."""
    cur.execute(
        f"""
        SELECT event_type, COUNT(*), COUNT(DISTINCT session_id)
        FROM events WHERE {WINDOW}
        GROUP BY event_type
        """,
        (since_days,),
    )
    by_type: dict[str, int] = {}
    sessions_by_step: dict[str, int] = {}
    events_total = 0
    for event_type, count, sessions in cur.fetchall():
        by_type[event_type] = int(count)
        sessions_by_step[event_type] = int(sessions)
        events_total += int(count)
    return {
        "events_total": events_total,
        "by_type": by_type,
        "sessions_by_step": sessions_by_step,
    }


def order_revenue(cur, since_days: int) -> dict[str, Any]:
    """Paid-order count, revenue, AOV and distinct purchasing sessions."""
    cur.execute(
        f"""
        SELECT
            COUNT(*) FILTER (WHERE status = 'paid') AS order_count,
            COALESCE(SUM(total_idr) FILTER (WHERE status = 'paid'), 0) AS revenue_idr,
            AVG(total_idr) FILTER (WHERE status = 'paid') AS aov_idr,
            COUNT(DISTINCT session_id) FILTER (
                WHERE status = 'paid' AND session_id IS NOT NULL
            ) AS order_sessions
        FROM orders WHERE {WINDOW}
        """,
        (since_days,),
    )
    order_count, revenue_idr, aov_idr, order_sessions = cur.fetchone()
    return {
        "order_count": int(order_count),
        "revenue_idr": int(revenue_idr or 0),
        "aov_idr": percent_or_none(aov_idr),
        "order_sessions": int(order_sessions),
    }


# ── Copilot quality ──────────────────────────────────────────────────────────


def copilot_turn_stats(cur, since_days: int) -> dict[str, Any]:
    """One row of turn-level aggregates: outcomes, latency percentiles, cost."""
    cur.execute(
        f"""
        SELECT
            COUNT(*) AS total_turns,
            COUNT(*) FILTER (WHERE outcome = 'answered') AS answered_turns,
            COUNT(*) FILTER (WHERE outcome = 'guardrail') AS guardrail_turns,
            COUNT(*) FILTER (WHERE outcome = 'unanswered') AS unanswered_turns,
            COUNT(*) FILTER (WHERE is_retry) AS retry_turns,
            COUNT(*) FILTER (
                WHERE is_retry AND outcome IN ('answered', 'cart')
            ) AS retry_successes,
            AVG(latency_ms) AS avg_latency_ms,
            percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50_latency_ms,
            percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_latency_ms,
            SUM(cost_usd) AS total_cost_usd,
            COUNT(DISTINCT thread_id) AS conversations,
            COUNT(DISTINCT thread_id) FILTER (
                WHERE outcome IN ('answered', 'cart')
            ) AS resolved_conversations
        FROM copilot_turns WHERE {WINDOW}
        """,
        (since_days,),
    )
    (
        total_turns,
        answered_turns,
        guardrail_turns,
        unanswered_turns,
        retry_turns,
        retry_successes,
        avg_latency,
        p50_latency,
        p95_latency,
        total_cost,
        conversations,
        resolved_conversations,
    ) = cur.fetchone()
    return {
        "total_turns": int(total_turns),
        "answered_turns": int(answered_turns),
        "guardrail_turns": int(guardrail_turns),
        "unanswered_turns": int(unanswered_turns),
        "retry_turns": int(retry_turns),
        "retry_successes": int(retry_successes),
        "total_cost_usd": float(total_cost) if total_cost is not None else None,
        "conversations": int(conversations),
        "resolved_conversations": int(resolved_conversations),
        "avg_latency_ms": percent_or_none(avg_latency),
        "p50_latency_ms": percent_or_none(p50_latency),
        "p95_latency_ms": percent_or_none(p95_latency),
    }


def copilot_feedback_stats(cur, since_days: int) -> dict[str, Any]:
    cur.execute(
        f"""
        SELECT
            COUNT(*) FILTER (WHERE rating = 1) AS thumbs_up,
            COUNT(*) FILTER (WHERE rating = -1) AS thumbs_down
        FROM copilot_feedback WHERE {WINDOW}
        """,
        (since_days,),
    )
    thumbs_up, thumbs_down = cur.fetchone()
    return {"thumbs_up": int(thumbs_up), "thumbs_down": int(thumbs_down)}


def copilot_assisted_orders(cur, since_days: int) -> int:
    """Orders whose session also produced a copilot turn inside the window."""
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
    return int(cur.fetchone()[0])


def copilot_deflection(cur, since_days: int) -> dict[str, Any]:
    """Last-turn-per-conversation outcome: answered vs unanswered vs cart/guardrail."""
    cur.execute(
        f"""
        WITH final_turns AS (
            SELECT DISTINCT ON (thread_id) thread_id, outcome
            FROM copilot_turns
            WHERE thread_id IS NOT NULL
              AND thread_id <> ''
              AND {WINDOW}
            ORDER BY thread_id, created_at DESC, id DESC
        )
        SELECT
            COUNT(*) FILTER (WHERE outcome IN ('answered', 'unanswered')) AS eligible,
            COUNT(*) FILTER (WHERE outcome = 'answered') AS answered
        FROM final_turns
        """,
        (since_days,),
    )
    eligible, answered = cur.fetchone()
    return {
        "eligible_conversations": int(eligible),
        "answered_conversations": int(answered),
        "deflection_rate": rate(answered, eligible),
    }


# ── Cohorts ──────────────────────────────────────────────────────────────────


def copilot_cohorts(cur, since_days: int) -> list[tuple[bool, int, int, int, int, int]]:
    """Per cohort (copilot / everyone else): sessions, add-to-cart, orders, revenue.

    Returns ``(is_copilot, sessions, add_to_cart_sessions, order_sessions,
    order_count, revenue_idr)`` rows.  Observational, not a holdout experiment.
    """
    cur.execute(
        f"""
        WITH copilot_sessions AS (
            SELECT DISTINCT session_id FROM events
            WHERE event_type = 'copilot_message' AND {WINDOW}
        ),
        all_sessions AS (
            SELECT DISTINCT session_id FROM events WHERE {WINDOW}
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
    return [tuple(row) for row in cur.fetchall()]


def session_cohorts_by_purchase(cur, since_days: int) -> tuple[list[tuple], str]:
    """Event-level cohorts: sessions with an add-to-cart and with a purchase.

    ``/events/summary`` predates the ``orders`` table, so it keeps accepting
    ``purchase_mock`` events when ``orders.session_id`` does not exist yet.  The
    returned ``purchase_source`` says which table was used.
    """
    cur.execute(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'orders' AND column_name = 'session_id'
        )
        """
    )
    if cur.fetchone()[0]:
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
            WHERE event_type = 'copilot_message' AND {WINDOW}
        ),
        all_sessions AS (
            SELECT DISTINCT session_id FROM events WHERE {WINDOW}
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
    return [tuple(row) for row in cur.fetchall()], purchase_source
