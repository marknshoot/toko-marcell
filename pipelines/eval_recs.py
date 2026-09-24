#!/usr/bin/env python3
"""
Recommendation Systems Evaluation Harness (M10).

Compares:
  - Baseline 1: Random Selection
  - Baseline 2: Global Popularity (M8)
  - Treatment: Item-to-Item Collaborative Filtering + Graph (M9)

Evaluation Protocol:
  Sequential Leave-One-Out on active users (>= 5 interactions):
  - Held-out target: User's final interaction (by timestamp)
  - Session context: User's penultimate interaction (current PDP context)
  - Prior history: Masked from recommendations

Metrics:
  - HitRate@10 (HR@10): 1 if target item appears in top 10 recommendations
  - HitRate@5 (HR@5): 1 if target item appears in top 5 recommendations
  - MRR@10: Mean Reciprocal Rank of target item
  - nDCG@10: Normalized Discounted Cumulative Gain

Run:
  python3 pipelines/eval_recs.py [--n-users 5000]
"""

import argparse
import csv
import gzip
import json
import math
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

PROCESSED_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"
INTERACTIONS_FILE = PROCESSED_DIR / "interactions.csv.gz"
PRODUCTS_FILE = PROCESSED_DIR / "products.jsonl"
ITEM_RECS_FILE = PROCESSED_DIR / "item_recs.json"
POPULAR_RECS_FILE = PROCESSED_DIR / "popular_recs.json"


def main():
    parser = argparse.ArgumentParser(description="Evaluate Recommendations Models")
    parser.add_argument("--interactions-file", default=str(INTERACTIONS_FILE))
    parser.add_argument("--products-file", default=str(PRODUCTS_FILE))
    parser.add_argument("--item-recs-file", default=str(ITEM_RECS_FILE))
    parser.add_argument("--popular-recs-file", default=str(POPULAR_RECS_FILE))
    parser.add_argument("--n-users", type=int, default=5000, help="Number of test users")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    random.seed(args.seed)

    print(f"Loading recommendation models and catalog...")
    t0 = time.perf_counter()

    with open(args.products_file, "r", encoding="utf-8") as f:
        catalog_asins = [json.loads(line)["asin"] for line in f]
    catalog_set = set(catalog_asins)

    with open(args.item_recs_file, "r", encoding="utf-8") as f:
        item_recs = json.load(f)

    with open(args.popular_recs_file, "r", encoding="utf-8") as f:
        popular_data = json.load(f)
        global_popular = popular_data.get("global", [])

    print(f"Loaded {len(catalog_asins):,} products and {len(item_recs):,} item recommendation lists in {time.perf_counter() - t0:.2f}s.")

    # ── Read user histories from interactions ────────────────────────────────
    print(f"Reading user histories from: {args.interactions_file}...")
    user_timeline = defaultdict(list)

    with gzip.open(args.interactions_file, mode="rt", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        for row in reader:
            if not row or len(row) < 4:
                continue
            u, asin, _, ts_str = row[0], row[1], row[2], row[3]
            if asin in catalog_set:
                try:
                    ts = int(ts_str)
                except ValueError:
                    ts = 0
                user_timeline[u].append((ts, asin))

    # Filter to active users with >= 5 interactions
    active_users = [u for u, timeline in user_timeline.items() if len(timeline) >= 5]
    print(f"Found {len(active_users):,} active users (>= 5 interactions).")

    # Sample test users
    sample_size = min(args.n_users, len(active_users))
    sample_users = random.sample(active_users, sample_size)
    print(f"Evaluating on {sample_size:,} sampled test users...")

    # Evaluation accumulators
    models = ["Random", "Popularity (M8)", "Item-to-Item CF (M9)"]
    metrics = {
        m: {"hr10": 0.0, "hr5": 0.0, "mrr": 0.0, "ndcg10": 0.0}
        for m in models
    }

    t_eval = time.perf_counter()

    for i, u in enumerate(sample_users, 1):
        timeline = sorted(user_timeline[u], key=lambda x: x[0])
        # Last item is held-out target
        target_item = timeline[-1][1]
        # Penultimate item is session context
        context_item = timeline[-2][1]
        # Prior history to mask
        history_asins = {item for _, item in timeline[:-1]}

        # 1. Random Baseline
        rand_candidates = [a for a in catalog_asins if a not in history_asins]
        rand_recs = random.sample(rand_candidates, min(10, len(rand_candidates)))

        # 2. Popularity Baseline
        pop_recs = [a for a in global_popular if a not in history_asins][:10]

        # 3. Item-to-Item CF Model
        raw_cf = item_recs.get(context_item, [])
        cf_recs = [a for a in raw_cf if a not in history_asins][:10]
        # Backfill with popular if fewer than 10
        if len(cf_recs) < 10:
            for p in global_popular:
                if p not in history_asins and p not in cf_recs:
                    cf_recs.append(p)
                    if len(cf_recs) >= 10:
                        break

        # Compute metrics for each model
        for m, recs in [
            ("Random", rand_recs),
            ("Popularity (M8)", pop_recs),
            ("Item-to-Item CF (M9)", cf_recs),
        ]:
            if target_item in recs:
                rank = recs.index(target_item)  # 0-indexed
                metrics[m]["hr10"] += 1.0
                if rank < 5:
                    metrics[m]["hr5"] += 1.0
                metrics[m]["mrr"] += 1.0 / (rank + 1)
                metrics[m]["ndcg10"] += 1.0 / math.log2(rank + 2)

        if i % 1000 == 0 or i == sample_size:
            sys.stdout.write(f"\r  Evaluated {i:,} / {sample_size:,} users ({round(i/sample_size*100)}%)...")
            sys.stdout.flush()

    eval_duration = time.perf_counter() - t_eval
    print(f"\nCompleted evaluation in {eval_duration:.2f}s.\n")

    # ── Summary Report ────────────────────────────────────────────────────────
    print("=" * 80)
    print("RECOMMENDATION BENCHMARK RESULTS (M10: HR@10 & P@10 VS BASELINE)")
    print("=" * 80)
    print(f"{'Model':<25} | {'HR@10':<10} | {'HR@5':<10} | {'MRR@10':<10} | {'nDCG@10':<10}")
    print("-" * 80)

    for m in models:
        hr10 = metrics[m]["hr10"] / sample_size
        hr5 = metrics[m]["hr5"] / sample_size
        mrr = metrics[m]["mrr"] / sample_size
        ndcg = metrics[m]["ndcg10"] / sample_size
        print(f"{m:<25} | {hr10:<10.4f} | {hr5:<10.4f} | {mrr:<10.4f} | {ndcg:<10.4f}")

    pop_hr10 = metrics["Popularity (M8)"]["hr10"] / sample_size
    cf_hr10 = metrics["Item-to-Item CF (M9)"]["hr10"] / sample_size
    pop_ndcg = metrics["Popularity (M8)"]["ndcg10"] / sample_size
    cf_ndcg = metrics["Item-to-Item CF (M9)"]["ndcg10"] / sample_size

    hr10_gain = ((cf_hr10 - pop_hr10) / pop_hr10) * 100 if pop_hr10 > 0 else 0
    ndcg_gain = ((cf_ndcg - pop_ndcg) / pop_ndcg) * 100 if pop_ndcg > 0 else 0

    print("=" * 80)
    print(f"Item-to-Item CF vs Popularity Baseline:")
    print(f"  HR@10 Relative Gain:  {hr10_gain:+.1f}% ({pop_hr10:.2%} -> {cf_hr10:.2%})")
    print(f"  nDCG@10 Relative Gain: {ndcg_gain:+.1f}% ({pop_ndcg:.4f} -> {cf_ndcg:.4f})")
    print("=" * 80)

    return 0


if __name__ == "__main__":
    sys.exit(main())
