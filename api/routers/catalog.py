"""Catalog endpoints: categories, product listing, product detail.

All product SQL lives in :mod:`catalog_repo`; this module only does HTTP concerns
(status codes, cache headers, pagination echo).
"""

import catalog_repo
from fastapi import APIRouter, HTTPException, Query, Response

# Backwards-compatible names: older modules/tests import these from here.
PRODUCT_COLUMNS = catalog_repo.PRODUCT_COLUMNS
row_to_product = catalog_repo.product_from_row
invalidate_categories_cache = catalog_repo.invalidate_cache

router = APIRouter()


@router.get("/categories")
def categories(response: Response):
    """Facets for the shop filter chips — cached in-memory (TTL 5m) and on HTTP clients."""
    response.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=60"
    return catalog_repo.facet_counts()


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

    with catalog_repo.get_conn() as conn:
        with conn.cursor() as cur:
            items, total = catalog_repo.fetch_page(
                cur,
                department=department,
                category=category,
                limit=limit,
                offset=offset,
            )

    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/products/{product_id}")
def get_product(product_id: int, response: Response):
    response.headers["Cache-Control"] = "public, max-age=300, s-maxage=600, stale-while-revalidate=60"

    with catalog_repo.get_conn() as conn:
        with conn.cursor() as cur:
            product = catalog_repo.fetch_by_id(cur, product_id)

    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return product
