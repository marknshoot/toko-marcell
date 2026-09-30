import os
import secrets
import time
from typing import Literal

import psycopg
from fastapi import FastAPI, HTTPException, Query, UploadFile, File, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

from search import (
    BM25Index,
    get_index,
    reset_index,
    reciprocal_rank_fusion,
    embed_query,
    embed_image_bytes,
    embed_query_clip_text,
    tokenize,
)

app = FastAPI(title="Toko Marcell API")

cors_origins_raw = os.environ.get(
    "CORS_ORIGINS",
    "http://localhost:3000,https://toko-marcell.vercel.app",
)
origins = [
    origin.strip()
    for origin in cors_origins_raw.split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if "*" in origins else origins,
    allow_origin_regex=r"^https:\/\/.*\.vercel\.app$",
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE_URL = os.environ["DATABASE_URL"]

SCHEMA: list[tuple[str, str]] = [
    ("id", "INTEGER PRIMARY KEY"),
    ("asin", "TEXT NOT NULL UNIQUE"),
    ("title", "TEXT NOT NULL"),
    ("brand", "TEXT"),
    ("price_usd", "NUMERIC(10, 2) NOT NULL"),
    ("price_idr", "INTEGER NOT NULL"),
    ("department", "TEXT NOT NULL DEFAULT 'Other'"),
    ("category", "TEXT NOT NULL DEFAULT ''"),
    ("category_path", "JSONB NOT NULL DEFAULT '[]'::jsonb"),
    ("description", "TEXT"),
    ("features", "JSONB NOT NULL DEFAULT '[]'::jsonb"),
    ("image_url", "TEXT"),
    ("avg_rating", "NUMERIC(3, 2)"),
    ("rating_count", "INTEGER NOT NULL DEFAULT 0"),
    ("also_buy", "JSONB NOT NULL DEFAULT '[]'::jsonb"),
    ("also_view", "JSONB NOT NULL DEFAULT '[]'::jsonb"),
]

VECTOR_COLUMN: tuple[str, str] = ("embedding", "vector(384)")
IMAGE_VECTOR_COLUMN: tuple[str, str] = ("image_embedding", "vector(512)")

INDEXES: list[tuple[str, str]] = [
    ("products_department_idx", "products (department)"),
    ("products_category_idx", "products (category)"),
]

EVENTS_SCHEMA: list[tuple[str, str]] = [
    ("id", "BIGSERIAL PRIMARY KEY"),
    ("session_id", "TEXT NOT NULL"),
    ("event_type", "TEXT NOT NULL"),
    ("asin", "TEXT"),
    ("query", "TEXT"),
    ("results_count", "INTEGER"),
    ("qty", "INTEGER"),
    ("price_idr", "INTEGER"),
    ("created_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
]

EVENTS_INDEXES: list[tuple[str, str]] = [
    ("events_session_idx", "events (session_id)"),
    ("events_type_created_idx", "events (event_type, created_at DESC)"),
]

ORDERS_SCHEMA: list[tuple[str, str]] = [
    ("id", "BIGSERIAL PRIMARY KEY"),
    ("token", "TEXT NOT NULL UNIQUE"),
    ("total_idr", "INTEGER NOT NULL"),
    ("item_count", "INTEGER NOT NULL"),
    ("status", "TEXT NOT NULL DEFAULT 'paid'"),
    ("session_id", "TEXT"),
    ("created_at", "TIMESTAMPTZ NOT NULL DEFAULT now()"),
]

ORDER_ITEMS_SCHEMA: list[tuple[str, str]] = [
    ("id", "BIGSERIAL PRIMARY KEY"),
    ("order_id", "BIGINT NOT NULL REFERENCES orders(id) ON DELETE CASCADE"),
    ("asin", "TEXT NOT NULL"),
    ("title", "TEXT NOT NULL"),
    ("qty", "INTEGER NOT NULL"),
    ("unit_price_idr", "INTEGER NOT NULL"),
]

ORDERS_INDEXES: list[tuple[str, str]] = [
    ("orders_created_idx", "orders (created_at DESC)"),
]

ORDER_ITEMS_INDEXES: list[tuple[str, str]] = [
    ("order_items_order_idx", "order_items (order_id)"),
]

ITEM_RECS_SCHEMA: list[tuple[str, str]] = [
    ("asin", "TEXT PRIMARY KEY"),
    ("recs", "JSONB NOT NULL DEFAULT '[]'::jsonb"),
]

ITEM_RECS_INDEXES: list[tuple[str, str]] = [
    ("item_recs_asin_idx", "item_recommendations (asin)"),
]

REVIEWS_SCHEMA: list[tuple[str, str]] = [
    ("id", "SERIAL PRIMARY KEY"),
    ("asin", "TEXT NOT NULL"),
    ("rating", "NUMERIC(2, 1) NOT NULL"),
    ("summary", "TEXT"),
    ("comment", "TEXT NOT NULL"),
    ("author", "TEXT NOT NULL DEFAULT 'Amazon Customer'"),
    ("verified", "BOOLEAN NOT NULL DEFAULT true"),
    ("review_date", "TEXT"),
    ("created_at", "TIMESTAMPTZ DEFAULT now()"),
]

REVIEWS_INDEXES: list[tuple[str, str]] = [
    ("reviews_asin_idx", "reviews (asin)"),
    ("reviews_rating_idx", "reviews (rating)"),
]

STORE_KNOWLEDGE_SCHEMA: list[tuple[str, str]] = [
    ("id", "SERIAL PRIMARY KEY"),
    ("category", "TEXT NOT NULL"),
    ("title", "TEXT NOT NULL"),
    ("content", "TEXT NOT NULL"),
    ("embedding", "vector(384)"),
]

STORE_KNOWLEDGE_INDEXES: list[tuple[str, str]] = [
    ("store_knowledge_category_idx", "store_knowledge (category)"),
    ("store_knowledge_embedding_idx", "store_knowledge USING hnsw (embedding vector_cosine_ops)"),
]


PRODUCT_COLUMNS = ", ".join(name for name, _ in SCHEMA)


def _enable_pgvector(cur) -> bool:
    """Turn on the vector extension if this Postgres can. Returns whether it worked."""
    cur.execute("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")
    if cur.fetchone() is None:
        return False
    cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
    return True


def _ensure_table(cur, table: str, columns, indexes):
    """CREATE TABLE from a column list, then add whatever an existing one is missing.

    Same additive pattern as products: `CREATE TABLE IF NOT EXISTS` is a no-op on a
    table that already exists, so a column added later would silently never appear
    and every query would fail with `column "..." does not exist` — which reads like
    a broken query rather than a missing migration.
    """
    col_defs = ",\n                    ".join(f"{name} {ddl}" for name, ddl in columns)
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {table} (
            {col_defs}
        )
        """
    )

    cur.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
        (table,),
    )
    existing = {row[0] for row in cur.fetchall()}

    added = [name for name, _ in columns if name not in existing]
    for name in added:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {name} {dict(columns)[name]}")
    if added:
        print(f"[init_db] added missing columns to {table}: {', '.join(added)}")

    for idx_name, idx_target in indexes:
        cur.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {idx_target}")


def init_db():
    """Create or migrate the schema. The rows themselves come from the offline seed
    pipeline (pipelines/seed.py) — the API never writes catalog data.

    Two things happen here:

    1. A one-time drop of the v1 demo table. v1 had 4 hand-written products and no
       `asin`; v2 serves the real Amazon catalog. That data is fully regenerable,
       so it is dropped rather than migrated — see pipelines/README.md.

    2. An ADDITIVE migration. `CREATE TABLE IF NOT EXISTS` is a no-op on a table
       that already exists, so a column added since that database was created would
       silently never appear — and then every query would fail with
       `column "..." does not exist`, which reads like a bug in the SQL rather than
       a missing migration. So we diff and ALTER.
    """
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'products'"
            )
            existing = {row[0] for row in cur.fetchall()}

            if existing and "asin" not in existing:
                print("[init_db] dropping legacy v1 products table (4 demo rows)")
                cur.execute("DROP TABLE products")

            columns = list(SCHEMA)
            if _enable_pgvector(cur):
                columns.append(VECTOR_COLUMN)
                columns.append(IMAGE_VECTOR_COLUMN)
            else:
                print("[init_db] pgvector unavailable — no embedding column, search disabled")

            col_defs = ",\n                    ".join(f"{name} {ddl}" for name, ddl in columns)
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS products (
                    {col_defs}
                )
                """
            )

            cur.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'products'"
            )
            existing = {row[0] for row in cur.fetchall()}

            added = [name for name, _ in columns if name not in existing]
            for name in added:
                ddl = dict(columns)[name]
                cur.execute(f"ALTER TABLE products ADD COLUMN {name} {ddl}")
            if added:
                print(f"[init_db] added missing columns: {', '.join(added)}")

            for idx_name, idx_target in INDEXES:
                cur.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {idx_target}")

            _ensure_table(cur, "events", EVENTS_SCHEMA, EVENTS_INDEXES)
            _ensure_table(cur, "orders", ORDERS_SCHEMA, ORDERS_INDEXES)
            _ensure_table(cur, "order_items", ORDER_ITEMS_SCHEMA, ORDER_ITEMS_INDEXES)
            _ensure_table(cur, "item_recommendations", ITEM_RECS_SCHEMA, ITEM_RECS_INDEXES)
            _ensure_table(cur, "reviews", REVIEWS_SCHEMA, REVIEWS_INDEXES)
            _ensure_table(cur, "store_knowledge", STORE_KNOWLEDGE_SCHEMA, STORE_KNOWLEDGE_INDEXES)

            conn.commit()

        try:
            from knowledge_seed import seed_knowledge
            seed_knowledge(DATABASE_URL)
        except Exception as e:
            print(f"[init_db] Warning: knowledge seed error: {e}")


init_db()


def row_to_product(row):
    """One DB row -> the JSON shape the storefront consumes (camelCase, prices as numbers)."""
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
        "features": row[10],
        "imageUrl": row[11],
        "avgRating": float(row[12]) if row[12] is not None else None,
        "ratingCount": row[13],
        "alsoBuy": row[14],
        "alsoView": row[15],
    }


@app.get("/health")
def health():
    return {"status": "ok"}


_CATEGORIES_CACHE: dict = {"timestamp": 0.0, "data": None}
_CATEGORIES_TTL_SECONDS: float = 300.0


@app.get("/categories")
def categories(response: Response):
    """Facets for the shop filter chips — cached in-memory (TTL 5m) and on HTTP clients."""
    response.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=60"

    now = time.time()
    if _CATEGORIES_CACHE["data"] is not None and (now - _CATEGORIES_CACHE["timestamp"]) < _CATEGORIES_TTL_SECONDS:
        return _CATEGORIES_CACHE["data"]

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT department, COUNT(*) FROM products
                GROUP BY department ORDER BY COUNT(*) DESC, department
                """
            )
            departments = [{"name": name, "count": count} for name, count in cur.fetchall()]
            cur.execute(
                """
                SELECT category, COUNT(*) FROM products
                WHERE category <> '' GROUP BY category
                ORDER BY COUNT(*) DESC, category LIMIT 24
                """
            )
            top_categories = [{"name": name, "count": count} for name, count in cur.fetchall()]

    data = {"departments": departments, "categories": top_categories}
    _CATEGORIES_CACHE["timestamp"] = now
    _CATEGORIES_CACHE["data"] = data
    return data


@app.get("/products")
def products(
    response: Response,
    limit: int = Query(24, ge=1, le=24),
    offset: int = Query(0, ge=0),
    department: str | None = None,
    category: str | None = None,
):
    """Paginated catalog. `department` and `category` are exact-match facets."""
    response.headers["Cache-Control"] = "public, max-age=60, s-maxage=300, stale-while-revalidate=60"

    filters = []
    params = []
    if department:
        filters.append("department = %s")
        params.append(department)
    if category:
        filters.append("category = %s")
        params.append(category)
    where = f"WHERE {' AND '.join(filters)}" if filters else ""

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM products {where}", params)
            total = cur.fetchone()[0]
            cur.execute(
                f"""
                SELECT {PRODUCT_COLUMNS} FROM products
                {where}
                ORDER BY rating_count DESC, id
                LIMIT %s OFFSET %s
                """,
                params + [limit, offset],
            )
            rows = cur.fetchall()

    return {
        "items": [row_to_product(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@app.get("/products/{product_id}")
def get_product(product_id: int, response: Response):
    response.headers["Cache-Control"] = "public, max-age=300, s-maxage=600, stale-while-revalidate=60"

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {PRODUCT_COLUMNS} FROM products WHERE id = %s",
                (product_id,),
            )
            row = cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Product not found")

    return row_to_product(row)


# ── Events (M2 / M2b) ─────────────────────────────────────────────────────────

class EventIn(BaseModel):
    """One shopper action. Pydantic rejects anything malformed with a 422, so the
    events table cannot fill up with misspelled event names."""

    event_type: Literal[
        "view_product",
        "search",
        "add_to_cart",
        "checkout_start",
        "purchase_mock",
    ]
    session_id: str = Field(min_length=8, max_length=64)
    asin: str | None = Field(default=None, max_length=32)
    query: str | None = Field(default=None, max_length=200)
    results_count: int | None = Field(default=None, ge=0)
    qty: int | None = Field(default=None, ge=1, le=99)
    price_idr: int | None = Field(default=None, ge=0)


@app.post("/events", status_code=201)
def create_event(event: EventIn):
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO events
                    (session_id, event_type, asin, query, results_count, qty, price_idr)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    event.session_id,
                    event.event_type,
                    event.asin,
                    event.query,
                    event.results_count,
                    event.qty,
                    event.price_idr,
                ),
            )
            event_id = cur.fetchone()[0]
            conn.commit()

    return {"id": event_id, "event_type": event.event_type}


def rate(numerator, denominator):
    """None instead of a fake 0.0 when there is no denominator yet."""
    if not denominator:
        return None
    return round(numerator / denominator, 4)


@app.get("/events/summary")
def events_summary(since_days: int = Query(30, ge=1, le=365)):
    """Funnel KPIs from PLAN §4, computed from the events table.

    Two caveats that affect how these numbers should be read — they are returned
    with the payload rather than hidden in a comment:
      * `search_to_pdp` is a session-level proxy (a session that both searched and
        viewed a product), not a click-through rate: the click itself is not
        recorded as its own event yet.
      * `purchase_mock` is asserted by the browser. Once checkout is confirmed
        server-side (B5) it becomes authoritative.
    """
    window = "created_at > now() - make_interval(days => %s)"

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT event_type, COUNT(*), COUNT(DISTINCT session_id)
                FROM events WHERE {window}
                GROUP BY event_type
                """,
                (since_days,),
            )
            by_type = {}
            sessions_by_step = {}
            events_total = 0
            for event_type, count, sessions in cur.fetchall():
                by_type[event_type] = count
                sessions_by_step[event_type] = sessions
                events_total += count

            cur.execute(
                f"""
                SELECT COUNT(*), COUNT(*) FILTER (WHERE results_count = 0)
                FROM events WHERE event_type = 'search' AND {window}
                """,
                (since_days,),
            )
            searches, zero_result = cur.fetchone()

            cur.execute(
                f"""
                SELECT COUNT(*) FROM (
                    SELECT session_id FROM events
                    WHERE event_type = 'search' AND {window}
                    INTERSECT
                    SELECT session_id FROM events
                    WHERE event_type = 'view_product' AND {window}
                ) AS searched_and_viewed
                """,
                (since_days, since_days),
            )
            search_to_pdp_sessions = cur.fetchone()[0]

    viewed = sessions_by_step.get("view_product", 0)
    added = sessions_by_step.get("add_to_cart", 0)
    started = sessions_by_step.get("checkout_start", 0)
    purchased = sessions_by_step.get("purchase_mock", 0)
    searched = sessions_by_step.get("search", 0)

    return {
        "since_days": since_days,
        "events_total": events_total,
        "by_type": by_type,
        "sessions_by_step": sessions_by_step,
        "rates": {
            "view_to_cart": rate(added, viewed),
            "cart_to_checkout": rate(started, added),
            "checkout_to_purchase": rate(purchased, started),
            "view_to_purchase": rate(purchased, viewed),
            "search_to_pdp": rate(search_to_pdp_sessions, searched),
        },
        "search": {
            "total": searches,
            "zero_result": zero_result,
            "zero_result_rate": rate(zero_result, searches),
        },
        "caveats": [
            "search_to_pdp is a session-level proxy (search + view_product in one session)",
            "purchase_mock is browser-asserted until checkout is confirmed server-side (B5)",
        ],
    }


# ── Checkout (B5) ─────────────────────────────────────────────────────────────

class ConfirmItem(BaseModel):
    """What the client is allowed to say about a line: which product, how many.

    Deliberately no price. The server looks it up.
    """

    model_config = ConfigDict(extra="forbid")

    asin: str = Field(min_length=1, max_length=32)
    qty: int = Field(ge=1, le=99)


class ConfirmIn(BaseModel):
    """The checkout request.

    `session_id` is required, not optional: a purchase that cannot be attributed to
    a visit is useless to the funnel, and the events table enforces the same rule.
    Letting it through as null would surface here as a 500 from the database layer
    instead of a 422 from the edge.

    `extra="forbid"` makes the no-client-price rule explicit: a request that tries
    to send a total is rejected loudly rather than having the field silently
    ignored.
    """

    model_config = ConfigDict(extra="forbid")

    items: list[ConfirmItem] = Field(min_length=1, max_length=50)
    session_id: str = Field(min_length=8, max_length=64)


@app.post("/checkout/confirm", status_code=201)
def confirm_checkout(payload: ConfirmIn):
    """Turn a cart into a paid order — the authoritative step.

    Before this existed, "paid" was a client claim and /checkout/success would
    happily confirm an order to anyone who typed the URL. Now the truth is a row
    in `orders`, and the success page can only show what the server holds.
    """
    wanted: dict[str, int] = {}
    for item in payload.items:
        wanted[item.asin] = wanted.get(item.asin, 0) + item.qty

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT asin, title, price_idr FROM products WHERE asin = ANY(%s)",
                (list(wanted),),
            )
            found = {row[0]: (row[1], row[2]) for row in cur.fetchall()}

            unknown = sorted(set(wanted) - set(found))
            if unknown:
                raise HTTPException(
                    status_code=400,
                    detail=f"unknown asin(s): {', '.join(unknown)}",
                )

            lines = [
                (asin, found[asin][0], qty, found[asin][1])
                for asin, qty in wanted.items()
            ]
            total_idr = sum(qty * unit_price for _, _, qty, unit_price in lines)
            item_count = sum(qty for _, _, qty, _ in lines)

            token = f"tk_{secrets.token_urlsafe(12)}"
            cur.execute(
                """
                INSERT INTO orders (token, total_idr, item_count, status, session_id)
                VALUES (%s, %s, %s, 'paid', %s)
                RETURNING id
                """,
                (token, total_idr, item_count, payload.session_id),
            )
            order_id = cur.fetchone()[0]

            for asin, title, qty, unit_price in lines:
                cur.execute(
                    """
                    INSERT INTO order_items (order_id, asin, title, qty, unit_price_idr)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (order_id, asin, title, qty, unit_price),
                )

            cur.execute(
                """
                INSERT INTO events (session_id, event_type, qty, price_idr)
                VALUES (%s, 'purchase_mock', %s, %s)
                """,
                (payload.session_id, item_count, total_idr),
            )

            conn.commit()

    return {
        "token": token,
        "status": "paid",
        "total_idr": total_idr,
        "item_count": item_count,
        "items": [
            {"asin": asin, "title": title, "qty": qty, "unit_price_idr": unit_price}
            for asin, title, qty, unit_price in lines
        ],
    }


@app.get("/orders/{token}")
def get_order(token: str):
    """Read an order back. This is what makes the confirmation page survive a
    refresh, a new tab, or a different device — the token in the URL is the key."""
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, token, total_idr, item_count, status, created_at
                FROM orders WHERE token = %s
                """,
                (token,),
            )
            row = cur.fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Order not found")

            order_id, token, total_idr, item_count, status, created_at = row
            cur.execute(
                """
                SELECT asin, title, qty, unit_price_idr
                FROM order_items WHERE order_id = %s ORDER BY id
                """,
                (order_id,),
            )
            items = [
                {"asin": asin, "title": title, "qty": qty, "unitPriceIdr": unit_price}
                for asin, title, qty, unit_price in cur.fetchall()
            ]

    return {
        "token": token,
        "status": status,
        "totalIdr": total_idr,
        "itemCount": item_count,
        "createdAt": created_at.isoformat(),
        "items": items,
    }


# ── Search (M3/M4: the lexical baseline) ──────────────────────────────────────

def build_search_index() -> BM25Index:
    """Read the catalog and build the BM25 index. Built once, lazily (see
    search.get_index) so a database problem cannot stop the API from booting."""
    with psycopg.connect(DATABASE_URL) as conn:
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
    print(f"[search] BM25 index built: {index.size} documents")
    return index


@app.post("/search/reindex", status_code=202)
def reindex_search():
    """Rebuild the in-memory index.

    The index is cached for the life of the process, so a newly seeded catalog is
    invisible until this is called (or the container restarts). Kept as an
    endpoint because "why are my new products not searchable" is a confusing hour
    otherwise.
    """
    _CATEGORIES_CACHE["data"] = None
    reset_index()
    index = get_index(build_search_index)
    return {"documents": index.size, "mode": "hybrid"}


@app.get("/search")
def search_products(
    q: str = Query(min_length=1, max_length=100),
    limit: int = Query(24, ge=1, le=24),
    offset: int = Query(0, ge=0),
    department: str | None = None,
    category: str | None = None,
    mode: Literal["hybrid", "bm25", "vector", "trimodal"] = "hybrid",
):
    """Hybrid (BM25 + pgvector cosine similarity), BM25-only, vector-only, or trimodal search.

    Fuses lexical term matching (BM25), dense semantic embeddings (all-MiniLM-L6-v2),
    and cross-modal visual embeddings (CLIP text-to-image) using Reciprocal Rank Fusion (RRF, k=60).
    """
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
        filters, params = [], []
        if department:
            filters.append("department = %s")
            params.append(department)
        if category:
            filters.append("category = %s")
            params.append(category)
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT id FROM products WHERE {' AND '.join(filters)}",
                    params,
                )
                allowed = {row[0] for row in cur.fetchall()}

    bm25_ranked = []
    if mode in ("bm25", "hybrid", "trimodal"):
        bm25_ranked = index.rank(q, allowed=allowed, dedupe=False)

    vector_ranked = []
    if mode in ("vector", "hybrid", "trimodal"):
        query_vec = embed_query(q)
        if query_vec is not None:
            vec_str = "[" + ",".join(f"{x:.6f}" for x in query_vec) + "]"
            vec_filters = ["embedding IS NOT NULL"]
            vec_params = [vec_str]
            if department:
                vec_filters.append("department = %s")
                vec_params.append(department)
            if category:
                vec_filters.append("category = %s")
                vec_params.append(category)
            vec_params.append(vec_str)

            with psycopg.connect(DATABASE_URL) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        SELECT id, 1 - (embedding <=> %s::vector) AS similarity
                        FROM products
                        WHERE {' AND '.join(vec_filters)}
                        ORDER BY embedding <=> %s::vector
                        LIMIT 100
                        """,
                        vec_params,
                    )
                    vector_ranked = [(row[0], float(row[1])) for row in cur.fetchall()]

    clip_ranked = []
    if mode == "trimodal":
        clip_vec = embed_query_clip_text(q)
        if clip_vec is not None:
            clip_str = "[" + ",".join(f"{x:.6f}" for x in clip_vec) + "]"
            clip_filters = ["image_embedding IS NOT NULL"]
            clip_params = [clip_str]
            if department:
                clip_filters.append("department = %s")
                clip_params.append(department)
            if category:
                clip_filters.append("category = %s")
                clip_params.append(category)
            clip_params.append(clip_str)

            with psycopg.connect(DATABASE_URL) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        SELECT id, 1 - (image_embedding <=> %s::vector) AS similarity
                        FROM products
                        WHERE {' AND '.join(clip_filters)}
                        ORDER BY image_embedding <=> %s::vector
                        LIMIT 100
                        """,
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
    hits = ranked[offset : offset + limit]

    items = []
    if hits:
        ids = [product_id for product_id, _ in hits]
        scores = {product_id: score for product_id, score in hits}
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {PRODUCT_COLUMNS} FROM products WHERE id = ANY(%s)",
                    (ids,),
                )
                rows = cur.fetchall()
        by_id = {row[0]: row_to_product(row) for row in rows}
        for product_id in ids:  # SQL does not preserve order: rebuild it from the ranking
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
        "deduped": True,
        "took_ms": round((time.perf_counter() - started) * 1000, 2),
    }


@app.post("/search/image")
async def search_by_image(
    file: UploadFile = File(...),
    department: str | None = Query(None),
    limit: int = Query(24, ge=1, le=50),
):
    """Visual Search (Search by Image) using multimodal CLIP ViT-B/32 embeddings.

    Accepts an uploaded image file, computes its 512-dim vision embedding,
    and performs cosine similarity search against `products.image_embedding`
    using pgvector HNSW index.
    """
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
    vec = embed_image_bytes(contents)
    if not vec:
        raise HTTPException(
            status_code=500,
            detail="Failed to generate image embedding from vision model",
        )
    embed_ms = round((time.perf_counter() - started) * 1000, 2)

    vec_literal = "[" + ",".join(f"{x:.6f}" for x in vec) + "]"

    where_clauses = ["image_embedding IS NOT NULL"]
    params = [vec_literal]

    if department and department != "All":
        where_clauses.append("department = %s")
        params.append(department)

    where_sql = " AND ".join(where_clauses)
    params.extend([vec_literal, limit])

    t_db = time.perf_counter()
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {PRODUCT_COLUMNS},
                       ROUND((1 - (image_embedding <=> %s::vector))::numeric, 4) AS visual_similarity
                FROM products
                WHERE {where_sql}
                ORDER BY image_embedding <=> %s::vector ASC
                LIMIT %s
                """,
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



# ── Recommendations (M7 / M8 / M9) ───────────────────────────────────────────

@app.get("/recs/popular")
def recs_popular(
    department: str | None = None,
    limit: int = Query(10, ge=1, le=24),
):
    """Bestselling / popular products baseline (M8).

    Ranked by interaction & review volume. Filterable by department.
    """
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            if department and department != "All":
                cur.execute(
                    f"""
                    SELECT {PRODUCT_COLUMNS}
                    FROM products
                    WHERE department = %s
                    ORDER BY rating_count DESC, avg_rating DESC
                    LIMIT %s
                    """,
                    (department, limit),
                )
            else:
                cur.execute(
                    f"""
                    SELECT {PRODUCT_COLUMNS}
                    FROM products
                    ORDER BY rating_count DESC, avg_rating DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
            rows = cur.fetchall()

    items = [row_to_product(row) for row in rows]
    return {
        "items": items,
        "count": len(items),
        "department": department or "All",
        "strategy": "popularity_baseline",
    }


@app.get("/recs/item/{asin}")
def recs_item(
    asin: str,
    limit: int = Query(6, ge=1, le=24),
):
    """Item-to-item recommendations for PDP & Cart (M9).

    Uses offline co-occurrence & graph model from `item_recommendations`.
    Falls back to same-category popularity if cold or empty.
    """
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT recs FROM item_recommendations WHERE asin = %s",
                (asin,),
            )
            row = cur.fetchone()
            rec_asins = row[0] if row and row[0] else []

            strategy = "item_cf"
            items = []

            if rec_asins:
                target_asins = rec_asins[:limit]
                cur.execute(
                    f"SELECT {PRODUCT_COLUMNS} FROM products WHERE asin = ANY(%s)",
                    (target_asins,),
                )
                rows = cur.fetchall()
                by_asin = {r[1]: row_to_product(r) for r in rows}
                for a in target_asins:
                    prod = by_asin.get(a)
                    if prod:
                        items.append(prod)

            if len(items) < limit:
                cur.execute(
                    "SELECT department, category FROM products WHERE asin = %s",
                    (asin,),
                )
                source_prod = cur.fetchone()
                dept = source_prod[0] if source_prod else "Men"
                cat = source_prod[1] if source_prod else ""

                cur.execute(
                    f"""
                    SELECT {PRODUCT_COLUMNS}
                    FROM products
                    WHERE asin != %s AND (category = %s OR department = %s)
                    ORDER BY rating_count DESC, avg_rating DESC
                    LIMIT %s
                    """,
                    (asin, cat, dept, limit),
                )
                fb_rows = cur.fetchall()
                seen = {item["asin"] for item in items}
                seen.add(asin)
                for r in fb_rows:
                    fb_prod = row_to_product(r)
                    if fb_prod["asin"] not in seen:
                        items.append(fb_prod)
                        seen.add(fb_prod["asin"])
                        if len(items) >= limit:
                            break
                if not rec_asins:
                    strategy = "category_popularity_fallback"

    return {
        "asin": asin,
        "items": items,
        "count": len(items),
        "strategy": strategy,
    }


@app.get("/recs/session")
def recs_session(
    session_id: str = Query(min_length=1, max_length=128),
    limit: int = Query(6, ge=1, le=24),
):
    """Session-based recommendations (M7).

    Personalizes based on the session's recently viewed/added items from `events`.
    Falls back to popular baseline if cold.
    """
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT asin FROM events
                WHERE session_id = %s
                  AND event_type IN ('view_product', 'add_to_cart')
                  AND asin IS NOT NULL
                ORDER BY id DESC
                LIMIT 5
                """,
                (session_id,),
            )
            seen_asins = [r[0] for r in cur.fetchall()]

            if not seen_asins:
                cur.execute(
                    f"""
                    SELECT {PRODUCT_COLUMNS}
                    FROM products
                    ORDER BY rating_count DESC, avg_rating DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                items = [row_to_product(r) for r in cur.fetchall()]
                return {
                    "session_id": session_id,
                    "items": items,
                    "count": len(items),
                    "strategy": "cold_popularity",
                }

            cur.execute(
                """
                SELECT asin, recs FROM item_recommendations
                WHERE asin = ANY(%s)
                """,
                (seen_asins,),
            )
            recs_by_asin = {r[0]: r[1] for r in cur.fetchall()}

            seen_set = set(seen_asins)
            candidate_asins = []
            cand_seen = set()

            for viewed in seen_asins:
                for target in recs_by_asin.get(viewed, []):
                    if target not in seen_set and target not in cand_seen:
                        candidate_asins.append(target)
                        cand_seen.add(target)
                        if len(candidate_asins) >= limit:
                            break
                if len(candidate_asins) >= limit:
                    break

            if len(candidate_asins) < limit:
                cur.execute(
                    f"""
                    SELECT asin FROM products
                    WHERE asin != ALL(%s)
                    ORDER BY rating_count DESC
                    LIMIT %s
                    """,
                    (list(seen_set.union(cand_seen)), limit - len(candidate_asins)),
                )
                for r in cur.fetchall():
                    candidate_asins.append(r[0])

            cur.execute(
                f"SELECT {PRODUCT_COLUMNS} FROM products WHERE asin = ANY(%s)",
                (candidate_asins,),
            )
            rows = cur.fetchall()
            by_asin = {r[1]: row_to_product(r) for r in rows}
            items = [by_asin[a] for a in candidate_asins if a in by_asin]

    return {
        "session_id": session_id,
        "items": items,
        "count": len(items),
        "strategy": "session_cf",
    }


# ── Reviews ───────────────────────────────────────────────────────────────────

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


@app.get("/products/{product_id}/reviews")
def get_product_reviews(product_id: int, limit: int = Query(10, ge=1, le=50)):
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT asin FROM products WHERE id = %s", (product_id,))
            row = cur.fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Product not found")
            return _fetch_reviews_for_asin(cur, row[0], limit=limit)


@app.get("/reviews/{asin}")
def get_asin_reviews(asin: str, limit: int = Query(10, ge=1, le=50)):
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM products WHERE asin = %s", (asin,))
            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="Product not found")
            return _fetch_reviews_for_asin(cur, asin, limit=limit)


# ── AI Copilot (Tri-Modal Multi-Agent Orchestrator) ──────────────────────────

class CopilotMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str = Field(min_length=1, max_length=2000)
    imageUrl: str | None = Field(default=None, max_length=8_000_000)


class CopilotChatIn(BaseModel):
    session_id: str | None = Field(default=None, max_length=64)
    messages: list[CopilotMessage] = Field(min_length=1, max_length=20)
    image_url: str | None = Field(default=None, max_length=8_000_000)


@app.post("/copilot/chat")
async def copilot_chat(payload: CopilotChatIn):
    """End-to-End Multimodal AI Shopping Copilot (Admin Toko Marcell).

    Covers the 7 core e-commerce use cases:
    1. Visual Search & Style Matching
    2. Sizing & Fit Consultation (TB/BB + Brand Deviations)
    3. Outfit Builder under Budget (Top + Bottom + Shoes <= Budget)
    4. Fabric, Material & Construction Q&A
    5. Head-to-Head Product Comparison (A vs B)
    6. Aspect-Based Social Proof & Review Highlights
    7. Store Operations, Shipping & QRIS Demo Rules

    Checkout is a portfolio demo simulation: there is no fulfillment, courier or
    order tracking, so deliberately no order-status tool is exposed to the model.
    """
    from agent import chat_copilot

    image_ref = payload.image_url
    if not image_ref and payload.messages:
        image_ref = payload.messages[-1].imageUrl

    res = await chat_copilot(
        messages=[{"role": m.role, "content": m.content} for m in payload.messages],
        session_id=payload.session_id,
        image_url=image_ref,
        db_url=DATABASE_URL,
    )
    return res


@app.get("/copilot/tools")
def copilot_tools():
    """Returns the deterministic tool definitions used by the AI Copilot."""
    from agent import TOOLS_SCHEMA
    return {"tools": TOOLS_SCHEMA}


