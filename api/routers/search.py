"""Search endpoints: text search, image search, reindex."""

import hmac
import logging
import time
from typing import Literal

from config import get_settings
from db import get_conn
from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from psycopg import sql
from search import (
    BM25Index,
    embed_image_bytes,
    embed_query,
    embed_query_clip_text,
    get_index,
    reciprocal_rank_fusion,
    reset_index,
    tokenize,
    vision_encoder_ready,
)

from routers.catalog import PRODUCT_COLUMNS, row_to_product

logger = logging.getLogger(__name__)

router = APIRouter()


# ── BM25 index builder ──────────────────────────────────────────────────────

def build_search_index() -> BM25Index:
    """Read the catalog and build the BM25 index."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, title, brand, category, department, features, description
                FROM products
                """
            )
            rows = cur.fetchall()

    index = BM25Index()
    for product_id, title, brand, category, department, features, description in rows:
        index.add(
            product_id,
            {
                "title": title or "",
                "brand": brand or "",
                "category": category or "",
                "department": department or "",
                "features": " ".join(features or []),
                "description": description or "",
            },
            dedupe_key=(title or "").strip().lower(),
        )
    index.build()
    logger.info("BM25 index built: %d documents", index.size)
    return index


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
    mode: Literal["hybrid", "bm25", "vector", "trimodal"] = "hybrid",
    rerank: bool = Query(False, description="When true AND ENABLE_RERANKER is set, apply cross-encoder reranking on fused candidates before pagination."),
):
    """Hybrid (BM25 + pgvector cosine similarity), BM25-only, vector-only, or trimodal search."""
    settings = get_settings()
    if mode == "trimodal" and not settings.ENABLE_TRIMODAL:
        raise HTTPException(
            status_code=400,
            detail=(
                "mode=trimodal is disabled on this deployment to protect memory; "
                "set ENABLE_TRIMODAL=true (and give the instance >=1 GB) to enable it."
            ),
        )
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

    bm25_ranked = []
    if mode in ("bm25", "hybrid", "trimodal"):
        bm25_ranked = index.rank(q, allowed=allowed, dedupe=False)

    vector_ranked = []
    if mode in ("vector", "hybrid", "trimodal"):
        query_vec = embed_query(q)
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

    clip_ranked = []
    if mode == "trimodal":
        clip_vec = embed_query_clip_text(q)
        if clip_vec is not None:
            clip_str = "[" + ",".join(f"{x:.6f}" for x in clip_vec) + "]"
            clip_filters: list[sql.Composable] = [sql.SQL("image_embedding IS NOT NULL")]
            clip_params: list = [clip_str]
            if department:
                clip_filters.append(sql.SQL("department = %s"))
                clip_params.append(department)
            if category:
                clip_filters.append(sql.SQL("category = %s"))
                clip_params.append(category)
            clip_params.append(clip_str)

            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        sql.SQL(
                            "SELECT id, 1 - (image_embedding <=> %s::vector) AS similarity"
                            " FROM products WHERE "
                        )
                        + sql.SQL(" AND ").join(clip_filters)
                        + sql.SQL(" ORDER BY image_embedding <=> %s::vector LIMIT 100"),
                        clip_params,
                    )
                    clip_ranked = [(row[0], float(row[1])) for row in cur.fetchall()]

    if mode == "bm25":
        ranked = index.dedupe(bm25_ranked)
    elif mode == "vector":
        ranked = index.dedupe(vector_ranked)
    elif mode == "trimodal":
        rankings_to_fuse = []
        if bm25_ranked:
            rankings_to_fuse.append(bm25_ranked[:100])
        if vector_ranked:
            rankings_to_fuse.append(vector_ranked[:100])
        if clip_ranked:
            rankings_to_fuse.append(clip_ranked[:100])
        if rankings_to_fuse:
            fused = reciprocal_rank_fusion(rankings_to_fuse, k=60)
            ranked = index.dedupe(fused)
        else:
            ranked = []
    else:  # hybrid
        if bm25_ranked and vector_ranked:
            fused = reciprocal_rank_fusion([bm25_ranked[:100], vector_ranked[:100]], k=60)
            ranked = index.dedupe(fused)
        elif vector_ranked:
            ranked = index.dedupe(vector_ranked)
        else:
            ranked = index.dedupe(bm25_ranked)

    total = len(ranked)
    reranked = False

    # ── Optional cross-encoder reranking (Stage 2) ───────────────────────────
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
            reranked_items = do_rerank(q, rerank_candidates, text_key="title", limit=rerank_window)
            ranked = [(item["id"], item.get("cross_encoder_score", item["score"])) for item in reranked_items] + ranked[rerank_window:]
            total = len(ranked)
            reranked = True

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
        "took_ms": round((time.perf_counter() - started) * 1000, 2),
    }


# ── Image search ─────────────────────────────────────────────────────────────

@router.post("/search/image")
async def search_by_image(
    file: UploadFile = File(...),  # noqa: B008  # FastAPI dependency injection pattern
    department: str | None = Query(None),
    limit: int = Query(24, ge=1, le=50),
):
    """Visual Search (Search by Image) using multimodal CLIP ViT-B/32 embeddings."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(
            status_code=400,
            detail="Uploaded file must be an image (JPEG, PNG, WEBP, etc.)",
        )

    contents = await file.read()
    if len(contents) < 50:
        raise HTTPException(status_code=400, detail="Image file is too small or corrupted")
    if len(contents) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image file exceeds 10MB limit")

    started = time.perf_counter()
    if not vision_encoder_ready():
        raise HTTPException(
            status_code=503,
            detail=(
                "Image search is unavailable: the fine-tuned CLIP vision encoder is "
                "not loaded on this deployment."
            ),
        )

    vec = embed_image_bytes(contents)
    if not vec:
        raise HTTPException(
            status_code=400,
            detail="Could not read an image from the uploaded file.",
        )
    embed_ms = round((time.perf_counter() - started) * 1000, 2)

    vec_literal = "[" + ",".join(f"{x:.6f}" for x in vec) + "]"

    where_clauses: list[sql.Composable] = [sql.SQL("image_embedding IS NOT NULL")]
    params: list = [vec_literal]

    if department and department != "All":
        where_clauses.append(sql.SQL("department = %s"))
        params.append(department)

    where_sql = sql.SQL(" AND ").join(where_clauses)
    params.extend([vec_literal, limit])

    t_db = time.perf_counter()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    "SELECT {cols},"
                    " ROUND((1 - (image_embedding <=> %s::vector))::numeric, 4) AS visual_similarity"
                    " FROM products WHERE "
                ).format(cols=sql.SQL(PRODUCT_COLUMNS))
                + where_sql
                + sql.SQL(" ORDER BY image_embedding <=> %s::vector ASC LIMIT %s"),
                params,
            )
            rows = cur.fetchall()
    db_ms = round((time.perf_counter() - t_db) * 1000, 2)

    items = []
    for row in rows:
        prod = row_to_product(row[:-1])
        prod["visualSimilarity"] = float(row[-1]) if row[-1] is not None else 0.0
        items.append(prod)

    return {
        "items": items,
        "total": len(items),
        "limit": limit,
        "filename": file.filename,
        "department": department or "All",
        "took_ms": round((time.perf_counter() - started) * 1000, 2),
        "timings": {"embed_ms": embed_ms, "db_ms": db_ms},
    }
