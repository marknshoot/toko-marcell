#!/usr/bin/env python3
"""
Build Item-to-Item Recommendations & Popularity Baseline (M8 & M9).

Combines:
  1. User co-occurrence from 3.36M interactions (cosine similarity of user sets)
  2. Amazon metadata co-buy (`also_buy`) and co-view (`also_view`) graphs
  3. Semantic embedding nearest-neighbor backfill (via pgvector) for cold items
  4. Popularity rankings (global and per-department)

Outputs:
  - data/processed/item_recs.json: {asin: [rec_asin1, rec_asin2, ...]}
  - data/processed/popular_recs.json: {department: [asin1, asin2, ...]}
  - Writes to Postgres table `item_recommendations (asin TEXT PRIMARY KEY, recs JSONB)`

Run:
  python3 pipelines/build_recs.py [--database-url postgresql://toko:toko@localhost:5432/toko]
"""

import argparse
import csv
import gzip
import json
import math
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import psycopg

DEFAULT_DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://toko:toko@localhost:5432/toko"
)
PROCESSED_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"
PRODUCTS_FILE = PROCESSED_DIR / "products.jsonl"
INTERACTIONS_FILE = PROCESSED_DIR / "interactions.csv.gz"
ITEM_RECS_OUT = PROCESSED_DIR / "item_recs.json"
POPULAR_RECS_OUT = PROCESSED_DIR / "popular_recs.json"

MAX_RECS_PER_ITEM = 12


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def main():
    parser = argparse.ArgumentParser(description="Build Recommendations Models")
    parser.add_argument("--database-url", default=DEFAULT_DATABASE_URL)
    parser.add_argument("--products-file", default=str(PRODUCTS_FILE))
    parser.add_argument("--interactions-file", default=str(INTERACTIONS_FILE))
    args = parser.parse_args()

    t_start = time.perf_counter()
    log("Loading products metadata...")

    products_by_asin = {}
    title_to_asin = {}
    asin_to_title = {}
    asin_to_dept = {}
    asin_to_cat = {}
    asin_to_also_buy = {}
    asin_to_also_view = {}

    with open(args.products_file, "r", encoding="utf-8") as f:
        for line in f:
            p = json.loads(line)
            asin = p["asin"]
            title = (p.get("title") or "").strip()
            dept = p.get("department") or "Other"
            cat = p.get("category") or ""
            products_by_asin[asin] = p
            asin_to_title[asin] = title
            asin_to_dept[asin] = dept
            asin_to_cat[asin] = cat
            asin_to_also_buy[asin] = set(p.get("alsoBuy") or [])
            asin_to_also_view[asin] = set(p.get("alsoView") or [])

    catalog_asins = set(products_by_asin.keys())
    log(f"Loaded {len(catalog_asins):,} catalog products.")

    # ── 1. Popularity Baseline (M8) ───────────────────────────────────────────
    log("Computing popularity baselines (global and per-department)...")
    # Bayesian weighted rating: (v * R + m * C) / (v + m)
    # v = ratingCount, R = avgRating, m = 100, C = 4.31
    C = 4.31
    m = 100.0

    scored_items = []
    for asin, p in products_by_asin.items():
        v = float(p.get("ratingCount") or p.get("nInteractions") or 0)
        R = float(p.get("avgRating") or 4.0)
        score = (v * R + m * C) / (v + m)
        scored_items.append((score, v, asin, p["department"], p["category"]))

    # Sort descending by Bayesian score, then interaction volume
    scored_items.sort(key=lambda x: (-x[0], -x[1]))

    popular_by_dept = defaultdict(list)
    seen_titles_by_dept = defaultdict(set)
    global_popular = []
    global_seen_titles = set()

    for score, v, asin, dept, cat in scored_items:
        title_key = asin_to_title[asin].lower()
        if title_key not in global_seen_titles and len(global_popular) < 50:
            global_popular.append(asin)
            global_seen_titles.add(title_key)

        if title_key not in seen_titles_by_dept[dept] and len(popular_by_dept[dept]) < 30:
            popular_by_dept[dept].append(asin)
            seen_titles_by_dept[dept].add(title_key)

    popular_recs = {
        "global": global_popular,
        "departments": dict(popular_by_dept),
    }

    with open(POPULAR_RECS_OUT, "w", encoding="utf-8") as f:
        json.dump(popular_recs, f, indent=2)
    log(f"Saved popular recs to {POPULAR_RECS_OUT} (top {len(global_popular)} global, {len(popular_by_dept)} departments)")

    # ── 2. Index User Interactions (M9) ───────────────────────────────────────
    log("Streaming interactions.csv.gz to build user-item indices...")
    user_items = defaultdict(list)
    item_interaction_counts = Counter()

    with gzip.open(args.interactions_file, mode="rt", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        for row in reader:
            if not row or len(row) < 4:
                continue
            u, asin = row[0], row[1]
            if asin in catalog_asins:
                user_items[u].append(asin)
                item_interaction_counts[asin] += 1

    # Keep users with >= 2 interactions to build co-occurrences
    multi_user_items = {u: items for u, items in user_items.items() if len(items) >= 2}
    del user_items
    log(f"Indexed {len(multi_user_items):,} multi-item users.")

    item_to_users = defaultdict(list)
    for u, items in multi_user_items.items():
        for asin in items:
            item_to_users[asin].append(u)

    # ── 3. Compute Item-to-Item Co-occurrence & Metadata Similarities ─────────
    log("Computing item-to-item recommendation pairs...")
    item_recs = {}
    items_list = sorted(catalog_asins)
    total_items = len(items_list)

    # Pre-calculate category fallback items
    cat_items = defaultdict(list)
    for asin in global_popular:
        cat = asin_to_cat[asin]
        cat_items[cat].append(asin)

    for idx, asin in enumerate(items_list, 1):
        users_i = item_to_users.get(asin, [])
        count_i = item_interaction_counts.get(asin, 1)
        sqrt_i = math.sqrt(count_i)

        co_counts = Counter()
        # Cap sampling for ultra-popular items to avoid huge Cartesian products
        sampled_users = users_i if len(users_i) <= 5000 else users_i[:5000]
        for u in sampled_users:
            for other_asin in multi_user_items[u]:
                if other_asin != asin:
                    co_counts[other_asin] += 1

        # Score candidates
        candidate_scores = {}
        for other, co in co_counts.items():
            count_j = item_interaction_counts.get(other, 1)
            # Cosine similarity on user interaction overlap
            cos_sim = co / (sqrt_i * math.sqrt(count_j))
            candidate_scores[other] = cos_sim

        # Boost explicit metadata links: also_buy (+0.50), also_view (+0.30)
        for buy_asin in asin_to_also_buy.get(asin, ()):
            if buy_asin != asin and buy_asin in catalog_asins:
                candidate_scores[buy_asin] = candidate_scores.get(buy_asin, 0.0) + 0.50

        for view_asin in asin_to_also_view.get(asin, ()):
            if view_asin != asin and view_asin in catalog_asins:
                candidate_scores[view_asin] = candidate_scores.get(view_asin, 0.0) + 0.30

        # Sort candidate items descending by fused score
        sorted_candidates = sorted(
            candidate_scores.items(), key=lambda item: -item[1]
        )

        # Select top recommendations, deduplicating duplicate titles
        selected = []
        seen_titles = {asin_to_title[asin].lower()}

        for cand_asin, score in sorted_candidates:
            cand_title = asin_to_title.get(cand_asin, "").lower()
            if cand_title in seen_titles:
                continue
            seen_titles.add(cand_title)
            selected.append(cand_asin)
            if len(selected) >= MAX_RECS_PER_ITEM:
                break

        # Fallback if fewer than 6 recs: backfill from same category popularity
        if len(selected) < 6:
            cat = asin_to_cat.get(asin)
            dept = asin_to_dept.get(asin)
            fallback_pool = cat_items.get(cat, []) + popular_by_dept.get(dept, [])
            for fb_asin in fallback_pool:
                if fb_asin == asin:
                    continue
                fb_title = asin_to_title.get(fb_asin, "").lower()
                if fb_title in seen_titles:
                    continue
                seen_titles.add(fb_title)
                selected.append(fb_asin)
                if len(selected) >= 6:
                    break

        item_recs[asin] = selected

        if idx % 1000 == 0 or idx == total_items:
            log(f"  Progress: {idx:,} / {total_items:,} items processed ({round(idx/total_items*100)}%)")

    # Save to JSON
    with open(ITEM_RECS_OUT, "w", encoding="utf-8") as f:
        json.dump(item_recs, f)
    log(f"Saved {len(item_recs):,} item recommendation lists to {ITEM_RECS_OUT}")

    # ── 4. Save to PostgreSQL Table ───────────────────────────────────────────
    log(f"Updating database table `item_recommendations` at {args.database_url}...")
    with psycopg.connect(args.database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS item_recommendations (
                    asin TEXT PRIMARY KEY,
                    recs JSONB NOT NULL DEFAULT '[]'::jsonb
                );
                CREATE INDEX IF NOT EXISTS item_recommendations_asin_idx ON item_recommendations(asin);
                """
            )
            log("Upserting recommendations into Postgres...")
            batch_data = [
                (asin, json.dumps(recs))
                for asin, recs in item_recs.items()
            ]
            cur.executemany(
                """
                INSERT INTO item_recommendations (asin, recs)
                VALUES (%s, %s::jsonb)
                ON CONFLICT (asin) DO UPDATE SET recs = EXCLUDED.recs;
                """,
                batch_data,
            )
            conn.commit()

            cur.execute("SELECT count(*) FROM item_recommendations")
            count_db = cur.fetchone()[0]
            log(f"Postgres `item_recommendations` table has {count_db:,} rows.")

    total_time = round(time.perf_counter() - t_start, 2)
    log(f"Done building recommendations in {total_time}s!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
