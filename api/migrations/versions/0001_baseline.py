"""Baseline schema (pre-Phase 4): catalog, funnel, orders, recs, reviews, RAG knowledge.

This is the schema that ``init_db()`` used to build with CREATE TABLE IF NOT
EXISTS + information_schema column diffing. Every statement stays idempotent
(IF NOT EXISTS) on purpose: databases created before Alembic was introduced —
the committed seed dump, Neon — have these objects but no ``alembic_version``
table, so ``alembic upgrade head`` must be a safe no-op for them rather than
requiring a manual ``alembic stamp``.

Not managed here: the LangGraph checkpoint tables (``PostgresSaver.setup()``)
and their ``copilot_thread_activity`` sidecar, both created at runtime by
``copilot_memory.py``.

Revision ID: 0001
Revises:
Create Date: 2026-10-22
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # A v1 demo database had a 4-row `products` table without `asin`; it is not
    # migratable and was always dropped by the old init_db().
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.tables
                       WHERE table_schema = current_schema() AND table_name = 'products')
               AND NOT EXISTS (SELECT 1 FROM information_schema.columns
                               WHERE table_schema = current_schema()
                                 AND table_name = 'products' AND column_name = 'asin') THEN
                DROP TABLE products;
            END IF;
        END $$
        """
    )

    # ── Catalog ──────────────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS products (
            id              INTEGER PRIMARY KEY,
            asin            TEXT NOT NULL UNIQUE,
            title           TEXT NOT NULL,
            brand           TEXT,
            price_usd       NUMERIC(10, 2) NOT NULL,
            price_idr       INTEGER NOT NULL,
            department      TEXT NOT NULL DEFAULT 'Other',
            category        TEXT NOT NULL DEFAULT '',
            category_path   JSONB NOT NULL DEFAULT '[]'::jsonb,
            description     TEXT,
            features        JSONB NOT NULL DEFAULT '[]'::jsonb,
            image_url       TEXT,
            avg_rating      NUMERIC(3, 2),
            rating_count    INTEGER NOT NULL DEFAULT 0,
            also_buy        JSONB NOT NULL DEFAULT '[]'::jsonb,
            also_view       JSONB NOT NULL DEFAULT '[]'::jsonb,
            embedding       vector(384),
            image_embedding vector(512)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS products_department_idx ON products (department)")
    op.execute("CREATE INDEX IF NOT EXISTS products_category_idx ON products (category)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS products_embedding_idx ON products USING hnsw (embedding vector_cosine_ops)"
    )
    # The visual-search column is retired by revision 0005. Keep this index
    # creation conditional so a database that is already at head can still be
    # adopted after losing its alembic_version row.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_schema = current_schema()
                         AND table_name = 'products' AND column_name = 'image_embedding') THEN
                CREATE INDEX IF NOT EXISTS products_image_embedding_idx
                ON products USING hnsw (image_embedding vector_cosine_ops);
            END IF;
        END $$
        """
    )

    # ── Funnel events ────────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id            BIGSERIAL PRIMARY KEY,
            session_id    TEXT NOT NULL,
            event_type    TEXT NOT NULL,
            asin          TEXT,
            query         TEXT,
            results_count INTEGER,
            qty           INTEGER,
            price_idr     INTEGER,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS events_session_idx ON events (session_id)")
    op.execute("CREATE INDEX IF NOT EXISTS events_type_created_idx ON events (event_type, created_at DESC)")

    # ── Orders ───────────────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (
            id         BIGSERIAL PRIMARY KEY,
            token      TEXT NOT NULL UNIQUE,
            total_idr  INTEGER NOT NULL,
            item_count INTEGER NOT NULL,
            status     TEXT NOT NULL DEFAULT 'paid',
            session_id TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS orders_created_idx ON orders (created_at DESC)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS order_items (
            id             BIGSERIAL PRIMARY KEY,
            order_id       BIGINT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
            asin           TEXT NOT NULL,
            title          TEXT NOT NULL,
            qty            INTEGER NOT NULL,
            unit_price_idr INTEGER NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS order_items_order_idx ON order_items (order_id)")

    # ── Recommendations ──────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS item_recommendations (
            asin TEXT PRIMARY KEY,
            recs JSONB NOT NULL DEFAULT '[]'::jsonb
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS item_recs_asin_idx ON item_recommendations (asin)")

    # ── Reviews ──────────────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS reviews (
            id          SERIAL PRIMARY KEY,
            asin        TEXT NOT NULL,
            rating      NUMERIC(2, 1) NOT NULL,
            summary     TEXT,
            comment     TEXT NOT NULL,
            author      TEXT NOT NULL DEFAULT 'Amazon Customer',
            verified    BOOLEAN NOT NULL DEFAULT true,
            review_date TEXT,
            created_at  TIMESTAMPTZ DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS reviews_asin_idx ON reviews (asin)")
    op.execute("CREATE INDEX IF NOT EXISTS reviews_rating_idx ON reviews (rating)")

    # ── RAG knowledge base ───────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS store_knowledge (
            id        SERIAL PRIMARY KEY,
            category  TEXT NOT NULL,
            title     TEXT NOT NULL,
            content   TEXT NOT NULL,
            embedding vector(384)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS store_knowledge_category_idx ON store_knowledge (category)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS store_knowledge_embedding_idx "
        "ON store_knowledge USING hnsw (embedding vector_cosine_ops)"
    )

    # Hypothetical questions: each row's embedding points at a parent chunk.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS store_knowledge_questions (
            id        SERIAL PRIMARY KEY,
            chunk_id  INTEGER NOT NULL REFERENCES store_knowledge(id) ON DELETE CASCADE,
            question  TEXT NOT NULL,
            lang      TEXT NOT NULL DEFAULT 'id',
            embedding vector(384)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS store_knowledge_q_chunk_idx ON store_knowledge_questions (chunk_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS store_knowledge_q_embedding_idx "
        "ON store_knowledge_questions USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    # Destructive: drops every table this revision owns (and their data).
    # The vector extension is left in place; other objects may depend on it.
    op.execute("DROP TABLE IF EXISTS store_knowledge_questions")
    op.execute("DROP TABLE IF EXISTS store_knowledge")
    op.execute("DROP TABLE IF EXISTS reviews")
    op.execute("DROP TABLE IF EXISTS item_recommendations")
    op.execute("DROP TABLE IF EXISTS order_items")
    op.execute("DROP TABLE IF EXISTS orders")
    op.execute("DROP TABLE IF EXISTS events")
    op.execute("DROP TABLE IF EXISTS products")
