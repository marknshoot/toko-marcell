"""Recommendation endpoints: popular, item-to-item, session-based.

The recommendation *strategy* lives here; reading products and turning rows into
the storefront shape lives in :mod:`catalog_repo`.
"""

import catalog_repo
from fastapi import APIRouter, Query
from psycopg import sql

router = APIRouter()

_POPULAR_ORDER = sql.SQL(" ORDER BY rating_count DESC, avg_rating DESC")


def _popular(cur, *, limit: int, department: str | None = None, exclude: list[str] | None = None):
    """Best-rated products, optionally scoped to one department and ASIN blacklist."""
    where: sql.Composable | None = None
    params: list = []
    if department and department != "All":
        where = sql.SQL("department = %s")
        params.append(department)
    if exclude:
        clause = sql.SQL("asin <> ALL(%s)")
        where = clause if where is None else where + sql.SQL(" AND ") + clause
        params.append(exclude)

    cur.execute(
        catalog_repo.select_products(where=where) + _POPULAR_ORDER + sql.SQL(" LIMIT %s"),
        [*params, limit],
    )
    return [catalog_repo.product_from_row(row) for row in cur.fetchall()]


def _hydrate_in_order(cur, asins: list[str]) -> list[dict]:
    """Products for ``asins`` in the order given, skipping missing ones."""
    by_asin = catalog_repo.fetch_by_asins(cur, asins)
    return [by_asin[asin] for asin in asins if asin in by_asin]


@router.get("/recs/popular")
def recs_popular(department: str | None = None, limit: int = Query(10, ge=1, le=24)):
    """Bestselling / popular products baseline."""
    with catalog_repo.get_conn() as conn:
        with conn.cursor() as cur:
            items = _popular(cur, limit=limit, department=department)

    return {
        "items": items,
        "count": len(items),
        "department": department or "All",
        "strategy": "popularity_baseline",
    }


@router.get("/recs/item/{asin}")
def recs_item(asin: str, limit: int = Query(6, ge=1, le=24)):
    """Item-to-item recommendations for PDP & Cart."""
    with catalog_repo.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT recs FROM item_recommendations WHERE asin = %s", (asin,))
            row = cur.fetchone()
            rec_asins = (row[0] if row and row[0] else [])[:limit]

            items = _hydrate_in_order(cur, rec_asins)
            strategy = "item_cf"

            if len(items) < limit:
                # Collaborative filtering ran dry: fall back to the same category,
                # then the same department, skipping what we already have.
                cur.execute("SELECT department, category FROM products WHERE asin = %s", (asin,))
                source = cur.fetchone()
                department = source[0] if source else "Men"
                category = source[1] if source else ""

                cur.execute(
                    catalog_repo.select_products(where=sql.SQL("asin != %s AND (category = %s OR department = %s)"))
                    + _POPULAR_ORDER
                    + sql.SQL(" LIMIT %s"),
                    (asin, category, department, limit),
                )
                seen = {asin} | {item["asin"] for item in items}
                for row in cur.fetchall():
                    product = catalog_repo.product_from_row(row)
                    if product["asin"] in seen:
                        continue
                    seen.add(product["asin"])
                    items.append(product)
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
    """Session-based recommendations from the products this session looked at."""
    with catalog_repo.get_conn() as conn:
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
            seen_asins = [row[0] for row in cur.fetchall()]

            if not seen_asins:
                items = _popular(cur, limit=limit)
                return {
                    "session_id": session_id,
                    "items": items,
                    "count": len(items),
                    "strategy": "cold_popularity",
                }

            cur.execute(
                "SELECT asin, recs FROM item_recommendations WHERE asin = ANY(%s)",
                (seen_asins,),
            )
            recs_by_asin = {row[0]: row[1] or [] for row in cur.fetchall()}

            excluded = set(seen_asins)
            candidate_asins: list[str] = []
            for viewed in seen_asins:
                for target in recs_by_asin.get(viewed, []):
                    if target not in excluded:
                        excluded.add(target)
                        candidate_asins.append(target)
                        if len(candidate_asins) >= limit * 3:
                            break
                if len(candidate_asins) >= limit * 3:
                    break

            items = _hydrate_in_order(cur, candidate_asins)[:limit]

            if len(items) < limit:
                items += _popular(cur, limit=limit - len(items), exclude=sorted(excluded))

    return {
        "session_id": session_id,
        "items": items,
        "count": len(items),
        "strategy": "session_cf",
    }
