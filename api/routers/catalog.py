"""Catalog endpoints: categories, product listing, product detail."""

import logging
import time

from db import get_conn
from fastapi import APIRouter, HTTPException, Query, Response
from psycopg import sql

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Shared helpers ───────────────────────────────────────────────────────────

SCHEMA_COLUMNS = [
    "id", "asin", "title", "brand", "price_usd", "price_idr",
    "department", "category", "category_path", "description",
    "features", "image_url", "avg_rating", "rating_count",
    "also_buy", "also_view",
]

PRODUCT_COLUMNS = ", ".join(SCHEMA_COLUMNS)


def row_to_product(row):
    """One DB row -> the JSON shape the storefront consumes (camelCase, prices as numbers)."""
    return {
        "id": row[0],
        "asin": row[1],
        "title": row[2],
        "brand": row[3],
        "priceUsd": float(row[4]) if row[4] is not None else None,
        "priceIdr": row[5],
        "department": row[6],
        "category": row[7],
        "categoryPath": row[8],
        "description": row[9],
        "features": row[10],
        "imageUrl": row[11],
        "avgRating": float(row[12]) if row[12] is not None else None,
        "ratingCount": row[13],
        "alsoBuy": row[14],
        "alsoView": row[15],
    }


# ── Categories (facets) ─────────────────────────────────────────────────────

_CATEGORIES_CACHE: dict = {"timestamp": 0.0, "data": None}
_CATEGORIES_TTL_SECONDS: float = 300.0


def invalidate_categories_cache() -> None:
    """Called by the reindex endpoint after a reseed."""
    _CATEGORIES_CACHE["data"] = None


@router.get("/categories")
def categories(response: Response):
    """Facets for the shop filter chips — cached in-memory (TTL 5m) and on HTTP clients."""
    response.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=60"

    now = time.time()
    if _CATEGORIES_CACHE["data"] is not None and (now - _CATEGORIES_CACHE["timestamp"]) < _CATEGORIES_TTL_SECONDS:
        return _CATEGORIES_CACHE["data"]

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT department, COUNT(*) FROM products
                GROUP BY department ORDER BY COUNT(*) DESC, department
                """
            )
            departments = [{"name": name, "count": count} for name, count in cur.fetchall()]
            cur.execute(
                """
                SELECT category, COUNT(*) FROM products
                WHERE category <> '' GROUP BY category
                ORDER BY COUNT(*) DESC, category LIMIT 24
                """
            )
            top_categories = [{"name": name, "count": count} for name, count in cur.fetchall()]

    data = {"departments": departments, "categories": top_categories}
    _CATEGORIES_CACHE["timestamp"] = now
    _CATEGORIES_CACHE["data"] = data
    return data


# ── Product list ─────────────────────────────────────────────────────────────

@router.get("/products")
def products(
    response: Response,
    limit: int = Query(24, ge=1, le=24),
    offset: int = Query(0, ge=0),
    department: str | None = None,
    category: str | None = None,
):
    """Paginated catalog. ``department`` and ``category`` are exact-match facets."""
    response.headers["Cache-Control"] = "public, max-age=60, s-maxage=300, stale-while-revalidate=60"

    filters: list[sql.Composable] = []
    params: list = []
    if department:
        filters.append(sql.SQL("department = %s"))
        params.append(department)
    if category:
        filters.append(sql.SQL("category = %s"))
        params.append(category)

    where = sql.SQL(" WHERE ") + sql.SQL(" AND ").join(filters) if filters else sql.SQL("")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("SELECT COUNT(*) FROM products") + where,
                params,
            )
            total = cur.fetchone()[0]
            cur.execute(
                sql.SQL("SELECT {} FROM products").format(sql.SQL(PRODUCT_COLUMNS))
                + where
                + sql.SQL(" ORDER BY rating_count DESC, id LIMIT %s OFFSET %s"),
                params + [limit, offset],
            )
            rows = cur.fetchall()

    return {
        "items": [row_to_product(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# ── Single product ───────────────────────────────────────────────────────────

@router.get("/products/{product_id}")
def get_product(product_id: int, response: Response):
    response.headers["Cache-Control"] = "public, max-age=300, s-maxage=600, stale-while-revalidate=60"

    from fit import get_product_fit

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("SELECT {} FROM products WHERE id = %s").format(
                    sql.SQL(PRODUCT_COLUMNS)
                ),
                (product_id,),
            )
            row = cur.fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Product not found")
            product = row_to_product(row)
            product["fit"] = get_product_fit(cur, product["asin"], product["brand"])

    return product


@router.get("/products/{product_id}/fit")
def get_product_fit_endpoint(product_id: int, response: Response):
    """Return just the review-derived fit signal for a product (or null).

    Separate from the product detail so the storefront can lazy-load the fit
    badge without re-fetching the whole product, and so copilot product cards
    can be enriched on demand.
    """
    response.headers["Cache-Control"] = "public, max-age=300, s-maxage=600, stale-while-revalidate=60"

    from fit import get_product_fit

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT asin, brand FROM products WHERE id = %s",
                (product_id,),
            )
            row = cur.fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Product not found")
            fit = get_product_fit(cur, row[0], row[1])

    return {"id": product_id, "asin": row[0], "fit": fit}
