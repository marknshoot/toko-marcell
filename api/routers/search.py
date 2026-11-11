"""Search endpoints: hybrid text search, search insights, and reindex."""

import hmac
import logging
import time
from typing import Literal

import analytics
import catalog_repo
import retrieval
from config import get_settings
from db import get_conn
from fastapi import APIRouter, HTTPException, Query, Request
from memory_trim import trim_memory
from search import build_search_index, get_index, reset_index, rewrite_id_to_en

logger = logging.getLogger(__name__)

router = APIRouter()

# Backwards-compatible names: unit tests import these from this module.
row_to_product = catalog_repo.product_from_row


# ── Reindex ──────────────────────────────────────────────────────────────────


@router.post("/search/reindex", status_code=202)
def reindex_search(request: Request):
    """Rebuild the in-memory index.

    Protected by X-Admin-Token header compared against ADMIN_TOKEN env var.
    If ADMIN_TOKEN is unset, the endpoint is disabled (503).
    """
    admin_token = get_settings().ADMIN_TOKEN
    if not admin_token:
        raise HTTPException(
            status_code=503,
            detail="Reindex endpoint is disabled (ADMIN_TOKEN not configured).",
        )
    provided = request.headers.get("X-Admin-Token", "")
    if not hmac.compare_digest(provided, admin_token):
        raise HTTPException(status_code=403, detail="Invalid admin token.")

    catalog_repo.invalidate_cache()
    reset_index()
    index = get_index(build_search_index)
    return {"documents": index.size, "mode": "hybrid"}


# ── Text search ──────────────────────────────────────────────────────────────


def _rank_options(mode: str, *, enable_dense: bool) -> retrieval.RankOptions:
    """Map the public ``mode`` (incl. the legacy ``vector`` alias) to rank options."""
    dense_mode = mode in ("dense", "vector")
    return retrieval.RankOptions(
        mode="dense" if dense_mode else mode,
        enable_dense=enable_dense,
        # Hybrid fusion only looks at the top-100 of each ranker; single-ranker
        # modes keep every candidate they produced.
        lexical_limit=100 if mode == "hybrid" else None,
        dense_limit=100,
        dedupe=True,
    )


@router.get("/search")
def search_products(
    q: str = Query(min_length=1, max_length=100),
    limit: int = Query(24, ge=1, le=24),
    offset: int = Query(0, ge=0),
    department: str | None = None,
    category: str | None = None,
    mode: Literal["hybrid", "bm25", "dense", "vector"] = "hybrid",
    rerank: bool = Query(
        False,
        description=(
            "When true AND ENABLE_RERANKER is set, apply cross-encoder reranking on fused candidates before pagination."
        ),
    ),
):
    """Hybrid (BM25 + pgvector cosine similarity), BM25-only, or dense-only search.

    ``vector`` is a deprecated alias for ``dense`` kept for backward compatibility
    with existing clients and evaluation artifacts.
    """
    settings = get_settings()
    enable_dense = settings.ENABLE_DENSE_SEARCH
    if mode in ("dense", "vector") and not enable_dense:
        logger.info(
            "mode=%s requested but ENABLE_DENSE_SEARCH is false; falling back to BM25 for q=%r",
            mode,
            q,
        )

    started = time.perf_counter()
    filters = retrieval.ProductFilters(department=department, category=category)
    options = _rank_options(mode, enable_dense=enable_dense)

    with get_conn() as conn:
        with conn.cursor() as cur:
            active_query = q
            ranked = retrieval.ranked_ids(cur, active_query, filters=filters, options=options)
            rewritten_query: str | None = None

            if not ranked:
                # Nothing matched: try the deterministic Indonesian→English rewrite
                # once, with the same facet constraints.
                candidate = rewrite_id_to_en(q)
                if candidate:
                    ranked = retrieval.ranked_ids(cur, candidate, filters=filters, options=options)
                    if ranked:
                        active_query, rewritten_query = candidate, candidate

            total = len(ranked)
            reranked = False
            if rerank and settings.ENABLE_RERANKER and ranked:
                window = min(len(ranked), max(limit + offset, 50))
                ranked, reranked = retrieval.rerank_top(cur, active_query, ranked, window=window)

            items = retrieval.hydrate(cur, ranked[offset : offset + limit])

    # A hybrid query materializes a 100-candidate vector list plus hydrated
    # product rows; trim before returning so those pages do not stay resident.
    trim_memory()

    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "query": q,
        "mode": mode,
        "reranked": reranked,
        "deduped": True,
        "rewritten": rewritten_query is not None,
        "fallback_query": rewritten_query,
        "original_query": q if rewritten_query else None,
        "took_ms": round((time.perf_counter() - started) * 1000, 2),
    }


# ── Search insights ──────────────────────────────────────────────────────────


@router.get("/search/insights")
def search_insights(since_days: int = Query(30, ge=1, le=365)):
    """Top search queries, zero-result queries and session-level search-to-PDP."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            health = analytics.search_health(cur, since_days)

    return {
        "since_days": since_days,
        "total_searches": health["total_searches"],
        "zero_result_searches": health["zero_result_searches"],
        "zero_result_rate": health["zero_result_rate"],
        "top_zero_result_queries": health["top_zero_result_queries"],
        "top_queries": health["top_queries"],
        "search_to_pdp_rate": health["search_to_pdp_rate"],
    }
