"""Product data access: the single place that knows the product schema.

Everything that reads a row from ``products`` and wants the storefront JSON
shape goes through this module.  Before it existed, the column list and the
positional row-to-dict mapping lived in ``routers/catalog.py`` and were imported
*upward* by ``agent_tools`` (a domain module importing the web layer), while
``routers/search.py``, ``routers/recs.py`` and ``agent_tools.py`` each repeated
the same ``SELECT ... WHERE id = ANY(...)`` hydration SQL.

Layering
    config/db -> catalog_repo -> services (retrieval, agent_tools) -> routers

All ``fetch_*`` helpers take an open cursor so the caller owns the transaction
and connection lifetime (pool or one-shot).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from db import get_conn
from psycopg import sql

logger = logging.getLogger(__name__)

# Every column ``product_from_row`` reads, in row order.
COLUMNS: tuple[str, ...] = (
    "id",
    "asin",
    "title",
    "brand",
    "price_usd",
    "price_idr",
    "department",
    "category",
    "category_path",
    "description",
    "features",
    "image_url",
    "avg_rating",
    "rating_count",
    "also_buy",
    "also_view",
)

# Interpolated into SQL; the names above are trusted constants.
PRODUCT_COLUMNS = ", ".join(COLUMNS)


# ── Row mapping ──────────────────────────────────────────────────────────────


def product_from_row(row: tuple) -> dict[str, Any]:
    """One DB row (in ``COLUMNS`` order) -> the JSON shape the storefront consumes.

    camelCase keys, prices as numbers, NULL list columns as empty lists.
    """
    (
        product_id,
        asin,
        title,
        brand,
        price_usd,
        price_idr,
        department,
        category,
        category_path,
        description,
        features,
        image_url,
        avg_rating,
        rating_count,
        also_buy,
        also_view,
    ) = row
    return {
        "id": product_id,
        "asin": asin,
        "title": title,
        "brand": brand,
        "priceUsd": float(price_usd) if price_usd is not None else None,
        "priceIdr": price_idr,
        "department": department,
        "category": category,
        "categoryPath": category_path,
        "description": description,
        "features": features if isinstance(features, list) else [],
        "imageUrl": image_url,
        "avgRating": float(avg_rating) if avg_rating is not None else None,
        "ratingCount": rating_count,
        "alsoBuy": also_buy if isinstance(also_buy, list) else [],
        "alsoView": also_view if isinstance(also_view, list) else [],
    }


# ── Query building ───────────────────────────────────────────────────────────


def select_products(*, where: sql.Composable | None = None) -> sql.Composable:
    """``SELECT <product columns> FROM products [WHERE ...]``."""
    query = sql.SQL("SELECT {} FROM products").format(sql.SQL(PRODUCT_COLUMNS))
    if where is not None:
        query = query + sql.SQL(" WHERE ") + where
    return query


def equality_filters(**values: str | None) -> tuple[list[sql.Composable], list[Any]]:
    """Turn ``{"department": "Men", "category": None}`` into SQL fragments + params."""
    fragments: list[sql.Composable] = []
    params: list[Any] = []
    for column, value in values.items():
        if value:
            fragments.append(sql.SQL("{} = %s").format(sql.Identifier(column)))
            params.append(value)
    return fragments, params


# ── Reads ────────────────────────────────────────────────────────────────────


def fetch_page(
    cur,
    *,
    department: str | None = None,
    category: str | None = None,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    """One catalog page plus the total row count for the same filters."""
    fragments, params = equality_filters(department=department, category=category)
    where = sql.SQL(" AND ").join(fragments) if fragments else None

    cur.execute(
        sql.SQL("SELECT COUNT(*) FROM products") + (sql.SQL(" WHERE ") + where if where is not None else sql.SQL("")),
        params,
    )
    total = cur.fetchone()[0]

    cur.execute(
        select_products(where=where) + sql.SQL(" ORDER BY rating_count DESC, id LIMIT %s OFFSET %s"),
        [*params, limit, offset],
    )
    return [product_from_row(row) for row in cur.fetchall()], total


def fetch_by_id(cur, product_id: int) -> dict[str, Any] | None:
    cur.execute(select_products(where=sql.SQL("id = %s")), (product_id,))
    row = cur.fetchone()
    return product_from_row(row) if row else None


def fetch_by_ids(cur, ids: list[int]) -> dict[int, dict[str, Any]]:
    """Hydrate many products by id. Returns ``{id: product}`` (order not guaranteed)."""
    if not ids:
        return {}
    cur.execute(select_products(where=sql.SQL("id = ANY(%s)")), (list(ids),))
    return {row[0]: product_from_row(row) for row in cur.fetchall()}


def fetch_by_id_or_asin(cur, asin_or_id: str | int) -> dict[str, Any] | None:
    """Product by numeric id or ASIN, matching what ``agent_tools`` accepts."""
    if isinstance(asin_or_id, int) or (isinstance(asin_or_id, str) and asin_or_id.isdigit()):
        return fetch_by_id(cur, int(asin_or_id))
    cur.execute(select_products(where=sql.SQL("asin = %s")), (str(asin_or_id),))
    row = cur.fetchone()
    return product_from_row(row) if row else None


def fetch_by_asin(cur, asin: str) -> dict[str, Any] | None:
    cur.execute(select_products(where=sql.SQL("asin = %s")), (asin,))
    row = cur.fetchone()
    return product_from_row(row) if row else None


def fetch_by_asins(cur, asins: list[str]) -> dict[str, dict[str, Any]]:
    """Hydrate many products by ASIN. Returns ``{asin: product}``."""
    if not asins:
        return {}
    cur.execute(select_products(where=sql.SQL("asin = ANY(%s)")), (list(asins),))
    return {row[1]: product_from_row(row) for row in cur.fetchall()}


def fetch_brief(cur, asin: str) -> dict[str, Any] | None:
    """The four fields chat context blocks need, without the long text columns."""
    cur.execute(
        "SELECT asin, title, brand, price_idr FROM products WHERE asin = %s",
        (asin,),
    )
    row = cur.fetchone()
    if not row:
        return None
    return {"asin": row[0], "title": row[1], "brand": row[2], "priceIdr": row[3]}


# ── Facets (cached) ──────────────────────────────────────────────────────────

# One cache for both the filter chips (``facet_counts``) and the tool-description
# category enum (``category_names``); a reseed invalidates both together.
_CACHE_TTL_SECONDS = 300.0
_facets: dict[str, Any] | None = None
_facets_at: float = 0.0
_categories: list[str] | None = None
_categories_at: float = 0.0


def invalidate_cache() -> None:
    """Drop cached facets/categories (called after a catalog reseed/reindex)."""
    global _facets, _facets_at, _categories, _categories_at
    _facets = None
    _facets_at = 0.0
    _categories = None
    _categories_at = 0.0


def facet_counts() -> dict[str, list[dict[str, Any]]]:
    """Departments + top categories with product counts (cache: 5 minutes)."""
    global _facets, _facets_at

    now = time.time()
    if _facets is not None and (now - _facets_at) < _CACHE_TTL_SECONDS:
        return _facets

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
            categories = [{"name": name, "count": count} for name, count in cur.fetchall()]

    _facets = {"departments": departments, "categories": categories}
    _facets_at = now
    return _facets


def category_names() -> list[str]:
    """Distinct non-empty categories, used to constrain the copilot tool enum."""
    global _categories, _categories_at

    now = time.time()
    if _categories is not None and (now - _categories_at) < _CACHE_TTL_SECONDS:
        return _categories

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT DISTINCT category FROM products WHERE category <> '' ORDER BY category")
                names = [row[0] for row in cur.fetchall()]
    except Exception as e:
        # A missing catalog must not stop the agent from being built.
        logger.warning("catalog category lookup failed: %s", e)
        return []

    _categories = names
    _categories_at = now
    return names
