"""Recommendation endpoints: popular, item-to-item, session-based."""

import logging

from db import get_conn
from fastapi import APIRouter, Query
from psycopg import sql

from routers.catalog import PRODUCT_COLUMNS, row_to_product

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/recs/popular")
def recs_popular(
    department: str | None = None,
    limit: int = Query(10, ge=1, le=24),
):
    """Bestselling / popular products baseline."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            if department and department != "All":
                cur.execute(
                    sql.SQL("SELECT {} FROM products WHERE department = %s ORDER BY rating_count DESC, avg_rating DESC LIMIT %s").format(
                        sql.SQL(PRODUCT_COLUMNS)
                    ),
                    (department, limit),
                )
            else:
                cur.execute(
                    sql.SQL("SELECT {} FROM products ORDER BY rating_count DESC, avg_rating DESC LIMIT %s").format(
                        sql.SQL(PRODUCT_COLUMNS)
                    ),
                    (limit,),
                )
            rows = cur.fetchall()

    items = [row_to_product(row) for row in rows]
    return {
        "items": items,
        "count": len(items),
        "department": department or "All",
        "strategy": "popularity_baseline",
    }


@router.get("/recs/item/{asin}")
def recs_item(
    asin: str,
    limit: int = Query(6, ge=1, le=24),
):
    """Item-to-item recommendations for PDP & Cart."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT recs FROM item_recommendations WHERE asin = %s",
                (asin,),
            )
            row = cur.fetchone()
            rec_asins = row[0] if row and row[0] else []

            strategy = "item_cf"
            items = []

            if rec_asins:
                target_asins = rec_asins[:limit]
                cur.execute(
                    sql.SQL("SELECT {} FROM products WHERE asin = ANY(%s)").format(
                        sql.SQL(PRODUCT_COLUMNS)
                    ),
                    (target_asins,),
                )
                rows = cur.fetchall()
                by_asin = {r[1]: row_to_product(r) for r in rows}
                for a in target_asins:
                    prod = by_asin.get(a)
                    if prod:
                        items.append(prod)

            if len(items) < limit:
                cur.execute(
                    "SELECT department, category FROM products WHERE asin = %s",
                    (asin,),
                )
                source_prod = cur.fetchone()
                dept = source_prod[0] if source_prod else "Men"
                cat = source_prod[1] if source_prod else ""

                cur.execute(
                    sql.SQL(
                        "SELECT {} FROM products"
                        " WHERE asin != %s AND (category = %s OR department = %s)"
                        " ORDER BY rating_count DESC, avg_rating DESC LIMIT %s"
                    ).format(sql.SQL(PRODUCT_COLUMNS)),
                    (asin, cat, dept, limit),
                )
                fb_rows = cur.fetchall()
                seen = {item["asin"] for item in items}
                seen.add(asin)
                for r in fb_rows:
                    fb_prod = row_to_product(r)
                    if fb_prod["asin"] not in seen:
                        items.append(fb_prod)
                        seen.add(fb_prod["asin"])
                        if len(items) >= limit:
                            break
                if not rec_asins:
                    strategy = "category_popularity_fallback"

    return {
        "asin": asin,
        "items": items,
        "count": len(items),
        "strategy": strategy,
    }


@router.get("/recs/session")
def recs_session(
    session_id: str = Query(min_length=1, max_length=128),
    limit: int = Query(6, ge=1, le=24),
):
    """Session-based recommendations."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT asin FROM events
                WHERE session_id = %s
                  AND event_type IN ('view_product', 'add_to_cart')
                  AND asin IS NOT NULL
                ORDER BY id DESC
                LIMIT 5
                """,
                (session_id,),
            )
            seen_asins = [r[0] for r in cur.fetchall()]

            if not seen_asins:
                cur.execute(
                    sql.SQL(
                        "SELECT {} FROM products ORDER BY rating_count DESC, avg_rating DESC LIMIT %s"
                    ).format(sql.SQL(PRODUCT_COLUMNS)),
                    (limit,),
                )
                items = [row_to_product(r) for r in cur.fetchall()]
                return {
                    "session_id": session_id,
                    "items": items,
                    "count": len(items),
                    "strategy": "cold_popularity",
                }

            cur.execute(
                """
                SELECT asin, recs FROM item_recommendations
                WHERE asin = ANY(%s)
                """,
                (seen_asins,),
            )
            recs_by_asin = {r[0]: r[1] for r in cur.fetchall()}

            seen_set = set(seen_asins)
            candidate_asins: list[str] = []
            cand_seen: set[str] = set()

            for viewed in seen_asins:
                for target in recs_by_asin.get(viewed, []):
                    if target not in seen_set and target not in cand_seen:
                        candidate_asins.append(target)
                        cand_seen.add(target)
                        if len(candidate_asins) >= limit * 3:
                            break
                if len(candidate_asins) >= limit * 3:
                    break

            cur.execute(
                sql.SQL("SELECT {} FROM products WHERE asin = ANY(%s)").format(
                    sql.SQL(PRODUCT_COLUMNS)
                ),
                (candidate_asins,) if candidate_asins else ([],),
            )
            rows = cur.fetchall()
            by_asin = {r[1]: row_to_product(r) for r in rows}
            items = [by_asin[a] for a in candidate_asins if a in by_asin][:limit]

            if len(items) < limit:
                exclude = list(seen_set.union(cand_seen).union(it["asin"] for it in items))
                cur.execute(
                    """
                    SELECT asin FROM products
                    WHERE asin != ALL(%s)
                    ORDER BY rating_count DESC
                    LIMIT %s
                    """,
                    (exclude, limit - len(items)),
                )
                for r in cur.fetchall():
                    candidate_asins.append(r[0])

                backfill_asins = [a for a in candidate_asins if a not in by_asin]
                if backfill_asins:
                    cur.execute(
                        sql.SQL("SELECT {} FROM products WHERE asin = ANY(%s)").format(
                            sql.SQL(PRODUCT_COLUMNS)
                        ),
                        (backfill_asins,),
                    )
                    for row in cur.fetchall():
                        p = row_to_product(row)
                        if p["asin"] not in {it["asin"] for it in items}:
                            items.append(p)
                            if len(items) >= limit:
                                break

    return {
        "session_id": session_id,
        "items": items,
        "count": len(items),
        "strategy": "session_cf",
    }
