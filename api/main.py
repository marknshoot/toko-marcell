import os

import psycopg
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

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
