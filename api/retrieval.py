"""Ranked product retrieval: BM25, dense vectors and their fusion.

The shop's ``GET /search`` endpoint and the copilot's ``search_catalog`` tool both
answer "which products match this query", with the same building blocks:
filter → lexical rank → vector rank → Reciprocal Rank Fusion → hydrate.

That pipeline used to exist twice (once in ``routers/search.py``, once in
``agent_tools.py``) with different candidate caps, different category matching
(``=`` vs ``ILIKE``) and therefore different results for the same query.  The
knobs that genuinely differ per caller are explicit parameters here.

This module is UI-agnostic and returns ``(product_id, score)`` pairs; callers
decide pagination, reranking and response shape.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

import catalog_repo
from config import get_settings
from psycopg import sql
from search import (
    build_search_index,
    embed_query,
    get_index,
    reciprocal_rank_fusion,
    tokenize,
)

logger = logging.getLogger(__name__)

Mode = Literal["hybrid", "bm25", "dense"]
CategoryMatch = Literal["exact", "contains"]


# ── Filters ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProductFilters:
    """Constraints applied in SQL before ranking.

    ``category_match="contains"`` is a case-insensitive substring match, which is
    what the copilot tool has always used (the model may pass a partial category);
    the HTTP endpoint keeps exact matching.
    """

    department: str | None = None
    category: str | None = None
    price_max: int | None = None
    category_match: CategoryMatch = "exact"

    def is_empty(self) -> bool:
        return not (self.department or self.category or self.price_max)

    def sql(self) -> tuple[list[sql.Composable], list[Any]]:
        """AND-able SQL fragments plus their parameters."""
        fragments: list[sql.Composable] = []
        params: list[Any] = []
        if self.department and self.department != "All":
            fragments.append(sql.SQL("department = %s"))
            params.append(self.department)
        if self.category:
            if self.category_match == "contains":
                escaped = self.category.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                fragments.append(sql.SQL("category ILIKE %s ESCAPE '\\'"))
                params.append(f"%{escaped}%")
            else:
                fragments.append(sql.SQL("category = %s"))
                params.append(self.category)
        if self.price_max and self.price_max > 0:
            fragments.append(sql.SQL("price_idr <= %s"))
            params.append(self.price_max)
        return fragments, params


@dataclass(frozen=True)
class RankOptions:
    """Per-caller ranking knobs.

    ``lexical_limit``/``dense_limit`` cap each ranker's contribution before fusion
    and ``fusion_limit`` caps the fused list; ``None`` keeps everything.  The
    defaults mirror the HTTP endpoint.
    """

    mode: Mode = "hybrid"
    enable_dense: bool = True
    lexical_limit: int | None = 100
    dense_limit: int = 100
    fusion_limit: int | None = None
    dedupe: bool = True

    def __post_init__(self) -> None:
        if self.lexical_limit is not None and self.lexical_limit <= 0:
            raise ValueError("lexical_limit must be positive or None")
        if self.dense_limit <= 0:
            raise ValueError("dense_limit must be positive")


# ── Ranking ──────────────────────────────────────────────────────────────────


def _vector_literal(vector: list[float]) -> str:
    """pgvector text literal: ``[0.012345, ...]``."""
    return "[" + ",".join(f"{value:.6f}" for value in vector) + "]"


def allowed_ids(cur, filters: ProductFilters) -> set[int] | None:
    """Ids that satisfy the filters, or ``None`` when there are no filters.

    An empty set means "nothing matches" and callers short-circuit on it.
    """
    fragments, params = filters.sql()
    if not fragments:
        return None
    cur.execute(
        sql.SQL("SELECT id FROM products WHERE ") + sql.SQL(" AND ").join(fragments),
        params,
    )
    return {row[0] for row in cur.fetchall()}


def dense_ranked(
    cur,
    query: str,
    filters: ProductFilters,
    *,
    limit: int,
) -> list[tuple[int, float]]:
    """Vector-similarity ranking (pgvector cosine distance), best first."""
    query_vector = embed_query(query)
    if query_vector is None:
        return []

    embed_column = sql.Identifier(get_settings().TEXT_EMBED_COLUMN)
    fragments, params = filters.sql()
    where = [sql.SQL("{} IS NOT NULL").format(embed_column), *fragments]
    vector = _vector_literal(query_vector)

    cur.execute(
        sql.SQL("SELECT id, 1 - ({col} <=> %s::vector) AS similarity FROM products WHERE ").format(col=embed_column)
        + sql.SQL(" AND ").join(where)
        + sql.SQL(" ORDER BY {col} <=> %s::vector LIMIT %s").format(col=embed_column),
        [vector, *params, vector, limit],
    )
    return [(row[0], float(row[1])) for row in cur.fetchall()]


def ranked_ids(
    cur,
    query: str,
    *,
    filters: ProductFilters | None = None,
    options: RankOptions | None = None,
) -> list[tuple[int, float]]:
    """Rank product ids for ``query``. Empty list means "no matches"."""
    filters = filters or ProductFilters()
    options = options or RankOptions()

    if not tokenize(query):
        return []

    allowed = allowed_ids(cur, filters)
    if allowed is not None and not allowed:
        return []

    index = get_index(build_search_index)
    wants_lexical = options.mode in ("bm25", "hybrid") or not options.enable_dense
    lexical: list[tuple[int, float]] = []
    if wants_lexical:
        lexical = index.rank(query, allowed=allowed, dedupe=False)
        if options.lexical_limit is not None:
            lexical = lexical[: options.lexical_limit]

    dense: list[tuple[int, float]] = []
    if options.enable_dense and options.mode in ("dense", "hybrid"):
        dense = dense_ranked(cur, query, filters, limit=options.dense_limit)

    if options.mode == "bm25":
        ranked = lexical
    elif options.mode == "dense":
        # A dense-only request on a deployment without embeddings still has the
        # lexical index to fall back on.
        ranked = dense if options.enable_dense else lexical
    elif lexical and dense:
        ranked = reciprocal_rank_fusion([lexical, dense], k=60)
    else:
        ranked = dense or lexical

    if options.dedupe:
        ranked = index.dedupe(ranked)
    if options.fusion_limit is not None:
        ranked = ranked[: options.fusion_limit]
    return ranked


# ── Hydration / reranking ────────────────────────────────────────────────────


def hydrate(cur, hits: list[tuple[int, float]]) -> list[dict[str, Any]]:
    """Attach the full product JSON (and the retrieval score) to ranked ids."""
    ids = [product_id for product_id, _ in hits]
    products = catalog_repo.fetch_by_ids(cur, ids)
    return [{**products[product_id], "score": score} for product_id, score in hits if product_id in products]


def rerank_top(
    cur,
    query: str,
    ranked: list[tuple[int, float]],
    *,
    window: int,
    text_key: str = "title",
) -> tuple[list[tuple[int, float]], bool]:
    """Cross-encoder rerank the first ``window`` hits, keeping the rest in place.

    Returns ``(hits, applied)``; ``applied`` is ``False`` when there was nothing
    to rerank, so callers report what actually happened.
    """
    from reranker import rerank

    top = ranked[:window]
    products = catalog_repo.fetch_by_ids(cur, [product_id for product_id, _ in top])
    candidates = [{**products[product_id], "score": score} for product_id, score in top if product_id in products]
    if not candidates:
        return ranked, False

    reranked = rerank(query=query, candidates=candidates, text_key=text_key, limit=window)
    hits = [(item["id"], item.get("cross_encoder_score", item["score"])) for item in reranked] + ranked[window:]
    return hits, True


def search_products(
    cur,
    query: str,
    *,
    filters: ProductFilters | None = None,
    options: RankOptions | None = None,
    rerank: bool = False,
    rerank_window: int = 50,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Ranked, hydrated products for a query, optionally truncated to ``limit``."""
    ranked = ranked_ids(cur, query, filters=filters, options=options)
    if rerank and ranked and get_settings().ENABLE_RERANKER:
        ranked, _ = rerank_top(cur, query, ranked, window=min(len(ranked), rerank_window))
    if limit is not None:
        ranked = ranked[:limit]
    return hydrate(cur, ranked)
