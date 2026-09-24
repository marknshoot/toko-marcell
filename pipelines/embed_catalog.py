#!/usr/bin/env python3
"""
Generate 384-dimensional dense sentence embeddings for all products in the catalog
and write them to the Postgres `products.embedding` column with an HNSW cosine index.

Model: sentence-transformers/all-MiniLM-L6-v2 (via fastembed ONNX runtime)
Dimension: 384
Text representation: title + brand + category + features + description

Run:
  python3 pipelines/embed_catalog.py
"""

import argparse
import os
import sys
import time
from pathlib import Path

import psycopg
from fastembed import TextEmbedding

DEFAULT_DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://toko:toko@localhost:5432/toko"
)
BATCH_SIZE = 128
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


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
    args = ap.parse_args()

    started = time.perf_counter()
    log(f"loading embedding model: {MODEL_NAME}...")
    model = TextEmbedding(model_name=MODEL_NAME)
    log("model ready.")

    with psycopg.connect(args.database_url) as conn:
        with conn.cursor() as cur:
            # Check table exists
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

            # Process in batches
            updated = 0
            for i in range(0, total, args.batch_size):
                chunk = rows[i : i + args.batch_size]
                doc_ids = [row[0] for row in chunk]
                texts = [
                    build_text(row[1], row[2], row[3], row[4], row[5])
                    for row in chunk
                ]

                # Generate dense embeddings (batch)
                embeddings = list(model.embed(texts))

                # Update database
                update_params = [
                    (format_vector(vec), doc_id)
                    for doc_id, vec in zip(doc_ids, embeddings)
                ]
                cur.executemany(
                    "UPDATE products SET embedding = %s WHERE id = %s",
                    update_params,
                )
                conn.commit()
                updated += len(chunk)
                log(f"  progress: {updated:,} / {total:,} products embedded ({round(updated/total*100)}%)")

            # Build HNSW index on the embedded products
            log("building HNSW cosine index (products_embedding_idx)...")
            idx_start = time.perf_counter()
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS products_embedding_idx ON products
                  USING hnsw (embedding vector_cosine_ops)
                """
            )
            conn.commit()
            log(f"HNSW index built in {round(time.perf_counter() - idx_start, 2)}s")

            # Verify with a quick test query
            cur.execute("SELECT count(*), count(embedding) FROM products")
            total_db, embedded_db = cur.fetchone()

    total_time = round(time.perf_counter() - started, 2)
    log(f"Done! {embedded_db:,} of {total_db:,} products have 384-dim embeddings in {total_time}s.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
