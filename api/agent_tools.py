"""
Deterministic Grounding Tools for Toko Marcell AI Copilot.

All tools access authoritative database tables (products, reviews, store_knowledge)
with zero hallucination, strict price handling (IDR), and robust error handling.

Tools accept an optional ``db_url`` parameter for standalone use (pipeline tests);
when called from within the server process, ``db.get_conn()`` uses the pool.
"""

import base64
import io
import ipaddress
import logging
import os
import re
import socket
import urllib.request
from typing import Any

import psycopg
from PIL import Image
from reranker import rerank
from search import (
    embed_image_bytes,
    embed_query,
    get_index,
    reciprocal_rank_fusion,
    tokenize,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Image input validation helpers (SSRF-safe)
# ---------------------------------------------------------------------------

_IMAGE_DATA_URL_RE = re.compile(
    r"^data:image/(png|jpeg|webp|gif);base64,",
    re.IGNORECASE,
)

# Max decoded image size: 10 MB
_MAX_IMAGE_BYTES = 10 * 1024 * 1024

# Allowlisted hosts for HTTPS image fetching (env-configurable)
_DEFAULT_ALLOWED_HOSTS = "images-na.ssl-images-amazon.com,m.media-amazon.com"


def _get_allowed_hosts() -> frozenset[str]:
    raw = os.environ.get("IMAGE_FETCH_ALLOWED_HOSTS", _DEFAULT_ALLOWED_HOSTS)
    return frozenset(h.strip().lower() for h in raw.split(",") if h.strip())


def _is_private_ip(ip_str: str) -> bool:
    """Return True if the IP is private, loopback, link-local, or reserved."""
    try:
        addr = ipaddress.ip_address(ip_str)
        return (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_multicast
        )
    except ValueError:
        return True


def _fetch_image_from_url(url: str) -> bytes:
    """Fetch image bytes from an HTTPS URL on the allowlist, with SSRF protections."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("Only https:// URLs are allowed for image fetching")

    host = (parsed.hostname or "").lower()
    allowed = _get_allowed_hosts()
    if host not in allowed:
        raise ValueError(
            f"Host '{host}' is not in the image fetch allowlist: {sorted(allowed)}"
        )

    try:
        resolved = socket.getaddrinfo(host, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"DNS resolution failed for '{host}': {exc}") from exc

    for _family, _type, _proto, _canonname, sockaddr in resolved:
        ip_str = sockaddr[0]
        if _is_private_ip(ip_str):
            raise ValueError(
                f"Host '{host}' resolves to a private/reserved IP ({ip_str}); request blocked"
            )

    req = urllib.request.Request(url, headers={"User-Agent": "TokoMarcell/1.0"})

    class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ARG002
            raise ValueError(f"Redirect to {newurl} is not allowed")

    opener = urllib.request.build_opener(_NoRedirectHandler)
    with opener.open(req, timeout=5) as resp:
        data = resp.read(_MAX_IMAGE_BYTES + 1)
        if len(data) > _MAX_IMAGE_BYTES:
            raise ValueError("Fetched image exceeds 10 MB size limit")
        return data


def validate_image_input(
    image_bytes: bytes | None = None,
    image_url_or_ref: str | None = None,
) -> bytes:
    """Resolve image input to validated bytes."""
    contents: bytes | None = image_bytes

    if contents is None and image_url_or_ref:
        ref = image_url_or_ref.strip()

        m = _IMAGE_DATA_URL_RE.match(ref)
        if m:
            b64data = ref[m.end():]
            contents = base64.b64decode(b64data)
        elif ref.startswith("https://"):
            contents = _fetch_image_from_url(ref)
        elif ref.startswith("http://"):
            raise ValueError("Only https:// URLs are allowed (http:// is rejected)")
        else:
            raise ValueError(
                "Invalid image reference. Accepted: raw bytes, data:image/...;base64,... URLs, "
                "or https:// URLs on allowlisted hosts."
            )

    if not contents:
        raise ValueError("No image data provided")

    if len(contents) > _MAX_IMAGE_BYTES:
        raise ValueError(f"Image exceeds {_MAX_IMAGE_BYTES // (1024 * 1024)} MB size limit")

    try:
        img = Image.open(io.BytesIO(contents))
        img.verify()
    except Exception as exc:
        raise ValueError(f"Data is not a valid image: {exc}") from exc

    return contents


def _default_db_url() -> str:
    try:
        from config import get_settings
        return get_settings().DATABASE_URL
    except Exception:
        return os.environ.get("DATABASE_URL", "postgresql://toko:toko@localhost:5432/toko")


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


def _connect(db_url: str | None):
    """Get a connection: use pool when available, direct connect otherwise."""
    try:
        import contextlib

        from db import get_conn
        # If db_url is custom (not the default), don't use pool
        if db_url and db_url != _default_db_url():
            return contextlib.contextmanager(lambda: (yield psycopg.connect(db_url)))()
        return get_conn()
    except Exception:
        url = db_url or _default_db_url()
        return psycopg.connect(url)


def search_catalog(
    query: str,
    category: str | None = None,
    department: str | None = None,
    price_max: int | None = None,
    limit: int = 4,
    db_url: str | None = None,
) -> list[dict[str, Any]]:
    """Hybrid BM25 + pgvector search, optionally reranked."""
    clean_tokens = tokenize(query)
    if not clean_tokens:
        return []

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
    with _connect(db_url) as conn:
        with conn.cursor() as cur:
            if filters:
                cur.execute(
                    f"SELECT id FROM products WHERE {' AND '.join(filters)}",
                    params,
                )
                allowed_ids = {row[0] for row in cur.fetchall()}
                if not allowed_ids:
                    return []

            bm25_index = get_index()
            bm25_ranked = bm25_index.rank(query, allowed=allowed_ids, dedupe=False)

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

    if bm25_ranked and vector_ranked:
        fused = reciprocal_rank_fusion([bm25_ranked[:40], vector_ranked[:40]], k=60)
        candidate_ids = [doc_id for doc_id, _ in fused[:20]]
    elif vector_ranked:
        candidate_ids = [doc_id for doc_id, _ in vector_ranked[:20]]
    elif bm25_ranked:
        candidate_ids = [doc_id for doc_id, _ in bm25_ranked[:20]]
    else:
        return []

    candidates = []
    with _connect(db_url) as conn:
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

    reranked = rerank(query=query, candidates=candidates, text_key="title", limit=limit)
    return reranked


def get_product_details(asin_or_id: str | int, db_url: str | None = None) -> dict[str, Any]:
    """Retrieve full catalog specifications by ASIN or product ID."""
    with _connect(db_url) as conn:
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
    """Fetch authentic customer reviews for sizing reality, durability, and fabric quality."""
    with _connect(db_url) as conn:
        with conn.cursor() as cur:
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
    """Visual Search (CLIP ViT-B/32 + pgvector HNSW cosine distance)."""
    try:
        contents = validate_image_input(image_bytes, image_url_or_ref)
    except ValueError:
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

    with _connect(db_url) as conn:
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
    q_vec = embed_query(query)
    if q_vec is None:
        return []

    vec_str = "[" + ",".join(f"{x:.6f}" for x in q_vec) + "]"
    with _connect(db_url) as conn:
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


# ---------------------------------------------------------------------------
# Sizing recommender (design §3 — 3 layers, never a certainty)
# ---------------------------------------------------------------------------

def _infer_garment_kind(department: str | None, category: str | None) -> str:
    """Guess tops vs bottoms from department/category text. Defaults to tops."""
    blob = f"{department or ''} {category or ''}".lower()
    bottoms_kw = ("pant", "trouser", "jean", "short", "legging", "chino", "cargo", "skirt", "celana")
    if any(k in blob for k in bottoms_kw):
        return "bottoms"
    return "tops"


def recommend_size(
    height_cm: float,
    weight_kg: float,
    asin: str | None = None,
    category: str | None = None,
    brand: str | None = None,
    db_url: str | None = None,
) -> dict[str, Any]:
    """Suggest a size from TB (height) + BB (weight), layering store baseline,
    brand cut rules, and the product's review fit signal.

    Resolution:
      - If ``asin`` is given, look up the product's brand + department/category
        and its product-level fit signal (``product_fit``, brand fallback).
      - Else use the provided ``brand`` / ``category`` directly.

    The result is advisory ("coba ukuran X dulu"), always with a disclaimer —
    never "you must buy size X".
    """
    from fit import get_product_fit
    from sizing import recommend_size_core

    if height_cm <= 0 or weight_kg <= 0:
        return {"error": "Butuh tinggi badan (cm) dan berat badan (kg) yang valid untuk saran ukuran."}

    resolved_brand = brand
    resolved_category = category
    resolved_department = None
    product_fit = None
    matched_title = None

    if asin:
        with _connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT asin, title, brand, department, category FROM products WHERE asin = %s",
                    (str(asin),),
                )
                row = cur.fetchone()
                if row:
                    matched_title = row[1]
                    resolved_brand = resolved_brand or row[2]
                    resolved_department = row[3]
                    resolved_category = resolved_category or row[4]
                    product_fit = get_product_fit(cur, row[0], resolved_brand)

    garment_kind = _infer_garment_kind(resolved_department, resolved_category)

    result = recommend_size_core(
        height_cm=float(height_cm),
        weight_kg=float(weight_kg),
        garment_kind=garment_kind,
        brand=resolved_brand,
        product_fit=product_fit,
    )
    if matched_title:
        result["matched_product"] = {"asin": asin, "title": matched_title}
    return result
