"""
Deterministic Grounding Tools for Toko Marcell AI Copilot.

All tools access authoritative database tables (products, store_knowledge)
with zero hallucination, strict price handling (IDR), and robust error handling.

Tools accept an optional ``db_url`` parameter for standalone use (pipeline tests);
``db.connect`` uses the pool when the URL is the configured one and a one-shot
connection otherwise.
"""

import logging
import re
import threading
import time
from collections import OrderedDict
from typing import Any

import catalog_repo
import retrieval
from db import connect as _connect
from search import embed_query, reciprocal_rank_fusion, rewrite_id_to_en

logger = logging.getLogger(__name__)

# Policy RAG is deterministic for a fixed knowledge seed, so identical queries
# can skip both embedding and pgvector. Entries are bounded and expire; seeding
# new knowledge calls clear_policy_cache().
POLICY_CACHE_TTL_SECONDS = 600
POLICY_CACHE_MAX_ITEMS = 256
_policy_cache: OrderedDict[
    tuple[str, int], tuple[float, list[dict[str, Any]]]
] = OrderedDict()
_policy_cache_lock = threading.Lock()


def _normalize_policy_query(query: str) -> str:
    """Case/whitespace normalization for cache keys, not for retrieval."""
    return " ".join((query or "").casefold().split())


def clear_policy_cache() -> None:
    """Drop all cached policy chunks (tests and knowledge re-seeding)."""
    with _policy_cache_lock:
        _policy_cache.clear()


def _policy_cache_get(key: tuple[str, int]) -> list[dict[str, Any]] | None:
    now = time.monotonic()
    with _policy_cache_lock:
        entry = _policy_cache.get(key)
        if entry is None:
            return None
        expires_at, results = entry
        if expires_at <= now:
            _policy_cache.pop(key, None)
            return None
        _policy_cache.move_to_end(key)
        return [dict(item) for item in results]


def _policy_cache_set(key: tuple[str, int], results: list[dict[str, Any]]) -> None:
    expires_at = time.monotonic() + POLICY_CACHE_TTL_SECONDS
    with _policy_cache_lock:
        _policy_cache[key] = (expires_at, [dict(item) for item in results])
        _policy_cache.move_to_end(key)
        while len(_policy_cache) > POLICY_CACHE_MAX_ITEMS:
            _policy_cache.popitem(last=False)


def _search_catalog_once(
    query: str,
    category: str | None = None,
    department: str | None = None,
    price_max: int | None = None,
    limit: int = 4,
    db_url: str | None = None,
) -> list[dict[str, Any]]:
    """One hybrid BM25 + pgvector search, optionally reranked."""
    filters = retrieval.ProductFilters(
        department=department,
        category=category,
        price_max=price_max,
        # The model may pass a partial category ("Dresses" vs "Dresses & Gowns"),
        # so the tool keeps its substring match.
        category_match="contains",
    )
    options = retrieval.RankOptions(
        mode="hybrid",
        lexical_limit=40,
        dense_limit=40,
        fusion_limit=20,
        dedupe=False,
    )
    with _connect(db_url) as conn:
        with conn.cursor() as cur:
            return retrieval.search_products(
                cur,
                query,
                filters=filters,
                options=options,
                rerank=True,
                rerank_window=20,
                limit=limit,
            )


def search_catalog(
    query: str,
    category: str | None = None,
    department: str | None = None,
    price_max: int | None = None,
    limit: int = 4,
    db_url: str | None = None,
) -> list[dict[str, Any]]:
    """Hybrid search with one deterministic Indonesian→English retry on empty.

    The retry keeps the same category, department, price and limit constraints;
    it only substitutes the query text.
    """
    results = _search_catalog_once(
        query,
        category=category,
        department=department,
        price_max=price_max,
        limit=limit,
        db_url=db_url,
    )
    if results:
        return results

    rewritten = rewrite_id_to_en(query)
    if not rewritten:
        return results

    return _search_catalog_once(
        rewritten,
        category=category,
        department=department,
        price_max=price_max,
        limit=limit,
        db_url=db_url,
    )


def get_product_details(asin_or_id: str | int, db_url: str | None = None) -> dict[str, Any]:
    """Retrieve full catalog specifications by ASIN or product ID."""
    with _connect(db_url) as conn:
        with conn.cursor() as cur:
            product = catalog_repo.fetch_by_id_or_asin(cur, asin_or_id)
    if product is None:
        return {"error": f"Product '{asin_or_id}' not found in catalog."}
    return product


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
    """Retrieve store ops, QRIS, shipping, returns, and size charts.

    Identical normalized query+limit pairs are served from a bounded 10-minute
    in-memory TTL cache, avoiding a second embedding and pgvector round trip.
    """
    cache_key = (_normalize_policy_query(query), int(limit))
    cached = _policy_cache_get(cache_key)
    if cached is not None:
        return cached

    results = _lookup_store_policy_uncached(query, limit=limit, db_url=db_url)
    _policy_cache_set(cache_key, results)
    return results


def _lookup_store_policy_uncached(
    query: str, limit: int = 3, db_url: str | None = None
) -> list[dict[str, Any]]:
    """Bilingual hybrid RAG: hypothetical + direct chunk + lexical RRF."""
    q_vec = embed_query(query)
    if q_vec is None:
        # Dense search disabled (ENABLE_DENSE_SEARCH=false) or embedding failed.
        # The knowledge base is tiny (a few dozen chunks), so IDF-weighted
        # lexical scoring over every chunk keeps policy RAG working with zero
        # ML models in memory.
        logger.debug("embed_query returned None; using lexical store-policy retrieval")
        with _connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, category, title, content FROM store_knowledge")
                rows = cur.fetchall()
        chunks = [(int(r[0]), str(r[1]), str(r[2]), str(r[3])) for r in rows]
        chunk_map = {cid: (cat, tit, cont) for cid, cat, tit, cont in chunks}
        lexical_ranked = score_policy_chunks_lexical(query, chunks)
        return [
            {
                "category": chunk_map[cid][0],
                "title": chunk_map[cid][1],
                "content": chunk_map[cid][2],
                "score": round(score, 4),
            }
            for cid, score in lexical_ranked[:limit]
        ]

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
                product = catalog_repo.fetch_by_asin(cur, str(asin))
        if product is None:
            return {"error": f"Produk dengan ASIN {asin} tidak ditemukan."}
        matched_title = product["title"]
        product_brand = product["brand"]
        product_department = product["department"]
        product_category = product["category"]
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
