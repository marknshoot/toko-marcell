#!/usr/bin/env python3
"""
Migrate local PostgreSQL database (toko) to Neon Serverless PostgreSQL.
Replicates schema, 6,000 catalog products, 5,378 champion VLM image embeddings,
text embeddings, and HNSW indexes.

Usage:
  python manual/pipelines/migrate_to_neon.py --target-url="postgresql://user:pass@ep-xyz.neon.tech/neondb?sslmode=require"
"""

import argparse
import sys
import time
from typing import List, Tuple

import psycopg
from psycopg.rows import dict_row

LOCAL_DB_URL = "postgresql://toko:toko@localhost:5432/toko"


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def main():
    parser = argparse.ArgumentParser(description="Migrate Toko Marcell to Neon PostgreSQL")
    parser.add_argument("--source-url", default=LOCAL_DB_URL, help="Local Postgres URL")
    parser.add_argument("--target-url", required=True, help="Neon Postgres Connection String")
    args = parser.parse_args()

    t_start = time.perf_counter()
    log("=== Starting Toko Marcell Database Migration to Neon ===")

    # 1. Connect to Source
    log(f"Connecting to source database: {args.source_url}...")
    try:
        src_conn = psycopg.connect(args.source_url)
    except Exception as e:
        log(f"Failed to connect to source database: {e}")
        return 1

    # 2. Connect to Target (Neon)
    log("Connecting to target Neon database...")
    try:
        tgt_conn = psycopg.connect(args.target_url)
    except Exception as e:
        log(f"Failed to connect to Neon target database: {e}")
        return 1

    with src_conn, tgt_conn:
        with src_conn.cursor(row_factory=dict_row) as src_cur, tgt_conn.cursor() as tgt_cur:
            # 3. Enable pgvector on Neon
            log("Enabling pgvector extension on Neon...")
            tgt_cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            tgt_conn.commit()

            # 4. Create Tables on Neon
            log("Creating tables (products, events, orders, order_items, knowledge)...")
            tgt_cur.execute("""
                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY,
                    asin TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    brand TEXT,
                    price_usd NUMERIC(10, 2) NOT NULL,
                    price_idr INTEGER NOT NULL,
                    department TEXT NOT NULL DEFAULT 'Other',
                    category TEXT NOT NULL DEFAULT '',
                    category_path JSONB NOT NULL DEFAULT '[]'::jsonb,
                    description TEXT,
                    features JSONB NOT NULL DEFAULT '[]'::jsonb,
                    image_url TEXT,
                    avg_rating NUMERIC(3, 2),
                    rating_count INTEGER NOT NULL DEFAULT 0,
                    also_buy JSONB NOT NULL DEFAULT '[]'::jsonb,
                    also_view JSONB NOT NULL DEFAULT '[]'::jsonb,
                    embedding vector(384),
                    image_embedding vector(512)
                );

                CREATE TABLE IF NOT EXISTS events (
                    id BIGSERIAL PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    asin TEXT,
                    query TEXT,
                    results_count INTEGER,
                    qty INTEGER,
                    price_idr INTEGER,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );

                CREATE TABLE IF NOT EXISTS orders (
                    id BIGSERIAL PRIMARY KEY,
                    token TEXT NOT NULL UNIQUE,
                    total_idr INTEGER NOT NULL,
                    item_count INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'paid',
                    session_id TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );

                CREATE TABLE IF NOT EXISTS order_items (
                    id BIGSERIAL PRIMARY KEY,
                    order_id BIGINT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
                    asin TEXT NOT NULL,
                    title TEXT NOT NULL,
                    qty INTEGER NOT NULL,
                    unit_price_idr INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS knowledge (
                    id BIGSERIAL PRIMARY KEY,
                    category TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
            """)
            tgt_conn.commit()

            # 5. Fetch rows from source products
            log("Fetching all products and vector embeddings from local database...")
            src_cur.execute("""
                SELECT 
                    id, asin, title, brand, price_usd, price_idr, department, category,
                    category_path, description, features, image_url, avg_rating, rating_count,
                    also_buy, also_view,
                    embedding::text as embedding_str,
                    image_embedding::text as image_embedding_str
                FROM products
                ORDER BY id ASC;
            """)
            products = src_cur.fetchall()
            total_prods = len(products)
            log(f"Fetched {total_prods:,} products to migrate.")

            # 6. Bulk Insert to Neon
            log("Migrating products to Neon in batches of 500...")
            insert_sql = """
                INSERT INTO products (
                    id, asin, title, brand, price_usd, price_idr, department, category,
                    category_path, description, features, image_url, avg_rating, rating_count,
                    also_buy, also_view, embedding, image_embedding
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s::vector, %s::vector
                )
                ON CONFLICT (id) DO UPDATE SET
                    asin = EXCLUDED.asin,
                    title = EXCLUDED.title,
                    brand = EXCLUDED.brand,
                    price_usd = EXCLUDED.price_usd,
                    price_idr = EXCLUDED.price_idr,
                    department = EXCLUDED.department,
                    category = EXCLUDED.category,
                    category_path = EXCLUDED.category_path,
                    description = EXCLUDED.description,
                    features = EXCLUDED.features,
                    image_url = EXCLUDED.image_url,
                    avg_rating = EXCLUDED.avg_rating,
                    rating_count = EXCLUDED.rating_count,
                    also_buy = EXCLUDED.also_buy,
                    also_view = EXCLUDED.also_view,
                    embedding = EXCLUDED.embedding,
                    image_embedding = EXCLUDED.image_embedding;
            """

            from psycopg.types.json import Json
            batch_size = 500
            for i in range(0, total_prods, batch_size):
                chunk = products[i : i + batch_size]
                params = [
                    (
                        r["id"], r["asin"], r["title"], r["brand"], r["price_usd"], r["price_idr"],
                        r["department"], r["category"],
                        Json(r["category_path"]) if r["category_path"] is not None else None,
                        r["description"],
                        Json(r["features"]) if r["features"] is not None else None,
                        r["image_url"], r["avg_rating"], r["rating_count"],
                        Json(r["also_buy"]) if r["also_buy"] is not None else None,
                        Json(r["also_view"]) if r["also_view"] is not None else None,
                        r["embedding_str"], r["image_embedding_str"]
                    )
                    for r in chunk
                ]
                tgt_cur.executemany(insert_sql, params)
                tgt_conn.commit()
                log(f"  Migrated {min(i + batch_size, total_prods):,}/{total_prods:,} products...")

            # 7. Check Knowledge table
            src_cur.execute("SELECT count(*) as count FROM knowledge;")
            k_count = src_cur.fetchone()["count"]
            if k_count > 0:
                log(f"Migrating {k_count} knowledge base entries...")
                src_cur.execute("SELECT category, topic, content FROM knowledge;")
                k_rows = src_cur.fetchall()
                tgt_cur.executemany(
                    "INSERT INTO knowledge (category, topic, content) VALUES (%s, %s, %s);",
                    [(r["category"], r["topic"], r["content"]) for r in k_rows]
                )
                tgt_conn.commit()

            # 8. Create Indexes
            log("Building HNSW cosine indexes on Neon (image_embedding & embedding)...")
            tgt_cur.execute("""
                CREATE INDEX IF NOT EXISTS products_department_idx ON products (department);
                CREATE INDEX IF NOT EXISTS products_category_idx ON products (category);
                CREATE INDEX IF NOT EXISTS events_session_idx ON events (session_id);
                CREATE INDEX IF NOT EXISTS events_type_created_idx ON events (event_type, created_at DESC);
                CREATE INDEX IF NOT EXISTS products_image_embedding_idx ON products USING hnsw (image_embedding vector_cosine_ops);
                CREATE INDEX IF NOT EXISTS products_embedding_idx ON products USING hnsw (embedding vector_cosine_ops);
            """)
            tgt_conn.commit()

            # 9. Verification count
            tgt_cur.execute("""
                SELECT 
                    count(*) as total, 
                    count(image_embedding) as with_image_vec,
                    count(embedding) as with_text_vec
                FROM products;
            """)
            verified = tgt_cur.fetchone()
            log(f"Neon verification: Total products: {verified[0]:,}, with VLM vector: {verified[1]:,}, with text vector: {verified[2]:,}")

    elapsed = time.perf_counter() - t_start
    log(f"✨ MIGRATION TO NEON COMPLETED SUCCESSFULLY in {elapsed:.1f}s!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
