"""
Toko Marcell API — thin application factory.

Creates the FastAPI app, wires middleware/CORS/lifespan, and includes routers.
All business logic lives in the ``routers/`` package, database access in ``db.py``,
and configuration in ``config.py``.

Startup: ``uvicorn main:app --host 0.0.0.0 --port 8000``
"""

import logging
from contextlib import asynccontextmanager

import psycopg
from config import get_settings
from db import close_pool, open_pool
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)

# ── Schema definitions (kept here for init_db; identical to the old main.py) ─

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


# ── Database migration ───────────────────────────────────────────────────────

def _enable_pgvector(cur) -> bool:
    cur.execute("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")
    if cur.fetchone() is None:
        return False
    cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
    return True


def _ensure_table(cur, table: str, columns, indexes):
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
        logger.info("[init_db] added missing columns to %s: %s", table, ", ".join(added))

    for idx_name, idx_target in indexes:
        cur.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {idx_target}")


def init_db():
    """Create or migrate the schema."""
    settings = get_settings()
    with psycopg.connect(settings.DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'products'"
            )
            existing = {row[0] for row in cur.fetchall()}

            if existing and "asin" not in existing:
                logger.info("[init_db] dropping legacy v1 products table (4 demo rows)")
                cur.execute("DROP TABLE products")

            columns = list(SCHEMA)
            if _enable_pgvector(cur):
                columns.append(VECTOR_COLUMN)
                columns.append(IMAGE_VECTOR_COLUMN)
            else:
                logger.warning("[init_db] pgvector unavailable — no embedding column, search disabled")

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
                logger.info("[init_db] added missing columns: %s", ", ".join(added))

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
            seed_knowledge(settings.DATABASE_URL)
        except Exception as e:
            logger.warning("[init_db] knowledge seed error: %s", e)


# ── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(levelname)s %(name)s: %(message)s",
        force=True,
    )
    init_db()
    open_pool()
    yield
    close_pool()


# ── App creation ─────────────────────────────────────────────────────────────

app = FastAPI(title="Toko Marcell API", lifespan=lifespan)

settings = get_settings()
cors_origins_raw = settings.CORS_ORIGINS
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

# ── Include routers ──────────────────────────────────────────────────────────

from routers import catalog, checkout, copilot, events, health, recs, reviews, search  # noqa: E402

app.include_router(health.router)
app.include_router(catalog.router)
app.include_router(search.router)
app.include_router(recs.router)
app.include_router(reviews.router)
app.include_router(events.router)
app.include_router(checkout.router)
app.include_router(copilot.router)
