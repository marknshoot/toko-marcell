"""Search endpoints: hybrid text search, search insights, and reindex."""

import hmac
import logging
import time
from typing import Literal

from config import get_settings
from db import get_conn
from fastapi import APIRouter, HTTPException, Query, Request
from psycopg import sql
from search import (
    build_search_index,
    embed_query,
    get_index,
    reciprocal_rank_fusion,
    reset_index,
    rewrite_id_to_en,
    tokenize,
)

from routers.catalog import PRODUCT_COLUMNS, row_to_product

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Reindex ──────────────────────────────────────────────────────────────────

@router.post("/search/reindex", status_code=202)
def reindex_search(request: Request):
    """Rebuild the in-memory index.

    Protected by X-Admin-Token header compared against ADMIN_TOKEN env var.
    If ADMIN_TOKEN is unset, the endpoint is disabled (503).
    """
    settings = get_settings()
    admin_token = settings.ADMIN_TOKEN
    if not admin_token:
        raise HTTPException(
            status_code=503,
            detail="Reindex endpoint is disabled (ADMIN_TOKEN not configured).",
        )
    provided = request.headers.get("X-Admin-Token", "")
    if not hmac.compare_digest(provided, admin_token):
        raise HTTPException(status_code=403, detail="Invalid admin token.")

    from routers.catalog import invalidate_categories_cache
    invalidate_categories_cache()
    reset_index()
    index = get_index(build_search_index)
    return {"documents": index.size, "mode": "hybrid"}


# ── Text search ──────────────────────────────────────────────────────────────

@router.get("/search")
def search_products(
    q: str = Query(min_length=1, max_length=100),
    limit: int = Query(24, ge=1, le=24),
    offset: int = Query(0, ge=0),
    department: str | None = None,
    category: str | None = None,
    mode: Literal["hybrid", "bm25", "dense", "vector"] = "hybrid",
    rerank: bool = Query(False, description="When true AND ENABLE_RERANKER is set, apply cross-encoder reranking on fused candidates before pagination."),
):
    """Hybrid (BM25 + pgvector cosine similarity), BM25-only, or dense-only search.

    ``vector`` is a deprecated alias for ``dense`` kept for backward compatibility
    with existing clients and evaluation artifacts.
    """
    settings = get_settings()
    dense_mode = mode in ("dense", "vector")
    started = time.perf_counter()

    clean_tokens = tokenize(q)
    if not clean_tokens:
        return {
            "items": [],
            "total": 0,
            "limit": limit,
            "offset": offset,
            "query": q,
            "mode": mode,
            "deduped": True,
            "rewritten": False,
            "fallback_query": None,
            "original_query": None,
            "took_ms": round((time.perf_counter() - started) * 1000, 2),
        }

    index = get_index(build_search_index)

    # Resolve the facet to a set of ids once, so lexical ranking stays in-memory.
    allowed = None
    if department or category:
        facet_filters: list[sql.Composable] = []
        facet_params: list = []
        if department:
            facet_filters.append(sql.SQL("department = %s"))
            facet_params.append(department)
        if category:
            facet_filters.append(sql.SQL("category = %s"))
            facet_params.append(category)
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("SELECT id FROM products WHERE ")
                    + sql.SQL(" AND ").join(facet_filters),
                    facet_params,
                )
                allowed = {row[0] for row in cur.fetchall()}

    def _run_query(search_query: str) -> tuple[list[tuple[int, float]], bool]:
        bm25_ranked = []
        if mode in ("bm25", "hybrid"):
            bm25_ranked = index.rank(search_query, allowed=allowed, dedupe=False)

        vector_ranked = []
        if dense_mode or mode == "hybrid":
            query_vec = embed_query(search_query)
            if query_vec is not None:
                embed_col = settings.TEXT_EMBED_COLUMN
                vec_str = "[" + ",".join(f"{x:.6f}" for x in query_vec) + "]"
                vec_filters: list[sql.Composable] = [sql.SQL("{} IS NOT NULL").format(sql.Identifier(embed_col))]
                vec_params: list = [vec_str]
                if department:
                    vec_filters.append(sql.SQL("department = %s"))
                    vec_params.append(department)
                if category:
                    vec_filters.append(sql.SQL("category = %s"))
                    vec_params.append(category)
                vec_params.append(vec_str)

                with get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            sql.SQL(
                                "SELECT id, 1 - ({col} <=> %s::vector) AS similarity"
                                " FROM products WHERE "
                            ).format(col=sql.Identifier(embed_col))
                            + sql.SQL(" AND ").join(vec_filters)
                            + sql.SQL(" ORDER BY {col} <=> %s::vector LIMIT 100").format(col=sql.Identifier(embed_col)),
                            vec_params,
                        )
                        vector_ranked = [(row[0], float(row[1])) for row in cur.fetchall()]

        if mode == "bm25":
            ranked = index.dedupe(bm25_ranked)
        elif dense_mode:
            ranked = index.dedupe(vector_ranked)
        else:  # hybrid
            if bm25_ranked and vector_ranked:
                fused = reciprocal_rank_fusion([bm25_ranked[:100], vector_ranked[:100]], k=60)
                ranked = index.dedupe(fused)
            elif vector_ranked:
                ranked = index.dedupe(vector_ranked)
            else:
                ranked = index.dedupe(bm25_ranked)

        reranked = False
        if rerank and settings.ENABLE_RERANKER and ranked:
            from reranker import rerank as do_rerank
            rerank_window = min(len(ranked), max(limit + offset, 50))
            rerank_hits = ranked[:rerank_window]
            rerank_ids = [pid for pid, _ in rerank_hits]
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        sql.SQL("SELECT {} FROM products WHERE id = ANY(%s)").format(
                            sql.SQL(PRODUCT_COLUMNS)
                        ),
                        (rerank_ids,),
                    )
                    rerank_rows = cur.fetchall()
            rerank_by_id = {row[0]: row_to_product(row) for row in rerank_rows}
            rerank_candidates = []
            for pid, score in rerank_hits:
                p = rerank_by_id.get(pid)
                if p:
                    rerank_candidates.append({**p, "score": score})
            if rerank_candidates:
                reranked_items = do_rerank(
                    search_query, rerank_candidates, text_key="title", limit=rerank_window
                )
                ranked = [
                    (item["id"], item.get("cross_encoder_score", item["score"]))
                    for item in reranked_items
                ] + ranked[rerank_window:]
                reranked = True

        return ranked, reranked

    ranked, reranked = _run_query(q)
    rewritten = False
    fallback_query = None
    original_query = None
    if not ranked:
        q_rewrite = rewrite_id_to_en(q)
        if q_rewrite:
            ranked, reranked = _run_query(q_rewrite)
            if ranked:
                rewritten = True
                fallback_query = q_rewrite
                original_query = q
    total = len(ranked)

    hits = ranked[offset: offset + limit]

    items = []
    if hits:
        ids = [product_id for product_id, _ in hits]
        scores = {product_id: score for product_id, score in hits}
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("SELECT {} FROM products WHERE id = ANY(%s)").format(
                        sql.SQL(PRODUCT_COLUMNS)
                    ),
                    (ids,),
                )
                rows = cur.fetchall()
        by_id = {row[0]: row_to_product(row) for row in rows}
        for product_id in ids:
            product = by_id.get(product_id)
            if product:
                items.append({**product, "score": scores[product_id]})

    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "query": q,
        "mode": mode,
        "reranked": reranked,
        "deduped": True,
        "rewritten": rewritten,
        "fallback_query": fallback_query,
        "original_query": original_query,
        "took_ms": round((time.perf_counter() - started) * 1000, 2),
    }


# ── Search insights ─────────────────────────────────────────────────────────

def _rate(numerator: int, denominator: int) -> float | None:
    """None instead of a fake 0.0 when there is no denominator yet."""
    if not denominator:
        return None
    return round(numerator / denominator, 4)


@router.get("/search/insights")
def search_insights(since_days: int = Query(30, ge=1, le=365)):
    """Top search queries, zero-result queries and session-level search-to-PDP."""
    window = "created_at > now() - make_interval(days => %s)"

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    COUNT(*) AS total_searches,
                    COUNT(*) FILTER (WHERE results_count = 0) AS zero_result_searches
                FROM events
                WHERE event_type = 'search' AND {window}
                """,
                (since_days,),
            )
            total_searches, zero_result_searches = cur.fetchone()

            cur.execute(
                f"""
                SELECT query, COUNT(*) AS count
                FROM events
                WHERE event_type = 'search' AND {window}
                  AND results_count = 0
                  AND query IS NOT NULL AND btrim(query) <> ''
                GROUP BY query
                ORDER BY COUNT(*) DESC, query ASC
                LIMIT 20
                """,
                (since_days,),
            )
            top_zero_result_queries = [
                {"query": query, "count": count}
                for query, count in cur.fetchall()
            ]

            cur.execute(
                f"""
                SELECT
                    query,
                    COUNT(*) AS count,
                    COALESCE(BOOL_OR(results_count = 0), FALSE) AS zero_result
                FROM events
                WHERE event_type = 'search' AND {window}
                  AND query IS NOT NULL AND btrim(query) <> ''
                GROUP BY query
                ORDER BY COUNT(*) DESC, query ASC
                LIMIT 20
                """,
                (since_days,),
            )
            top_queries = [
                {"query": query, "count": count, "zero_result": zero_result}
                for query, count, zero_result in cur.fetchall()
            ]

            cur.execute(
                f"""
                SELECT COUNT(DISTINCT session_id)
                FROM events
                WHERE event_type = 'search' AND {window}
                  AND session_id IS NOT NULL
                """,
                (since_days,),
            )
            search_sessions = cur.fetchone()[0]

            cur.execute(
                f"""
                SELECT COUNT(*)
                FROM (
                    SELECT session_id FROM events
                    WHERE event_type = 'search' AND {window}
                    INTERSECT
                    SELECT session_id FROM events
                    WHERE event_type = 'view_product' AND {window}
                ) AS searched_and_viewed
                """,
                (since_days, since_days),
            )
            pdp_sessions = cur.fetchone()[0]

    return {
        "since_days": since_days,
        "total_searches": total_searches,
        "zero_result_searches": zero_result_searches,
        "zero_result_rate": _rate(zero_result_searches, total_searches),
        "top_zero_result_queries": top_zero_result_queries,
        "top_queries": top_queries,
        "search_to_pdp_rate": _rate(pdp_sessions, search_sessions),
    }
