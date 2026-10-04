"""Review endpoints."""

import logging

from db import get_conn
from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)

router = APIRouter()


def _fetch_reviews_for_asin(cur, asin: str, limit: int = 10):
    cur.execute(
        """
        SELECT
            count(*),
            coalesce(avg(rating), 0),
            count(*) FILTER (WHERE round(rating) = 5),
            count(*) FILTER (WHERE round(rating) = 4),
            count(*) FILTER (WHERE round(rating) = 3),
            count(*) FILTER (WHERE round(rating) = 2),
            count(*) FILTER (WHERE round(rating) = 1)
        FROM reviews
        WHERE asin = %s
        """,
        (asin,),
    )
    row = cur.fetchone()
    total = row[0]
    avg_rating = round(float(row[1]), 1) if row and row[0] > 0 else None
    breakdown = {
        "5": row[2] if row else 0,
        "4": row[3] if row else 0,
        "3": row[4] if row else 0,
        "2": row[5] if row else 0,
        "1": row[6] if row else 0,
    }

    cur.execute(
        """
        SELECT id, asin, rating, summary, comment, author, verified, review_date, created_at
        FROM reviews
        WHERE asin = %s
        ORDER BY id ASC
        LIMIT %s
        """,
        (asin, limit),
    )
    reviews = []
    for r in cur.fetchall():
        reviews.append({
            "id": r[0],
            "asin": r[1],
            "rating": float(r[2]),
            "summary": r[3] or "",
            "comment": r[4],
            "author": r[5],
            "verified": r[6],
            "reviewDate": r[7],
            "createdAt": r[8].isoformat() if r[8] else None,
        })

    return {
        "asin": asin,
        "total": total,
        "averageRating": avg_rating,
        "breakdown": breakdown,
        "reviews": reviews,
    }


@router.get("/products/{product_id}/reviews")
def get_product_reviews(product_id: int, limit: int = Query(10, ge=1, le=50)):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT asin FROM products WHERE id = %s", (product_id,))
            row = cur.fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Product not found")
            return _fetch_reviews_for_asin(cur, row[0], limit=limit)


@router.get("/reviews/{asin}")
def get_asin_reviews(asin: str, limit: int = Query(10, ge=1, le=50)):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM products WHERE asin = %s", (asin,))
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="Product not found")
            return _fetch_reviews_for_asin(cur, asin, limit=limit)
