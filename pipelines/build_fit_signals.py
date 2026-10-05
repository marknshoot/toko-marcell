#!/usr/bin/env python3
"""
Build review-derived fit signals for every catalog ASIN and brand.

Streams ``data/raw/reviews_clothing_5core.json.gz`` (~11 M reviews), classifies
each review text into ``runs_small / true_to_size / runs_large`` with negation
handling, applies Dirichlet smoothing toward the category prior, and writes:

  1. ``product_fit`` table (one row per catalog ASIN)
  2. ``brand_fit`` table (one row per brand)
  3. ``data/processed/fit_signals.json`` (flat JSON for offline inspection)

Tables are created idempotently so a fresh database (or the API's ``init_db``)
always has the schema even if the pipeline has not run yet.

Run:
    python3 pipelines/build_fit_signals.py [--database-url ...]
"""

import argparse
import gzip
import json
import os
import re
import sys
import time
from collections import defaultdict

# ── Paths ────────────────────────────────────────────────────────────────────

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REVIEWS_PATH = os.path.join(REPO, "data/raw/reviews_clothing_5core.json.gz")
PRODUCTS_PATH = os.path.join(REPO, "data/processed/products.jsonl")
OUT_DIR = os.path.join(REPO, "data/processed")
OUT_JSON = os.path.join(OUT_DIR, "fit_signals.json")

DEFAULT_DB_URL = os.environ.get(
    "DATABASE_URL", "postgresql://toko:toko@localhost:5432/toko"
)

# ── Dirichlet smoothing hyperparameter ───────────────────────────────────────
# Alpha = 10 pseudocounts spread across the category prior.  With fewer than 10
# real fit mentions the smoothed shares are dominated by the category average,
# which prevents a single "too small" review from labelling a product.
ALPHA = 10.0


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# ═══════════════════════════════════════════════════════════════════════════════
# Fit-mention regex classifier
# ═══════════════════════════════════════════════════════════════════════════════

# --- runs_small patterns ---
SMALL_PATTERNS = [
    r"\bruns?\s+small\b",
    r"\bran\s+small\b",
    r"\brunning\s+small\b",
    r"\btoo\s+small\b",
    r"\btoo\s+tight\b",
    r"\bvery\s+tight\b",
    r"\bsuper\s+tight\b",
    r"\bextremely\s+tight\b",
    r"\bway\s+too\s+small\b",
    r"\bsmaller\s+than\s+expected\b",
    r"\btighter\s+than\s+expected\b",
    r"\bsize\s+up\b",
    r"\bsized?\s+up\b",
    r"\border(?:ed)?\s+(?:a\s+)?(?:size\s+)?(?:one\s+|1\s+)?(?:size\s+)?(?:bigger|larger|up)\b",
    r"\bneed(?:ed)?\s+(?:a\s+)?bigger\s+size\b",
    r"\bwish(?:ed)?\s+i\s+(?:had\s+)?(?:gotten?|ordered?|bought)\s+(?:a\s+)?(?:size\s+)?(?:bigger|larger|up)\b",
]

# --- true_to_size patterns ---
TTS_PATTERNS = [
    r"\btrue\s+to\s+size\b",
    r"\btts\b",
    r"\bfits?\s+(?:as\s+)?expected\b",
    r"\bfits?\s+(?:just\s+)?right\b",
    r"\bfits?\s+perfectly\b",
    r"\bperfect\s+fit\b",
    r"\bfits?\s+great\b",
    r"\bfits?\s+well\b",
    r"\bfits?\s+true\b",
    r"\bnormal\s+fit\b",
    r"\bstandard\s+fit\b",
    r"\bregular\s+fit\b",
    r"\bjust\s+(?:the\s+)?right\s+(?:size|fit)\b",
    r"\bfits?\s+(?:my\s+)?(?:normal|usual|regular)\s+size\b",
]

# --- runs_large patterns ---
LARGE_PATTERNS = [
    r"\bruns?\s+large\b",
    r"\bran\s+large\b",
    r"\brunning\s+large\b",
    r"\bruns?\s+big\b",
    r"\bran\s+big\b",
    r"\btoo\s+(?:big|large|loose|baggy)\b",
    r"\bvery\s+(?:big|large|loose|baggy)\b",
    r"\bsuper\s+(?:big|large|loose|baggy)\b",
    r"\bway\s+too\s+(?:big|large|loose|baggy)\b",
    r"\bbigger\s+than\s+expected\b",
    r"\blarger\s+than\s+expected\b",
    r"\blooser\s+than\s+expected\b",
    r"\bsize\s+down\b",
    r"\bsized?\s+down\b",
    r"\border(?:ed)?\s+(?:a\s+)?(?:size\s+)?(?:one\s+|1\s+)?(?:size\s+)?(?:smaller|down)\b",
    r"\bneed(?:ed)?\s+(?:a\s+)?smaller\s+size\b",
]

_re_small = [re.compile(p, re.IGNORECASE) for p in SMALL_PATTERNS]
_re_tts = [re.compile(p, re.IGNORECASE) for p in TTS_PATTERNS]
_re_large = [re.compile(p, re.IGNORECASE) for p in LARGE_PATTERNS]

# Negation window: 40 chars before the match start.
_NEG_RE = re.compile(
    r"\b(?:not|n't|don't|doesn't|didn't|isn't|wasn't|aren't|weren't|never|no)\b",
    re.IGNORECASE,
)


def _is_negated(text: str, match_start: int) -> bool:
    """Return True if a negation word appears in the 40-char window before *match_start*."""
    window_start = max(0, match_start - 40)
    window = text[window_start:match_start]
    return bool(_NEG_RE.search(window))


def classify_fit(text: str) -> set[str]:
    """Classify review text into a set of fit labels.

    Negation handling:
      - Negated small pattern → ``runs_large`` (weak signal).
      - Negated large pattern → ``runs_small`` (weak signal).
      - Negated TTS → discarded (ambiguous).

    Returns a subset of ``{"runs_small", "true_to_size", "runs_large"}``.
    """
    if not text:
        return set()

    labels: set[str] = set()

    for pat in _re_small:
        m = pat.search(text)
        if m:
            if _is_negated(text, m.start()):
                labels.add("runs_large")
            else:
                labels.add("runs_small")
            break

    for pat in _re_tts:
        m = pat.search(text)
        if m:
            if not _is_negated(text, m.start()):
                labels.add("true_to_size")
            break

    for pat in _re_large:
        m = pat.search(text)
        if m:
            if _is_negated(text, m.start()):
                labels.add("runs_small")
            else:
                labels.add("runs_large")
            break

    return labels


# ═══════════════════════════════════════════════════════════════════════════════
# Label derivation
# ═══════════════════════════════════════════════════════════════════════════════

def fit_label(score: float, n_mentions: int) -> str:
    """Human-readable label from the smoothed fit score.

    score = share_large - share_small (range roughly -1..+1).
    """
    if n_mentions < 3:
        return "insufficient_data"
    if score > 0.15:
        return "runs_large"
    if score < -0.15:
        return "runs_small"
    return "true_to_size"


# ═══════════════════════════════════════════════════════════════════════════════
# Main pipeline
# ═══════════════════════════════════════════════════════════════════════════════

def load_catalog():
    """Load catalog ASIN → brand / department / category from products.jsonl."""
    asin_brand: dict[str, str] = {}
    asin_dept: dict[str, str] = {}
    asin_cat: dict[str, str] = {}
    with open(PRODUCTS_PATH) as f:
        for line in f:
            d = json.loads(line)
            asin = d["asin"]
            asin_brand[asin] = (d.get("brand") or "").strip()
            asin_dept[asin] = (d.get("department") or "").strip()
            cp = d.get("categoryPath") or []
            asin_cat[asin] = cp[-1] if cp else ""
    return set(asin_brand), asin_brand, asin_dept, asin_cat


def stream_reviews(catalog_asins):
    """Yield (asin, text) for every review whose ASIN is in the catalog."""
    with gzip.open(REVIEWS_PATH, "rt") as f:
        for line in f:
            d = json.loads(line)
            asin = d.get("asin", "")
            if asin not in catalog_asins:
                continue
            text = (d.get("reviewText") or "") + " " + (d.get("summary") or "")
            yield asin, text


def build_signals(db_url: str | None = None):
    """Run the full pipeline: stream → classify → smooth → write."""
    t0 = time.perf_counter()

    # ── 1. Load catalog metadata ─────────────────────────────────────────────
    log("Loading catalog ASINs from products.jsonl...")
    catalog_asins, asin_brand, asin_dept, asin_cat = load_catalog()
    log(f"  {len(catalog_asins):,} catalog ASINs loaded")

    # ── 2. Stream reviews and classify ───────────────────────────────────────
    log(f"Streaming reviews from {os.path.basename(REVIEWS_PATH)}...")
    asin_fit: dict[str, dict[str, int]] = defaultdict(lambda: {"runs_small": 0, "true_to_size": 0, "runs_large": 0})
    dept_fit: dict[str, dict[str, int]] = defaultdict(lambda: {"runs_small": 0, "true_to_size": 0, "runs_large": 0})
    brand_counts: dict[str, dict[str, int]] = defaultdict(lambda: {"runs_small": 0, "true_to_size": 0, "runs_large": 0, "total_reviews": 0})
    catalog_reviews = 0
    fit_mentions = 0

    for asin, text in stream_reviews(catalog_asins):
        catalog_reviews += 1
        if catalog_reviews % 500_000 == 0:
            log(f"  {catalog_reviews:,} catalog reviews processed...")

        labels = classify_fit(text)
        brand = asin_brand.get(asin, "")
        dept = asin_dept.get(asin, "")

        brand_counts[brand]["total_reviews"] += 1

        if labels:
            fit_mentions += 1
            for label in labels:
                asin_fit[asin][label] += 1
                dept_fit[dept][label] += 1
                brand_counts[brand][label] += 1

    log(f"  Done: {catalog_reviews:,} catalog reviews, {fit_mentions:,} with fit mention "
        f"({100 * fit_mentions / max(1, catalog_reviews):.1f}%)")

    # ── 3. Compute department priors ─────────────────────────────────────────
    global_s = sum(d["runs_small"] for d in dept_fit.values())
    global_t = sum(d["true_to_size"] for d in dept_fit.values())
    global_l = sum(d["runs_large"] for d in dept_fit.values())
    global_total = global_s + global_t + global_l
    global_prior = {
        "runs_small": global_s / max(1, global_total),
        "true_to_size": global_t / max(1, global_total),
        "runs_large": global_l / max(1, global_total),
    }

    dept_prior: dict[str, dict[str, float]] = {}
    for dept, counts in dept_fit.items():
        total = counts["runs_small"] + counts["true_to_size"] + counts["runs_large"]
        if total > 0:
            dept_prior[dept] = {
                "runs_small": counts["runs_small"] / total,
                "true_to_size": counts["true_to_size"] / total,
                "runs_large": counts["runs_large"] / total,
            }
        else:
            dept_prior[dept] = dict(global_prior)

    # ── 4. Smoothed per-ASIN signals ─────────────────────────────────────────
    log("Computing Dirichlet-smoothed per-ASIN fit signals...")
    product_rows: list[dict] = []
    for asin in sorted(catalog_asins):
        counts = asin_fit.get(asin, {"runs_small": 0, "true_to_size": 0, "runs_large": 0})
        n = counts["runs_small"] + counts["true_to_size"] + counts["runs_large"]
        dept = asin_dept.get(asin, "")
        prior = dept_prior.get(dept, global_prior)

        denom = n + ALPHA
        share_small = (counts["runs_small"] + ALPHA * prior["runs_small"]) / denom
        share_tts = (counts["true_to_size"] + ALPHA * prior["true_to_size"]) / denom
        share_large = (counts["runs_large"] + ALPHA * prior["runs_large"]) / denom
        score = share_large - share_small

        product_rows.append({
            "asin": asin,
            "n_mentions": n,
            "share_small": round(share_small, 4),
            "share_tts": round(share_tts, 4),
            "share_large": round(share_large, 4),
            "fit_score": round(score, 4),
            "label": fit_label(score, n),
        })

    # ── 5. Brand-level aggregates ────────────────────────────────────────────
    log("Computing brand-level fit aggregates...")
    brand_rows: list[dict] = []
    for brand in sorted(brand_counts):
        if not brand:
            continue
        c = brand_counts[brand]
        n = c["runs_small"] + c["true_to_size"] + c["runs_large"]
        if n == 0:
            continue
        share_small = c["runs_small"] / n
        share_tts = c["true_to_size"] / n
        share_large = c["runs_large"] / n
        score = share_large - share_small
        brand_rows.append({
            "brand": brand,
            "n_mentions": n,
            "share_small": round(share_small, 4),
            "share_tts": round(share_tts, 4),
            "share_large": round(share_large, 4),
            "fit_score": round(score, 4),
            "label": fit_label(score, n),
        })

    # ── 6. Coverage stats ────────────────────────────────────────────────────
    with_any = sum(1 for r in product_rows if r["n_mentions"] > 0)
    with_5 = sum(1 for r in product_rows if r["n_mentions"] >= 5)
    with_20 = sum(1 for r in product_rows if r["n_mentions"] >= 20)
    log("\nFit coverage:")
    log(f"  ASINs with any mention: {with_any:,} / {len(product_rows):,} ({100*with_any/len(product_rows):.1f}%)")
    log(f"  ASINs with ≥5 mentions: {with_5:,} / {len(product_rows):,} ({100*with_5/len(product_rows):.1f}%)")
    log(f"  ASINs with ≥20 mentions: {with_20:,} / {len(product_rows):,} ({100*with_20/len(product_rows):.1f}%)")
    log(f"  Brands with data: {len(brand_rows):,}")

    # ── 7. Write JSON ────────────────────────────────────────────────────────
    os.makedirs(OUT_DIR, exist_ok=True)
    out = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "catalog_reviews": catalog_reviews,
        "fit_mentions": fit_mentions,
        "alpha": ALPHA,
        "global_prior": {k: round(v, 4) for k, v in global_prior.items()},
        "coverage": {
            "total_asins": len(product_rows),
            "with_any": with_any,
            "with_5_plus": with_5,
            "with_20_plus": with_20,
        },
        "products": {r["asin"]: r for r in product_rows},
        "brands": {r["brand"]: r for r in brand_rows},
    }
    with open(OUT_JSON, "w") as f:
        json.dump(out, f, indent=1)
    log(f"Wrote {OUT_JSON} ({len(product_rows)} products, {len(brand_rows)} brands)")

    # ── 8. Write to database ─────────────────────────────────────────────────
    if db_url:
        log("Writing fit tables to database...")
        _write_to_db(db_url, product_rows, brand_rows)

    elapsed = time.perf_counter() - t0
    log(f"\nDone in {elapsed:.1f}s")
    return product_rows, brand_rows


# ═══════════════════════════════════════════════════════════════════════════════
# Database I/O
# ═══════════════════════════════════════════════════════════════════════════════

PRODUCT_FIT_DDL = """
CREATE TABLE IF NOT EXISTS product_fit (
    asin        TEXT PRIMARY KEY,
    n_mentions  INTEGER NOT NULL DEFAULT 0,
    share_small NUMERIC(5,4) NOT NULL DEFAULT 0,
    share_tts   NUMERIC(5,4) NOT NULL DEFAULT 0,
    share_large NUMERIC(5,4) NOT NULL DEFAULT 0,
    fit_score   NUMERIC(5,4) NOT NULL DEFAULT 0,
    label       TEXT NOT NULL DEFAULT 'insufficient_data'
)
"""

BRAND_FIT_DDL = """
CREATE TABLE IF NOT EXISTS brand_fit (
    brand       TEXT PRIMARY KEY,
    n_mentions  INTEGER NOT NULL DEFAULT 0,
    share_small NUMERIC(5,4) NOT NULL DEFAULT 0,
    share_tts   NUMERIC(5,4) NOT NULL DEFAULT 0,
    share_large NUMERIC(5,4) NOT NULL DEFAULT 0,
    fit_score   NUMERIC(5,4) NOT NULL DEFAULT 0,
    label       TEXT NOT NULL DEFAULT 'insufficient_data'
)
"""


def ensure_fit_tables(cur) -> None:
    """Create product_fit and brand_fit tables idempotently."""
    cur.execute(PRODUCT_FIT_DDL)
    cur.execute(BRAND_FIT_DDL)


def _write_to_db(db_url: str, product_rows: list[dict], brand_rows: list[dict]) -> None:
    import psycopg

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            ensure_fit_tables(cur)

            # Upsert product_fit
            cur.execute("DELETE FROM product_fit")
            for r in product_rows:
                cur.execute(
                    """
                    INSERT INTO product_fit (asin, n_mentions, share_small, share_tts, share_large, fit_score, label)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (r["asin"], r["n_mentions"], r["share_small"], r["share_tts"],
                     r["share_large"], r["fit_score"], r["label"]),
                )

            # Upsert brand_fit
            cur.execute("DELETE FROM brand_fit")
            for r in brand_rows:
                cur.execute(
                    """
                    INSERT INTO brand_fit (brand, n_mentions, share_small, share_tts, share_large, fit_score, label)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (r["brand"], r["n_mentions"], r["share_small"], r["share_tts"],
                     r["share_large"], r["fit_score"], r["label"]),
                )

            conn.commit()

    log(f"  product_fit: {len(product_rows)} rows")
    log(f"  brand_fit:   {len(brand_rows)} rows")


def main():
    ap = argparse.ArgumentParser(description="Build review-derived fit signals")
    ap.add_argument("--database-url", default=DEFAULT_DB_URL,
                    help="PostgreSQL connection string (empty string = skip DB write)")
    ap.add_argument("--no-db", action="store_true",
                    help="Skip database write (JSON only)")
    args = ap.parse_args()

    db_url = None if args.no_db else args.database_url
    build_signals(db_url=db_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
