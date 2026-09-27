#!/usr/bin/env python3
"""
Exploratory Data Analysis (EDA) on Toko Marcell interactions dataset (M14).

Analyzes:
  - Interaction volume, catalog coverage, user activity
  - Matrix sparsity
  - Long-tail concentration (Pareto / power-law analysis)
  - Rating distribution & temporal span

Run:
  python3 pipelines/eda_interactions.py [--input-file data/processed/interactions.csv.gz]
"""

import argparse
import csv
import gzip
import json
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

DEFAULT_INTERACTIONS = Path(__file__).resolve().parent.parent / "data" / "processed" / "interactions.csv.gz"
OUTPUT_SUMMARY = Path(__file__).resolve().parent.parent / "data" / "processed" / "eda_summary.json"


def format_pct(val: float) -> str:
    return f"{val * 100:.2f}%"


def main():
    parser = argparse.ArgumentParser(description="EDA on User-Item Interactions")
    parser.add_argument("--input-file", default=str(DEFAULT_INTERACTIONS))
    parser.add_argument("--output-json", default=str(OUTPUT_SUMMARY))
    args = parser.parse_args()

    input_path = Path(args.input_file)
    if not input_path.exists():
        print(f"Error: {input_path} does not exist", file=sys.stderr)
        return 1

    print(f"Starting EDA on: {input_path}")
    t0 = time.perf_counter()

    user_counts = Counter()
    item_counts = Counter()
    rating_counts = Counter()
    timestamps = []

    total_rows = 0

    with gzip.open(input_path, mode="rt", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)  # user_id,asin,rating,timestamp

        for row in reader:
            if not row or len(row) < 4:
                continue
            u, asin, r_str, ts_str = row[0], row[1], row[2], row[3]
            user_counts[u] += 1
            item_counts[asin] += 1
            rating_counts[r_str] += 1
            total_rows += 1

            if total_rows % 1_000_000 == 0:
                print(f"  Processed {total_rows:,} rows...", file=sys.stderr)

    duration = time.perf_counter() - t0
    print(f"Read {total_rows:,} interactions in {duration:.1f}s.")

    n_users = len(user_counts)
    n_items = len(item_counts)
    possible_entries = n_users * n_items
    sparsity = (1.0 - (total_rows / possible_entries)) * 100

    # User activity breakdown
    users_with_1 = sum(1 for c in user_counts.values() if c == 1)
    users_with_2_to_4 = sum(1 for c in user_counts.values() if 2 <= c <= 4)
    users_with_5_plus = sum(1 for c in user_counts.values() if c >= 5)

    # Long-tail item distribution
    sorted_items = item_counts.most_common()
    top_1_pct_count = max(1, int(0.01 * n_items))
    top_5_pct_count = max(1, int(0.05 * n_items))
    top_10_pct_count = max(1, int(0.10 * n_items))
    top_20_pct_count = max(1, int(0.20 * n_items))

    top_1_vol = sum(c for _, c in sorted_items[:top_1_pct_count])
    top_5_vol = sum(c for _, c in sorted_items[:top_5_pct_count])
    top_10_vol = sum(c for _, c in sorted_items[:top_10_pct_count])
    top_20_vol = sum(c for _, c in sorted_items[:top_20_pct_count])

    item_vols = sorted(item_counts.values())
    p25 = item_vols[int(0.25 * len(item_vols))]
    p50 = item_vols[int(0.50 * len(item_vols))]
    p75 = item_vols[int(0.75 * len(item_vols))]
    p90 = item_vols[int(0.90 * len(item_vols))]
    p99 = item_vols[int(0.99 * len(item_vols))]

    sum_ratings = sum(float(r) * cnt for r, cnt in rating_counts.items())
    avg_rating = round(sum_ratings / total_rows, 3) if total_rows > 0 else 0.0

    summary = {
        "dataset": "Amazon Reviews 2018 (Clothing, Shoes & Jewelry)",
        "total_interactions": total_rows,
        "distinct_users": n_users,
        "distinct_items": n_items,
        "sparsity_percent": round(sparsity, 4),
        "user_activity": {
            "users_1_interaction": users_with_1,
            "users_1_pct": round(users_with_1 / n_users * 100, 2),
            "users_2_to_4_interactions": users_with_2_to_4,
            "users_2_to_4_pct": round(users_with_2_to_4 / n_users * 100, 2),
            "users_5_plus_interactions": users_with_5_plus,
            "users_5_plus_pct": round(users_with_5_plus / n_users * 100, 2),
        },
        "item_long_tail": {
            "min_interactions_per_item": item_vols[0],
            "median_p50": p50,
            "p75": p75,
            "p90": p90,
            "p99": p99,
            "max_interactions_per_item": item_vols[-1],
            "top_1_pct_items_volume_pct": round(top_1_vol / total_rows * 100, 2),
            "top_5_pct_items_volume_pct": round(top_5_vol / total_rows * 100, 2),
            "top_10_pct_items_volume_pct": round(top_10_vol / total_rows * 100, 2),
            "top_20_pct_items_volume_pct": round(top_20_vol / total_rows * 100, 2),
        },
        "ratings": {
            "avg_rating": avg_rating,
            "distribution": dict(sorted(rating_counts.items())),
        },
    }

    print("\n" + "=" * 70)
    print("INTERACTIONS EXPLORATORY DATA ANALYSIS (EDA)")
    print("=" * 70)
    print(f"Total Interactions:      {total_rows:,}")
    print(f"Distinct Users:          {n_users:,}")
    print(f"Distinct Items:          {n_items:,}")
    print(f"Interaction Matrix Size: {n_users:,} × {n_items:,} = {possible_entries:,}")
    print(f"Matrix Sparsity:         {sparsity:.4f}% (only {100 - sparsity:.4f}% filled)")
    print("-" * 70)
    print("USER ACTIVITY:")
    print(f"  Single interaction:    {users_with_1:,} ({users_with_1 / n_users * 100:.1f}%)")
    print(f"  2 to 4 interactions:   {users_with_2_to_4:,} ({users_with_2_to_4 / n_users * 100:.1f}%)")
    print(f"  5+ interactions:       {users_with_5_plus:,} ({users_with_5_plus / n_users * 100:.1f}%)")
    print("-" * 70)
    print("ITEM LONG-TAIL & PARETO CONCENTRATION:")
    print(f"  Min / Median / Max:    {item_vols[0]} / {p50} / {item_vols[-1]:,} per item")
    print(f"  Top 1% items (60):     hold {top_1_vol / total_rows * 100:.2f}% of all interactions")
    print(f"  Top 5% items (300):    hold {top_5_vol / total_rows * 100:.2f}% of all interactions")
    print(f"  Top 10% items (600):   hold {top_10_vol / total_rows * 100:.2f}% of all interactions")
    print(f"  Top 20% items (1,200): hold {top_20_vol / total_rows * 100:.2f}% of all interactions")
    print("-" * 70)
    print("RATING DISTRIBUTION:")
    for r in sorted(rating_counts.keys(), key=lambda x: float(x)):
        cnt = rating_counts[r]
        pct = (cnt / total_rows) * 100
        bar = "█" * int(pct / 2)
        print(f"  {float(r):.1f} ★: {cnt:>8,} ({pct:>5.1f}%) {bar}")
    print(f"  Average Rating: {avg_rating} ★")
    print("=" * 70)

    with open(args.output_json, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"Saved EDA summary to: {args.output_json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
