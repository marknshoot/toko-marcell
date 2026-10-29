#!/usr/bin/env python3
"""Extract distinct (department, category) pairs from the product seed.

The seed is a PostgreSQL COPY block embedded in ``data/seed/init.sql.gz``.
This script parses only the product columns it needs, so it runs without a
database.

Usage:
    python pipelines/extract_categories.py
    python pipelines/extract_categories.py --seed data/seed/init.sql.gz --out api/knowledge/catalog_categories.json
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
from collections import Counter
from pathlib import Path


def _read_seed(path: Path) -> str:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
            return f.read()
    return path.read_text(encoding="utf-8", errors="replace")


def extract_pairs(seed_path: Path) -> list[dict]:
    text = _read_seed(seed_path)
    match = re.search(
        r"COPY public\.products \((.*?)\) FROM stdin;\n(.*?)\n\\\.\n",
        text,
        re.S,
    )
    if not match:
        raise SystemExit(f"products COPY block not found in {seed_path}")

    columns = [c.strip() for c in match.group(1).split(",")]
    dep_idx = columns.index("department")
    cat_idx = columns.index("category")

    counts: Counter[tuple[str, str]] = Counter()
    for line in match.group(2).splitlines():
        if not line:
            continue
        fields = line.split("\t")
        if len(fields) < len(columns):
            continue
        department = fields[dep_idx]
        category = fields[cat_idx]
        if department and category:
            counts[(department, category)] += 1

    return [
        {"department": dep, "category": cat, "count": count}
        for (dep, cat), count in sorted(counts.items())
    ]


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=Path, default=root / "data" / "seed" / "init.sql.gz")
    parser.add_argument(
        "--out",
        type=Path,
        default=root / "api" / "knowledge" / "catalog_categories.json",
    )
    args = parser.parse_args()

    categories = extract_pairs(args.seed)
    payload = {
        "_meta": {
            "generated_by": "pipelines/extract_categories.py",
            "source": str(args.seed.relative_to(root)),
            "pair_count": len(categories),
        },
        "categories": categories,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(categories)} pairs to {args.out}")


if __name__ == "__main__":
    main()
