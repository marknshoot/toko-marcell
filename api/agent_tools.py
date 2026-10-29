"""
Deterministic Grounding Tools for Toko Marcell AI Copilot.

All tools access authoritative database tables (products, store_knowledge)
with zero hallucination, strict price handling (IDR), and robust error handling.

Tools accept an optional ``db_url`` parameter for standalone use (pipeline tests);
when called from within the server process, ``db.get_conn()`` uses the pool.
"""

import logging
import os
import re
from typing import Any

import psycopg
from psycopg import sql
from reranker import rerank
from routers.catalog import PRODUCT_COLUMNS as PRODUCT_COLS
from routers.catalog import row_to_product as _row_to_dict
from search import (
    embed_query,
    get_index,
    reciprocal_rank_fusion,
    tokenize,
)

logger = logging.getLogger(__name__)


def _default_db_url() -> str:
    try:
        from config import get_settings
        return get_settings().DATABASE_URL
    except Exception:
        return os.environ.get("DATABASE_URL", "postgresql://toko:toko@localhost:5432/toko")


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
        escaped_cat = category.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        filters.append("category ILIKE %s ESCAPE '\\'")
        params.append(f"%{escaped_cat}%")
    if price_max and price_max > 0:
        filters.append("price_idr <= %s")
        params.append(price_max)

    candidate_ids: list[int] = []
    with _connect(db_url) as conn:
        with conn.cursor() as cur:
            allowed_ids = None
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
                try:
                    from config import get_settings
                    embed_col = get_settings().TEXT_EMBED_COLUMN
                except Exception:
                    embed_col = os.environ.get("TEXT_EMBED_COLUMN", "embedding_ml")
                vec_str = "[" + ",".join(f"{x:.6f}" for x in q_vec) + "]"
                vec_where: list[sql.Composable] = [
                    sql.SQL("{} IS NOT NULL").format(sql.Identifier(embed_col)),
                    *(sql.SQL(f) for f in filters),
                ]
                vec_params: list[Any] = [vec_str, *params, vec_str]

                cur.execute(
                    sql.SQL(
                        "SELECT id, 1 - ({col} <=> %s::vector) AS similarity"
                        " FROM products WHERE "
                    ).format(col=sql.Identifier(embed_col))
                    + sql.SQL(" AND ").join(vec_where)
                    + sql.SQL(" ORDER BY {col} <=> %s::vector ASC LIMIT 40").format(
                        col=sql.Identifier(embed_col)
                    ),
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

            cur.execute(
                f"SELECT {PRODUCT_COLS} FROM products WHERE id = ANY(%s)",
                (candidate_ids,),
            )
            by_id = {r[0]: _row_to_dict(r) for r in cur.fetchall()}

    candidates = [by_id[cid] for cid in candidate_ids if cid in by_id]
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


_POLICY_STOPWORDS = frozenset(
    "a an and are at be by do does for from how in is it of on or the to what when where "
    "ada aja apa apakah atau bagaimana berapa bisa buat dalam dan dari dengan di dong "
    "gak gimana itu jadi kalau kak ke lagi mau memang mending min nggak nya pada pakai "
    "sama saya sih soal sudah tapi tentang untuk ya yang".split()
)


def score_policy_chunks_lexical(
    query: str,
    chunks: list[tuple[int, str, str, str]],
) -> list[tuple[int, float]]:
    """IDF-weighted lexical keyword ranker for bilingual (ID/EN) store policy & sizing chunks."""
    import math

    tokens = list(dict.fromkeys(
        t for t in re.findall(r"[a-z0-9]+", (query or "").lower())
        if len(t) >= 2 and t not in _POLICY_STOPWORDS
    ))
    if not tokens or not chunks:
        return []

    n_docs = len(chunks)
    prepared = [
        (cid, f"{category or ''} {title or ''}".lower(), (content or "").lower())
        for cid, category, title, content in chunks
    ]
    idf = {
        tok: math.log(1.0 + n_docs / max(1, sum(1 for _, h, b in prepared if tok in h or tok in b)))
        for tok in tokens
    }

    scored: list[tuple[int, float]] = []
    for cid, head_blob, body_blob in prepared:
        score = 0.0
        for tok in tokens:
            w = idf[tok]
            if tok in head_blob:
                score += 2.5 * w
            if tok in body_blob:
                score += 1.0 * w
        if score > 0:
            scored.append((cid, score))
    return sorted(scored, key=lambda item: (-item[1], item[0]))


def lookup_store_policy(query: str, limit: int = 3, db_url: str | None = None) -> list[dict[str, Any]]:
    """Retrieve store operations, QRIS demo rules, shipping, returns, and master size charts.

    Uses Bilingual Hybrid RAG: fuses hypothetical-question vector similarity,
    direct chunk vector similarity, and lexical keyword matching via RRF.
    """
    q_vec = embed_query(query)
    if q_vec is None:
        return []

    vec_str = "[" + ",".join(f"{x:.6f}" for x in q_vec) + "]"
    with _connect(db_url) as conn:
        with conn.cursor() as cur:
            chunk_by_id: dict[int, tuple[str, str, str, float]] = {}
            hyp_ranked: list[tuple[int, float]] = []
            direct_ranked: list[tuple[int, float]] = []

            try:
                cur.execute(
                    """
                    SELECT k.category, k.title, k.content,
                           ROUND((1 - (q.embedding <=> %s::vector))::numeric, 4) AS score,
                           k.id
                    FROM store_knowledge_questions q
                    JOIN store_knowledge k ON k.id = q.chunk_id
                    WHERE q.embedding IS NOT NULL
                    ORDER BY q.embedding <=> %s::vector ASC
                    LIMIT %s
                    """,
                    (vec_str, vec_str, limit * 5),
                )
                seen_hyp: set[int] = set()
                for r in cur.fetchall():
                    cid = int(r[4]) if len(r) > 4 else len(seen_hyp) + 1
                    if cid in seen_hyp:
                        continue
                    seen_hyp.add(cid)
                    sim = float(r[3]) if r[3] is not None else 0.0
                    hyp_ranked.append((cid, sim))
                    chunk_by_id[cid] = (r[0], r[1], r[2], sim)
            except Exception:
                conn.rollback()

            try:
                cur.execute(
                    """
                    SELECT category, title, content,
                           ROUND((1 - (embedding <=> %s::vector))::numeric, 4) AS score,
                           id
                    FROM store_knowledge
                    WHERE embedding IS NOT NULL
                    ORDER BY embedding <=> %s::vector ASC
                    """,
                    (vec_str, vec_str),
                )
                for idx, r in enumerate(cur.fetchall(), 1):
                    cid = int(r[4]) if len(r) > 4 else idx
                    sim = float(r[3]) if r[3] is not None else 0.0
                    direct_ranked.append((cid, sim))
                    if cid not in chunk_by_id or sim > chunk_by_id[cid][3]:
                        chunk_by_id[cid] = (r[0], r[1], r[2], sim)
            except Exception:
                conn.rollback()

    lexical_ranked = score_policy_chunks_lexical(
        query,
        [(cid, v[0], v[1], v[2]) for cid, v in chunk_by_id.items()],
    )
    rankings = [r for r in (hyp_ranked, direct_ranked, lexical_ranked) if r]
    if not rankings:
        return []

    fused = reciprocal_rank_fusion(rankings, k=60)
    results = []
    for cid, rrf_score in fused[:limit]:
        if cid in chunk_by_id:
            cat, title, content, vec_sim = chunk_by_id[cid]
            results.append({
                "category": cat,
                "title": title,
                "content": content,
                "similarity": round(max(vec_sim, rrf_score), 4),
            })
    return results


# ---------------------------------------------------------------------------
# Sizing recommender (design §3 — 3 layers, never a certainty)
# ---------------------------------------------------------------------------

def _infer_garment_kind(
    department: str | None,
    category: str | None,
    title: str | None = None,
) -> str:
    """Return the explicit category-aware size kind (never a tops default)."""
    from sizing import size_kind_for

    return size_kind_for(department, category, title)


def recommend_size(
    height_cm: float | None = None,
    weight_kg: float | None = None,
    asin: str | None = None,
    category: str | None = None,
    brand: str | None = None,
    foot_length_cm: float | None = None,
    waist_cm: float | None = None,
    head_circumference_cm: float | None = None,
    db_url: str | None = None,
) -> dict[str, Any]:
    """Suggest a size using the correct category-aware measurement.

    Tops, bottoms, and dress use height (TB) + weight (BB). Footwear uses foot
    length, belts use waist, and headwear uses head circumference. If the
    product category has no defensible sizing basis, the core returns
    ``applicable=False`` instead of guessing tops.

    Resolution:
      - If ``asin`` is given, look up the product's brand + department/category.
      - Else use the provided ``brand`` / ``category`` directly.

    The result is advisory ("coba ukuran X dulu"), always with a disclaimer —
    never "you must buy size X".
    """
    from sizing import recommend_size_core, size_kind_for

    if asin:
        with _connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT asin, title, brand, department, category FROM products WHERE asin = %s",
                    (str(asin),),
                )
                row = cur.fetchone()
                if not row:
                    return {"error": f"Produk dengan ASIN {asin} tidak ditemukan."}
                matched_title, product_brand, product_department, product_category = row[1], row[2], row[3], row[4]
    else:
        matched_title = product_brand = product_department = product_category = None

    resolved_brand = brand or product_brand
    resolved_category = category or product_category
    resolved_department = product_department
    size_kind = size_kind_for(resolved_department, resolved_category, matched_title)

    result = recommend_size_core(
        height_cm=float(height_cm) if height_cm is not None else None,
        weight_kg=float(weight_kg) if weight_kg is not None else None,
        foot_length_cm=float(foot_length_cm) if foot_length_cm is not None else None,
        waist_cm=float(waist_cm) if waist_cm is not None else None,
        head_circumference_cm=(
            float(head_circumference_cm) if head_circumference_cm is not None else None
        ),
        size_kind=size_kind,
        brand=resolved_brand,
        department=resolved_department,
        category=resolved_category,
        title=matched_title,
    )
    result["size_kind"] = size_kind
    if matched_title:
        result["matched_product"] = {"asin": asin, "title": matched_title}
    return result
