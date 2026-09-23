import os
import secrets
from typing import Literal

import psycopg
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

app = FastAPI(title="Toko Marcell API")

origins = [
    origin.strip()
    for origin in os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE_URL = os.environ["DATABASE_URL"]

# ── Schema ────────────────────────────────────────────────────────────────────
# ONE source of truth. `CREATE TABLE` is generated from this list, PRODUCT_COLUMNS
# is derived from it, and init_db() adds whatever an existing database is missing.
# Add a column here and nowhere else.
#
# Rule: this list is APPEND-ONLY, and a new column must be nullable or carry a
# DEFAULT. `ALTER TABLE ADD COLUMN` cannot invent a value for existing rows, so a
# bare `NOT NULL` column would fail on any database that already has data.
#
# The order matters: row_to_product() maps by index.
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

# Phase C. Kept out of SCHEMA because it needs the pgvector extension, which a host
# may not have. If it is missing the catalog still works — only search degrades.
VECTOR_COLUMN: tuple[str, str] = ("embedding", "vector(384)")

INDEXES: list[tuple[str, str]] = [
    ("products_department_idx", "products (department)"),
    ("products_category_idx", "products (category)"),
    # No vector index here on purpose. An HNSW index is cheaper to build AFTER the
    # table is seeded — building it first makes every INSERT maintain the graph.
    # Create it once, after pipelines/seed.py:
    #   CREATE INDEX products_embedding_idx ON products
    #     USING hnsw (embedding vector_cosine_ops);
]

# ── Funnel events (M2/M2b) ────────────────────────────────────────────────────
# One row per shopper action, grouped into a visit by `session_id`: an anonymous
# id generated in the browser, NOT a login. It is the only identity this demo
# has, and it is enough to compute per-session funnel rates (PLAN §4).
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

# ── Orders (B5: server-side checkout confirm) ─────────────────────────────────
# The client sends {asin, qty} and nothing else. The server prices the order from
# `products` itself, because a client-supplied total is a client-supplied price:
# a tampered request could otherwise "pay" Rp 1.
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

# Derived, so the SELECT can never drift from the table definition. `embedding` is
# excluded by construction: 384 floats per product would bloat every response and
# the storefront never needs them.
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
            else:
                print("[init_db] pgvector unavailable — no embedding column, search disabled")

            # Interpolated, not parameterised, because these are column definitions
            # rather than values. SCHEMA is a module constant, never user input.
            col_defs = ",\n                    ".join(f"{name} {ddl}" for name, ddl in columns)
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS products (
                    {col_defs}
                )
                """
            )

            # Re-read: the table may have just been created, in which case every
            # column already exists and the loop below correctly adds nothing.
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
            # orders before order_items: the second references the first, and its
            # index has to be created with it, not with orders.
            _ensure_table(cur, "orders", ORDERS_SCHEMA, ORDERS_INDEXES)
            _ensure_table(cur, "order_items", ORDER_ITEMS_SCHEMA, ORDER_ITEMS_INDEXES)

            conn.commit()


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


@app.get("/categories")
def categories():
    """Facets for the shop filter chips — real counts, so the UI never invents one."""
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

    return {"departments": departments, "categories": top_categories}


@app.get("/products")
def products(
    limit: int = Query(24, ge=1, le=24),
    offset: int = Query(0, ge=0),
    department: str | None = None,
    category: str | None = None,
):
    """Paginated catalog. `department` and `category` are exact-match facets."""
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
def get_product(product_id: int):
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
    wanted = {item.asin: item.qty for item in payload.items}

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT asin, title, price_idr FROM products WHERE asin = ANY(%s)",
                (list(wanted),),
            )
            found = {row[0]: (row[1], row[2]) for row in cur.fetchall()}

            unknown = sorted(set(wanted) - set(found))
            if unknown:
                # 400, not 404: the request is wrong about the catalog, and the
                # detail names every bad asin so the bug is fixable from the log.
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

            token = secrets.token_urlsafe(16)
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

            # The purchase event is written HERE, not by the browser: the server
            # is the only party that knows the order really exists. This is what
            # makes the funnel's last step authoritative instead of a claim.
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
