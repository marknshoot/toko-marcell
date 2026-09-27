#!/usr/bin/env python3
"""
Extract top customer reviews for catalog products from the 2018 Amazon Reviews dataset.

Streams `data/raw/reviews_clothing_5core.json.gz` and extracts up to 10 authentic,
high-quality reviews per catalog ASIN (star rating, summary, comment, verified badge, date).
Writes them into the PostgreSQL table `reviews`.

Run:
  python3 pipelines/extract_reviews.py [--database-url postgresql://toko:toko@localhost:5432/toko]
"""

import argparse
import gzip
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import psycopg

DEFAULT_DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://toko:toko@localhost:5432/toko"
)
PROCESSED_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"
RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
PRODUCTS_FILE = PROCESSED_DIR / "products.jsonl"
REVIEWS_RAW_FILE = RAW_DIR / "reviews_clothing_5core.json.gz"
REVIEWS_OUT_FILE = PROCESSED_DIR / "reviews.jsonl"

MAX_REVIEWS_PER_ITEM = 10


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def main():
    parser = argparse.ArgumentParser(description="Extract Customer Reviews for Catalog")
    parser.add_argument("--database-url", default=DEFAULT_DATABASE_URL)
    parser.add_argument("--products-file", default=str(PRODUCTS_FILE))
    parser.add_argument("--raw-file", default=str(REVIEWS_RAW_FILE))
    parser.add_argument("--output-file", default=str(REVIEWS_OUT_FILE))
    args = parser.parse_args()

    t_start = time.perf_counter()
    log("Loading catalog ASINs...")

    with open(args.products_file, "r", encoding="utf-8") as f:
        catalog_asins = {json.loads(line)["asin"] for line in f}

    log(f"Catalog has {len(catalog_asins):,} target ASINs.")

    raw_path = Path(args.raw_file)
    if not raw_path.exists():
        log(f"Error: {raw_path} not found. Please ensure raw reviews dataset exists.")
        return 1

    log(f"Streaming reviews from: {raw_path}...")
    reviews_by_asin = defaultdict(list)
    total_scanned = 0

    with gzip.open(raw_path, mode="rt", encoding="utf-8") as f:
        for line in f:
            total_scanned += 1
            if total_scanned % 1_000_000 == 0:
                log(f"  Scanned {total_scanned:,} reviews...")

            try:
                data = json.loads(line)
            except Exception:
                continue

            asin = data.get("asin")
            if asin not in catalog_asins:
                continue

            text = (data.get("reviewText") or "").strip()
            if len(text) < 15:
                continue

            rating = float(data.get("overall") or 5.0)
            summary = (data.get("summary") or "").strip()
            author = (data.get("reviewerName") or "Amazon Customer").strip()
            verified = bool(data.get("verified", False))
            review_date = (data.get("reviewTime") or "").strip()

            verified_bonus = 2.0 if verified else 0.0
            length_bonus = min(len(text) / 100.0, 3.0)
            quality_score = verified_bonus + length_bonus

            reviews_by_asin[asin].append({
                "rating": rating,
                "summary": summary,
                "comment": text,
                "author": author,
                "verified": verified,
                "review_date": review_date,
                "quality": quality_score,
            })

    elapsed_scan = time.perf_counter() - t_start
    log(f"Finished scan of {total_scanned:,} reviews in {elapsed_scan:.1f}s.")
    log(f"Found reviews for {len(reviews_by_asin):,} catalog ASINs.")

    # ── Select top MAX_REVIEWS_PER_ITEM per item ──────────────────────────────
    log(f"Selecting top {MAX_REVIEWS_PER_ITEM} high-quality reviews per product...")
    all_selected = []

    for asin in catalog_asins:
        candidates = reviews_by_asin.get(asin, [])
        if not candidates:
            continue
        candidates.sort(key=lambda x: -x["quality"])
        selected = candidates[:MAX_REVIEWS_PER_ITEM]
        for r in selected:
            all_selected.append((
                asin,
                r["rating"],
                r["summary"][:250],
                r["comment"][:1500],
                r["author"][:100],
                r["verified"],
                r["review_date"][:50],
            ))

    log(f"Prepared {len(all_selected):,} curated reviews across {len(reviews_by_asin):,} products.")

    # ── Write to PostgreSQL ───────────────────────────────────────────────────
    log(f"Writing reviews to PostgreSQL at {args.database_url}...")
    with psycopg.connect(args.database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS reviews (
                    id SERIAL PRIMARY KEY,
                    asin TEXT NOT NULL,
                    rating NUMERIC(2, 1) NOT NULL,
                    summary TEXT,
                    comment TEXT NOT NULL,
                    author TEXT NOT NULL DEFAULT 'Amazon Customer',
                    verified BOOLEAN NOT NULL DEFAULT true,
                    review_date TEXT,
                    created_at TIMESTAMPTZ DEFAULT now()
                );
                CREATE INDEX IF NOT EXISTS reviews_asin_idx ON reviews(asin);
                CREATE INDEX IF NOT EXISTS reviews_rating_idx ON reviews(rating);
                """
            )
            cur.execute("TRUNCATE TABLE reviews RESTART IDENTITY")
            log("Bulk inserting reviews into table `reviews`...")
            cur.executemany(
                """
                INSERT INTO reviews (asin, rating, summary, comment, author, verified, review_date)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                all_selected,
            )
            conn.commit()

            cur.execute("SELECT count(*), count(DISTINCT asin), avg(rating) FROM reviews")
            tot_rows, tot_asins, avg_r = cur.fetchone()
            log(f"Database table `reviews` populated: {tot_rows:,} reviews for {tot_asins:,} ASINs (avg {float(avg_r):.2f} ★)")

    total_time = round(time.perf_counter() - t_start, 1)
    log(f"Done in {total_time}s!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
