#!/usr/bin/env python3
"""
Seed Postgres with the catalog produced by pipelines/build_catalog.py.

Offline by design (PLAN.md §3: "Offline Python -> seed"): the FastAPI app creates
the schema but never writes catalog rows, and the Next.js frontend never opens a
database connection at all.

Run
  python3 pipelines/seed.py            # upsert by asin
  python3 pipelines/seed.py --reset    # wipe the table first (after regenerating)
"""

import argparse
import json
import os
import sys
from pathlib import Path

import psycopg
from psycopg.types.json import Json

HERE = Path(__file__).resolve().parent
SEED_FILE = HERE.parent / "data" / "processed" / "products.jsonl"
DEFAULT_DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://toko:toko@localhost:5432/toko"
)

COLUMNS = (
    "id", "asin", "title", "brand", "price_usd", "price_idr", "department", "category",
    "category_path", "description", "features", "image_url", "avg_rating", "rating_count",
    "also_buy", "also_view",
)
PLACEHOLDERS = ", ".join(["%s"] * len(COLUMNS))

# `id` is intentionally not updated: it is the stable integer the shop and the
# cart already reference. Everything else refreshes from the pipeline output.
UPDATABLE = [c for c in COLUMNS if c not in ("id", "asin")]
UPSERT = f"""
INSERT INTO products ({", ".join(COLUMNS)}) VALUES ({PLACEHOLDERS})
ON CONFLICT (asin) DO UPDATE SET
    {", ".join(f"{c} = EXCLUDED.{c}" for c in UPDATABLE)}
"""


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed-file", default=str(SEED_FILE))
    ap.add_argument("--database-url", default=DEFAULT_DATABASE_URL)
    ap.add_argument("--reset", action="store_true",
                    help="DELETE all products first (required after the catalog changes size)")
    args = ap.parse_args()

    seed_path = Path(args.seed_file)
    if not seed_path.exists():
        log(f"missing {seed_path}\nrun: bash pipelines/download_data.sh && "
            f"python3 pipelines/build_catalog.py")
        return 1

    products = [json.loads(line) for line in seed_path.open(encoding="utf-8") if line.strip()]
    log(f"read {len(products):,} products from {seed_path.name}")

    rows = [
        (
            product["id"], product["asin"], product["title"], product["brand"],
            product["priceUsd"], product["priceIdr"], product["department"], product["category"],
            Json(product["categoryPath"]), product["description"], Json(product["features"]),
            product["imageUrl"], product["avgRating"], product["ratingCount"],
            Json(product["alsoBuy"]), Json(product["alsoView"]),
        )
        for product in products
    ]

    with psycopg.connect(args.database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('products')")
            if cur.fetchone()[0] is None:
                log("table 'products' does not exist — start the API once so it creates "
                    "the schema: cd api && docker compose up -d")
                return 1

            if args.reset:
                cur.execute("DELETE FROM products")
                log(f"deleted {cur.rowcount:,} existing rows")

            cur.executemany(UPSERT, rows)
            conn.commit()

            cur.execute(
                """
                SELECT COUNT(*), COUNT(brand), COUNT(description),
                       COUNT(NULLIF(category, '')), MIN(price_idr), MAX(price_idr)
                FROM products
                """
            )
            total, with_brand, with_desc, with_cat, min_price, max_price = cur.fetchone()

    log(f"products in DB: {total:,}")
    log(f"  with brand:       {with_brand:,}")
    log(f"  with description: {with_desc:,}")
    log(f"  with category:    {with_cat:,}")
    log(f"  price range IDR:  {min_price:,} – {max_price:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
