"""No-LLM sizing endpoints for the product page.

``GET /sizing/{asin}`` explains what the category needs. ``POST
/sizing/recommend`` runs the same deterministic core as the copilot tool, so a
shopper can get honest size guidance without opening chat.
"""

import logging

from db import get_conn
from fastapi import APIRouter, HTTPException, Request
from rate_limiter import check_write_rate_limit
from schemas import SizingRecommendIn
from sizing import recommend_size_core, size_kind_for, sizing_info_for

from routers.copilot import _get_client_ip

logger = logging.getLogger(__name__)

router = APIRouter()


def _load_product(asin: str) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT asin, title, brand, department, category FROM products WHERE asin = %s",
                (asin,),
            )
            row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"unknown asin: {asin}")
    return {
        "asin": row[0],
        "title": row[1],
        "brand": row[2],
        "department": row[3],
        "category": row[4],
    }


@router.get("/sizing/{asin}")
def sizing_for_product(asin: str):
    """What measurement this product's category needs (or why sizing is unsupported)."""
    product = _load_product(asin)
    info = sizing_info_for(product["department"], product["category"], product["title"])
    return {"asin": product["asin"], **info}


@router.post("/sizing/recommend")
def sizing_recommend(payload: SizingRecommendIn, request: Request):
    """Deterministic size advice from body measurements; never guesses."""
    allowed, retry_after = check_write_rate_limit(_get_client_ip(request))
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many sizing requests. Please try again shortly.",
            headers={"Retry-After": str(retry_after)},
        )

    product = _load_product(payload.asin)
    kind = size_kind_for(product["department"], product["category"], product["title"])
    result = recommend_size_core(
        height_cm=payload.height_cm,
        weight_kg=payload.weight_kg,
        foot_length_cm=payload.foot_length_cm,
        waist_cm=payload.waist_cm,
        head_circumference_cm=payload.head_circumference_cm,
        size_kind=kind,
        brand=product["brand"],
        department=product["department"],
        category=product["category"],
        title=product["title"],
    )
    return {"asin": product["asin"], **result}
