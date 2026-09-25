"""
Deterministic Grounding Tools for Toko Marcell AI Copilot.

All tools access authoritative database tables (products, reviews, orders, store_knowledge)
with zero hallucination, strict price handling (IDR), and robust error handling.
"""

import base64
import os
import re
import urllib.request
from typing import Any

import psycopg

from reranker import rerank
from search import (
    embed_image_bytes,
    embed_query,
    get_index,
    reciprocal_rank_fusion,
    tokenize,
)

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://toko:toko@localhost:5432/toko")

PRODUCT_COLS = (
    "id, asin, title, brand, price_usd, price_idr, department, category, "
    "category_path, description, features, image_url, avg_rating, rating_count, "
    "also_buy, also_view"
)


def _row_to_dict(row: tuple) -> dict[str, Any]:
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
        "features": row[10] if isinstance(row[10], list) else [],
        "imageUrl": row[11],
        "avgRating": float(row[12]) if row[12] is not None else None,
        "ratingCount": row[13],
        "alsoBuy": row[14] if isinstance(row[14], list) else [],
        "alsoView": row[15] if isinstance(row[15], list) else [],
    }


def search_catalog(
    query: str,
    category: str | None = None,
    department: str | None = None,
    price_max: int | None = None,
    limit: int = 4,
    db_url: str | None = None,
) -> list[dict[str, Any]]:
    """Hybrid BM25 + pgvector search with Stage 2 Cross-Encoder reranking.

    Supports department ('Men', 'Women'), category, and budget ('price_max') constraints.
    """
    url = db_url or DATABASE_URL
    clean_tokens = tokenize(query)
    if not clean_tokens:
        return []

    # 1. Candidate Generation: Resolve SQL filters for candidate pool
    filters, params = [], []
    if department and department != "All":
        filters.append("department = %s")
        params.append(department)
    if category:
        filters.append("category ILIKE %s")
        params.append(f"%{category}%")
    if price_max and price_max > 0:
        filters.append("price_idr <= %s")
        params.append(price_max)

    allowed_ids = None
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            if filters:
                cur.execute(
                    f"SELECT id FROM products WHERE {' AND '.join(filters)}",
                    params,
                )
                allowed_ids = {row[0] for row in cur.fetchall()}
                if not allowed_ids:
                    return []

            # BM25 Lexical candidates
            bm25_index = get_index()
            bm25_ranked = bm25_index.rank(query, allowed=allowed_ids, dedupe=False)

            # Dense Vector candidates (pgvector)
            vector_ranked = []
            q_vec = embed_query(query)
            if q_vec is not None:
                vec_str = "[" + ",".join(f"{x:.6f}" for x in q_vec) + "]"
                vec_where = ["embedding IS NOT NULL"]
                vec_params = [vec_str]
                if allowed_ids:
                    vec_where.append("id = ANY(%s)")
                    vec_params.append(list(allowed_ids))
                vec_params.append(vec_str)

                cur.execute(
                    f"""
                    SELECT id, 1 - (embedding <=> %s::vector) AS similarity
                    FROM products
                    WHERE {' AND '.join(vec_where)}
                    ORDER BY embedding <=> %s::vector ASC
                    LIMIT 40
                    """,
                    vec_params,
                )
                vector_ranked = [(row[0], float(row[1])) for row in cur.fetchall()]

    # Stage 1 Rank Fusion (RRF k=60)
    if bm25_ranked and vector_ranked:
        fused = reciprocal_rank_fusion([bm25_ranked[:40], vector_ranked[:40]], k=60)
        candidate_ids = [doc_id for doc_id, _ in fused[:20]]
    elif vector_ranked:
        candidate_ids = [doc_id for doc_id, _ in vector_ranked[:20]]
    elif bm25_ranked:
        candidate_ids = [doc_id for doc_id, _ in bm25_ranked[:20]]
    else:
        return []

    # Fetch hydrated product rows for top candidates
    candidates = []
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {PRODUCT_COLS} FROM products WHERE id = ANY(%s)",
                (candidate_ids,),
            )
            rows = cur.fetchall()
            by_id = {r[0]: _row_to_dict(r) for r in rows}
            for cid in candidate_ids:
                if cid in by_id:
                    candidates.append(by_id[cid])

    # Stage 2 Precision: Cross-Encoder Reranking
    reranked = rerank(query=query, candidates=candidates, text_key="title", limit=limit)
    return reranked


def get_product_details(asin_or_id: str | int, db_url: str | None = None) -> dict[str, Any]:
    """Retrieve full catalog specifications, features, and price by ASIN or product ID."""
    url = db_url or DATABASE_URL
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            if isinstance(asin_or_id, int) or (isinstance(asin_or_id, str) and asin_or_id.isdigit()):
                cur.execute(f"SELECT {PRODUCT_COLS} FROM products WHERE id = %s", (int(asin_or_id),))
            else:
                cur.execute(f"SELECT {PRODUCT_COLS} FROM products WHERE asin = %s", (str(asin_or_id),))
            row = cur.fetchone()
            if not row:
                return {"error": f"Product '{asin_or_id}' not found in catalog."}
            return _row_to_dict(row)


def get_product_reviews(
    asin: str,
    topic: str | None = None,
    limit: int = 5,
    db_url: str | None = None,
) -> dict[str, Any]:
    """Fetch authentic customer reviews for sizing reality, durability, and fabric quality.

    Computes star breakdown and filters/highlights reviews matching target topic.
    """
    url = db_url or DATABASE_URL
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            # Aggregate ratings
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
            agg = cur.fetchone()
            total_count = agg[0]
            avg_rating = round(float(agg[1]), 1) if agg and agg[0] > 0 else 0.0
            breakdown = {
                "5_star": agg[2] if agg else 0,
                "4_star": agg[3] if agg else 0,
                "3_star": agg[4] if agg else 0,
                "2_star": agg[5] if agg else 0,
                "1_star": agg[6] if agg else 0,
            }

            if total_count == 0:
                return {
                    "asin": asin,
                    "total_reviews": 0,
                    "average_rating": 0.0,
                    "breakdown": breakdown,
                    "aspect_summary": "No verified buyer reviews yet.",
                    "reviews": [],
                }

            # Filter or retrieve top reviews
            topic_filter = ""
            params: list[Any] = [asin]
            if topic:
                keywords = re.findall(r"\w+", topic.lower())
                if keywords:
                    kw_clauses = " OR ".join(["comment ILIKE %s OR summary ILIKE %s"] * len(keywords))
                    topic_filter = f"AND ({kw_clauses})"
                    for kw in keywords:
                        params.extend([f"%{kw}%", f"%{kw}%"])

            params.append(limit * 2)
            cur.execute(
                f"""
                SELECT id, rating, summary, comment, author, verified, review_date
                FROM reviews
                WHERE asin = %s {topic_filter}
                ORDER BY verified DESC, id ASC
                LIMIT %s
                """,
                params,
            )
            rows = cur.fetchall()

            if not rows and topic:
                # Fallback to general reviews if topic filter returned none
                cur.execute(
                    """
                    SELECT id, rating, summary, comment, author, verified, review_date
                    FROM reviews
                    WHERE asin = %s
                    ORDER BY verified DESC, id ASC
                    LIMIT %s
                    """,
                    (asin, limit),
                )
                rows = cur.fetchall()

            reviews = []
            for r in rows[:limit]:
                reviews.append({
                    "id": r[0],
                    "rating": float(r[1]),
                    "summary": r[2] or "",
                    "comment": r[3],
                    "author": r[4],
                    "verified": r[5],
                    "reviewDate": r[6],
                })

            # Quick aspect signals from comments
            comments_blob = " ".join(r["comment"].lower() for r in reviews)
            signals = []
            if "shrink" in comments_blob:
                signals.append("Multiple buyers note shrinkage after hot wash")
            if "tight" in comments_blob or "small" in comments_blob or "size up" in comments_blob:
                signals.append("Buyers advise sizing up due to snug waist/cut")
            if "comfortable" in comments_blob or "soft" in comments_blob:
                signals.append("High comfort and everyday wearability praised")
            if "durable" in comments_blob or "heavy" in comments_blob or "sturdy" in comments_blob:
                signals.append("Durable, heavyweight construction confirmed")

            aspect_summary = "; ".join(signals) if signals else "Customers generally report satisfactory fit and quality."

            return {
                "asin": asin,
                "total_reviews": total_count,
                "average_rating": avg_rating,
                "breakdown": breakdown,
                "aspect_summary": aspect_summary,
                "reviews": reviews,
            }


def search_by_image(
    image_bytes: bytes | None = None,
    image_url_or_ref: str | None = None,
    department: str | None = None,
    price_max: int | None = None,
    limit: int = 4,
    db_url: str | None = None,
) -> list[dict[str, Any]]:
    """Visual Search (CLIP ViT-B/32 + pgvector HNSW cosine distance).

    Accepts raw image bytes, HTTP URL, or base64 data URL.
    """
    url = db_url or DATABASE_URL
    contents = image_bytes

    if contents is None and image_url_or_ref:
        if image_url_or_ref.startswith("data:image/"):
            # base64 data URL
            _, b64data = image_url_or_ref.split(",", 1)
            contents = base64.b64decode(b64data)
        elif image_url_or_ref.startswith("http://") or image_url_or_ref.startswith("https://"):
            req = urllib.request.Request(image_url_or_ref, headers={"User-Agent": "TokoMarcell/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                contents = resp.read()
        elif os.path.isfile(image_url_or_ref):
            with open(image_url_or_ref, "rb") as f:
                contents = f.read()

    if not contents:
        return []

    vec = embed_image_bytes(contents)
    if not vec:
        return []

    vec_literal = "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
    where_clauses = ["image_embedding IS NOT NULL"]
    params: list[Any] = [vec_literal]

    if department and department != "All":
        where_clauses.append("department = %s")
        params.append(department)
    if price_max and price_max > 0:
        where_clauses.append("price_idr <= %s")
        params.append(price_max)

    params.extend([vec_literal, limit])
    where_sql = " AND ".join(where_clauses)

    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {PRODUCT_COLS},
                       ROUND((1 - (image_embedding <=> %s::vector))::numeric, 4) AS visual_similarity
                FROM products
                WHERE {where_sql}
                ORDER BY image_embedding <=> %s::vector ASC
                LIMIT %s
                """,
                params,
            )
            rows = cur.fetchall()

    items = []
    for r in rows:
        prod = _row_to_dict(r[:-1])
        prod["visualSimilarity"] = float(r[-1]) if r[-1] is not None else 0.0
        items.append(prod)
    return items


def lookup_store_policy(query: str, limit: int = 3, db_url: str | None = None) -> list[dict[str, Any]]:
    """Retrieve store operations, QRIS demo rules, shipping, returns, and master size charts."""
    url = db_url or DATABASE_URL
    q_vec = embed_query(query)
    if q_vec is None:
        return []

    vec_str = "[" + ",".join(f"{x:.6f}" for x in q_vec) + "]"
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT category, title, content,
                       ROUND((1 - (embedding <=> %s::vector))::numeric, 4) AS score
                FROM store_knowledge
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> %s::vector ASC
                LIMIT %s
                """,
                (vec_str, vec_str, limit),
            )
            rows = cur.fetchall()

    return [
        {
            "category": r[0],
            "title": r[1],
            "content": r[2],
            "similarity": float(r[3]) if r[3] is not None else 0.0,
        }
        for r in rows
    ]


def get_order_status(token: str, db_url: str | None = None) -> dict[str, Any]:
    """Retrieve live order status, total IDR, and purchased items by unique checkout token."""
    url = db_url or DATABASE_URL
    clean_token = token.strip().strip("'\"`")
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, token, total_idr, item_count, status, created_at
                FROM orders
                WHERE token = %s OR token ILIKE %s
                """,
                (clean_token, f"%{clean_token}%"),
            )
            order_row = cur.fetchone()
            if not order_row:
                return {
                    "error": f"Pesanan dengan token '{token}' tidak ditemukan dalam sistem Toko Marcell.",
                    "found": False,
                }

            order_id = order_row[0]
            cur.execute(
                """
                SELECT asin, title, qty, unit_price_idr
                FROM order_items
                WHERE order_id = %s
                ORDER BY id ASC
                """,
                (order_id,),
            )
            item_rows = cur.fetchall()
            items = [
                {
                    "asin": r[0],
                    "title": r[1],
                    "qty": r[2],
                    "unitPriceIdr": r[3],
                }
                for r in item_rows
            ]

            return {
                "found": True,
                "orderId": order_id,
                "token": order_row[1],
                "totalIdr": order_row[2],
                "itemCount": order_row[3],
                "status": order_row[4],
                "createdAt": order_row[5].isoformat() if order_row[5] else None,
                "items": items,
            }
