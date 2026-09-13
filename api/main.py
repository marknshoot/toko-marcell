import os
import psycopg
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

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


def init_db():
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    price_idr INTEGER NOT NULL,
                    category TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    image_url TEXT
                )
                """
            )
            cur.execute(
                "ALTER TABLE products ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT ''"
            )
            cur.execute(
                "ALTER TABLE products ADD COLUMN IF NOT EXISTS description TEXT NOT NULL DEFAULT ''"
            )
            cur.execute(
                "ALTER TABLE products ADD COLUMN IF NOT EXISTS image_url TEXT"
            )
            cur.execute(
                """
                INSERT INTO products (id, title, price_idr, category, description) VALUES
                (1, 'Cotton Tee', 129000, 'Tops', 'Soft cotton crew-neck tee.'),
                (2, 'Linen Shirt', 249000, 'Tops', 'Breathable linen button-up.'),
                (3, 'Chino Pants', 299000, 'Bottoms', 'Everyday stretch chinos.'),
                (4, 'Canvas Tote', 99000, 'Accessories', 'Sturdy canvas tote bag.')
                ON CONFLICT (id) DO UPDATE SET
                    title = EXCLUDED.title,
                    price_idr = EXCLUDED.price_idr,
                    category = EXCLUDED.category,
                    description = EXCLUDED.description
                """
            )
            conn.commit()


init_db()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/products")
def products(limit: int = 24, offset: int = 0, category: str | None = None):
    if limit < 1:
        limit = 1
    if limit > 24:
        limit = 24
    if offset < 0:
        offset = 0

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            if category:
                cur.execute(
                    "SELECT COUNT(*) FROM products WHERE category = %s",
                    (category,),
                )
                total = cur.fetchone()[0]
                cur.execute(
                    """
                    SELECT id, title, price_idr, category, description, image_url
                    FROM products
                    WHERE category = %s
                    ORDER BY id
                    LIMIT %s OFFSET %s
                    """,
                    (category, limit, offset),
                )
                rows = cur.fetchall()
            else:
                cur.execute("SELECT COUNT(*) FROM products")
                total = cur.fetchone()[0]
                cur.execute(
                    """
                    SELECT id, title, price_idr, category, description, image_url
                    FROM products
                    ORDER BY id
                    LIMIT %s OFFSET %s
                    """,
                    (limit, offset),
                )
                rows = cur.fetchall()

    result = []
    for row in rows:
        result.append(
            {
                "id": row[0],
                "title": row[1],
                "priceIdr": row[2],
                "category": row[3],
                "description": row[4],
                "imageUrl": row[5],
            }
        )
    return {
        "items": result,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@app.get("/products/{product_id}")
def get_product(product_id: int):
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, title, price_idr, category, description, image_url
                FROM products
                WHERE id = %s
                """,
                (product_id,),
            )
            row = cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Product not found")

    return {
        "id": row[0],
        "title": row[1],
        "priceIdr": row[2],
        "category": row[3],
        "description": row[4],
        "imageUrl": row[5],
    }
