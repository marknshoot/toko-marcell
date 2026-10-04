#!/usr/bin/env python3
"""
Generate dense sentence embeddings for all products in the catalog
and write them to a Postgres column with an HNSW cosine index.

Default: sentence-transformers/all-MiniLM-L6-v2 (384-d) → ``products.embedding``

With ``--model`` and ``--column`` flags, can embed using any fastembed-supported
model into any vector(384) column (e.g. the multilingual A/B variant).

Run:
  python3 pipelines/embed_catalog.py
  python3 pipelines/embed_catalog.py --model sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 --column embedding_ml
"""

import argparse
import os
import sys
import time

import psycopg
from fastembed import TextEmbedding

DEFAULT_DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://toko:toko@localhost:5432/toko"
)
BATCH_SIZE = 128
DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_COLUMN = "embedding"


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def format_vector(vec) -> str:
    """Format float array into Postgres vector string literal: [v1, v2, ...]"""
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def build_text(title, brand, category, features, description) -> str:
    parts = []
    if title:
        parts.append(title)
    if brand:
        parts.append(brand)
    if category:
        parts.append(category)
    if features and isinstance(features, list):
        parts.extend(features)
    if description:
        # truncate very long descriptions to avoid huge token spans
        parts.append(description[:500])
    return " ".join(parts).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--database-url", default=DEFAULT_DATABASE_URL)
    ap.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    ap.add_argument("--limit", type=int, default=0, help="Optional limit for dry runs")
    ap.add_argument("--model", default=DEFAULT_MODEL,
                    help="fastembed model name (must produce same dimension as column)")
    ap.add_argument("--column", default=DEFAULT_COLUMN,
                    help="Target column in products table (e.g. 'embedding' or 'embedding_ml')")
    args = ap.parse_args()

    model_name = args.model
    column = args.column

    started = time.perf_counter()
    log(f"loading embedding model: {model_name}...")
    model = TextEmbedding(model_name=model_name)
    log("model ready.")

    with psycopg.connect(args.database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('products')")
            if cur.fetchone()[0] is None:
                log("table 'products' does not exist. Run seed.py first.")
                return 1

            query = """
                SELECT id, title, brand, category, features, description
                FROM products
                ORDER BY id
            """
            if args.limit > 0:
                query += f" LIMIT {args.limit}"

            cur.execute(query)
            rows = cur.fetchall()
            total = len(rows)
            log(f"fetched {total:,} products from DB to embed")

            updated = 0
            for i in range(0, total, args.batch_size):
                chunk = rows[i : i + args.batch_size]
                doc_ids = [row[0] for row in chunk]
                texts = [
                    build_text(row[1], row[2], row[3], row[4], row[5])
                    for row in chunk
                ]

                embeddings = list(model.embed(texts))

                update_params = [
                    (format_vector(vec), doc_id)
                    for doc_id, vec in zip(doc_ids, embeddings)
                ]
                cur.executemany(
                    f"UPDATE products SET {column} = %s WHERE id = %s",
                    update_params,
                )
                conn.commit()
                updated += len(chunk)
                log(f"  progress: {updated:,} / {total:,} products embedded ({round(updated/total*100)}%)")

            idx_name = f"products_{column}_idx"
            log(f"building HNSW cosine index ({idx_name})...")
            idx_start = time.perf_counter()
            cur.execute(
                f"""
                CREATE INDEX IF NOT EXISTS {idx_name} ON products
                  USING hnsw ({column} vector_cosine_ops)
                """
            )
            conn.commit()
            log(f"HNSW index built in {round(time.perf_counter() - idx_start, 2)}s")

            cur.execute(f"SELECT count(*), count({column}) FROM products")
            total_db, embedded_db = cur.fetchone()

    total_time = round(time.perf_counter() - started, 2)
    log(f"Done! {embedded_db:,} of {total_db:,} products have embeddings in '{column}' ({model_name}) in {total_time}s.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
