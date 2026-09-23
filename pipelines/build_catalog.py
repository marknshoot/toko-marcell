#!/usr/bin/env python3
"""
Build the Toko Marcell catalog + interaction data from Amazon Reviews 2018
(Clothing, Shoes & Jewelry, 5-core).

Why this dataset
  * real product text (title, brand, feature bullets, description, category tree)
  * real prices
  * real interactions (reviewerID, asin, rating, timestamp) -> recommendations
  * `also_buy` / `also_view` -> ready-made co-view signal
  * it is the canonical recommendation benchmark, so HR@10 numbers are
    comparable with published results

Data quirks this script handles (learned the hard way)
  * every value in the 2018 files is a string, including numbers ("overall": "5.0")
  * the category path, description and image URLs are Python-repr strings
    ("['Women', 'Dresses']"), not JSON arrays -> parsed with ast.literal_eval
  * the category path is polluted with spec text ("Water resistant to 99 feet"),
    and most items have no price (~35% do) or no image (~61% do)
    -> candidates are drawn from an 8x larger pool before filtering

Pipeline (3 streaming passes, memory stays flat; no pandas needed)

  pass 1  reviews  -> per-item interaction count + rating sum
  pick    top-(N x 8) items by interactions as candidates
  pass 2  metadata -> quality filters + category vocabulary + product records
  pass 3  reviews  -> interactions.csv.gz for the kept items only

Run
  python3 pipelines/build_catalog.py --max-products 6000

Outputs (data/processed/)
  products.jsonl      one real product per line -> seeds Postgres
  interactions.csv.gz user_id, asin, rating, timestamp
  categories.json     departments + top leaves (shop filter chips)
  stats.json          counts, coverage, price spread, sparsity -> EDA / README
"""

import argparse
import ast
import csv
import gzip
import html
import json
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE.parent / "data"
RAW_DIR = DATA_DIR / "raw"
OUT_DIR = DATA_DIR / "processed"

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
PRICE_RE = re.compile(r"([0-9][0-9,]*\.?[0-9]{0,2})")
QUOTED_RE = re.compile(r"'([^']*)'|\"([^\"]*)\"")

# Some metadata rows have Amazon's inline page script scraped into a text field
# instead of the real content: 117 titles are literally the same
# "var aPageStart = (new Date()).getTime(); ..." blob. Those products are dropped
# (a title cannot be invented), while descriptions keep their real text once the
# CSS/script blocks are stripped.
SCRAPED_SCRIPT_RE = re.compile(
    r"(var\s+\w+\s*=|window\.|document\.|function\s*\(|new Date|getTime\(\)|"
    r"<script|ue_csm|ue_lhb|ueinit|aPageStart)",
    re.I,
)
# <style>…</style> / <script>…</script> including their contents: stripping only the
# tags (as TAG_RE does) leaves raw CSS behind in the description.
SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.I | re.S)
# leftover CSS rules like ".auto-style1 { font-family: ...; }"
CSS_BLOCK_RE = re.compile(r"[^{}]*\{[^{}]*\}")
DEPARTMENTS = ("Women", "Men", "Girls", "Boys", "Baby")
ROOT_CATEGORY = "clothing, shoes & jewelry"

# Amazon glues spec bullets onto the end of the category path ("Rubber sole",
# "Imported", "Machine Wash"). These are not categories.
SPEC_STOPLIST = {
    "imported", "machine wash", "hand wash", "line dry", "tumble dry", "do not bleach",
    "rubber sole", "synthetic sole", "leather sole", "manmade sole", "platform",
    "water resistant", "waterproof", "closure", "pull on", "button", "zipper",
    "cotton", "polyester", "spandex", "leather", "fabric", "material", "faux leather",
    "features", "details", "product details", "item model number", "department",
    "best sellers rank", "customer reviews", "care instructions", "package dimensions",
}

# Category names are chosen from the first nodes of the path only: everything
# past that is spec text (verified against the raw file).
CATEGORY_DEPTH = 4


def log(msg):
    print(msg, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- parsing ---

def to_float(value):
    """The 2018 files store numbers as strings ("overall": "5.0"), so coerce."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_int(value):
    number = to_float(value)
    return int(number) if number is not None else None


def repr_list(value):
    """Turn either a real list or a Python-repr string into a list of strings.

    "['Women', 'Dresses']" -> ["Women", "Dresses"]
    """
    if isinstance(value, list):
        return [str(v) for v in value]
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        parsed = ast.literal_eval(value)
        if isinstance(parsed, list):
            return [str(v) for v in parsed]
        if isinstance(parsed, str):
            return [parsed]
    except (ValueError, SyntaxError):
        pass
    # fallback: pull out the quoted pieces of a malformed repr
    return [a or b for a, b in QUOTED_RE.findall(value)]


def clean_text(value):
    """Amazon descriptions/features are HTML. Make them plain single-line text.

    Order matters: whole <script>/<style> blocks are removed first (their CSS would
    otherwise survive as text), then tags, then any leftover CSS rules.
    """
    if isinstance(value, list):
        value = " ".join(str(v) for v in value)
    if not isinstance(value, str):
        return ""
    text = SCRIPT_STYLE_RE.sub(" ", value)
    text = html.unescape(TAG_RE.sub(" ", text))
    text = CSS_BLOCK_RE.sub(" ", text)
    text = text.replace("\\n", " ").replace("\\u", " ")
    return WS_RE.sub(" ", text).strip()


def is_scraped_script(text):
    return bool(text) and bool(SCRAPED_SCRIPT_RE.search(text))


def parse_price_usd(raw):
    """'$12.99' / '$9.99 - $19.99' -> 12.99 / 9.99. None if unusable."""
    if not isinstance(raw, str) or "$" not in raw:
        return None
    match = PRICE_RE.search(raw.replace("$", ""))
    if not match:
        return None
    try:
        value = float(match.group(1).replace(",", ""))
    except ValueError:
        return None
    # plausibility filter: drops 0.01 placeholders and obvious data errors
    return value if 1.0 <= value <= 1000.0 else None


def read_jsonl(path):
    """Stream a .json.gz or .jsonl file (never loads it all into memory).

    Tolerates a truncated file (e.g. a download still in progress) and broken
    lines, so a long run is not lost to one bad record.
    """
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
        try:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
        except (EOFError, OSError, gzip.BadGzipFile):
            log(f"  (stopped early — {path} looks truncated)")
            return


# --------------------------------------------------------------- metadata ---

def is_real_category(name):
    """The path is polluted with spec text; keep only category-looking names."""
    if not name or len(name) > 40:
        return False
    if "[" in name or "\n" in name:
        return False
    if name.lower() in SPEC_STOPLIST:
        return False
    if any(ch.isdigit() for ch in name):
        return False
    if ":" in name or name.lower().startswith("size"):
        return False
    return True


def category_path(meta):
    """Raw category tree -> list of clean path names (root removed)."""
    names = [c.strip() for c in repr_list(meta.get("category"))]
    return [c for c in names
            if is_real_category(c) and c.lower() != ROOT_CATEGORY]


def department_of(path):
    return next((d for d in DEPARTMENTS if d in path), "Other")


def first_image(meta):
    for key in ("imageURLHighRes", "imageURL"):
        for url in repr_list(meta.get(key)):
            if url.startswith("http"):
                return url
    return None


def text_list(meta, *keys):
    """First non-empty of the given keys, as a list of strings."""
    for key in keys:
        values = repr_list(meta.get(key))
        if values:
            return values
        raw = meta.get(key)
        if isinstance(raw, str) and raw.strip() and not raw.strip().startswith("["):
            return [raw]
    return []


GENERIC_BRAND_WORDS = {"amazon", "generic", "the", "new", "men", "women", "kids",
                       "mens", "womens", "unisex", "unknown", "n/a"}


def infer_brand(title, brand_lookup, max_tokens=3):
    """Some rows have no brand field at all (even Levi's 501). Recover it by
    matching the first words of the title against brands seen elsewhere in the
    dataset — a lookup, not a guess. Returns None when nothing matches.
    """
    tokens = [t for t in re.split(r"\s+", title or "") if t]
    for size in range(min(max_tokens, len(tokens)), 0, -1):
        candidate = " ".join(tokens[:size]).strip(" ,.-'")
        canonical = brand_lookup.get(candidate.lower())
        if canonical:
            return canonical
    return None


# ------------------------------------------------------------------- main ---

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reviews", default=str(RAW_DIR / "reviews_clothing_5core.json.gz"))
    ap.add_argument("--meta", default=str(RAW_DIR / "meta_clothing.json.gz"))
    ap.add_argument("--max-products", type=int, default=6000,
                    help="catalog size kept (top items by interaction count)")
    ap.add_argument("--candidate-multiplier", type=int, default=8,
                    help="how many candidates to scan per kept slot (filters drop most)")
    ap.add_argument("--min-interactions", type=int, default=6,
                    help="an item needs at least this many reviews to be eligible")
    ap.add_argument("--min-leaf-count", type=int, default=150,
                    help="a path name must repeat at least this often to count as a category")
    ap.add_argument("--usd-idr", type=float, default=16000.0,
                    help="fixed demo conversion rate, documented in README")
    ap.add_argument("--max-features", type=int, default=5)
    ap.add_argument("--max-description-chars", type=int, default=600)
    ap.add_argument("--drop-unknown-department", action="store_true",
                    help="drop items whose path has no Women/Men/Girls/Boys/Baby node")
    args = ap.parse_args()

    for path in (args.reviews, args.meta):
        if not Path(path).exists():
            log(f"missing input: {path}\nrun pipelines/download_data.sh first")
            return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stats = {}

    # ---------- pass 1: interaction counts + ratings ----------
    log("pass 1/3: counting interactions from reviews …")
    item_count = Counter()
    rating_sum = Counter()
    n_reviews = 0
    for row in read_jsonl(args.reviews):
        asin = row.get("asin")
        if not asin:
            continue
        n_reviews += 1
        item_count[asin] += 1
        rating = to_float(row.get("overall") or row.get("rating"))
        if rating is not None:
            rating_sum[asin] += rating
        if n_reviews % 2_000_000 == 0:
            log(f"  … {n_reviews:,} reviews, {len(item_count):,} distinct items")

    log(f"  reviews read: {n_reviews:,} | distinct items: {len(item_count):,}")
    stats["reviews_read"] = n_reviews
    stats["items_in_reviews"] = len(item_count)

    # ---------- pick the candidate pool ----------
    eligible = sorted(((count, asin) for asin, count in item_count.items()
                       if count >= args.min_interactions), reverse=True)
    pool_size = args.max_products * args.candidate_multiplier
    wanted = {asin for _, asin in eligible[:pool_size]}
    log(f"  items with >= {args.min_interactions} reviews: {len(eligible):,}"
        f" | candidate pool: {len(wanted):,}")

    # ---------- pass 2: metadata ----------
    log("pass 2/3: reading metadata …")
    leaf_freq = Counter()    # every path name in the file -> finds real categories
    brand_lookup = {}        # lowercase brand -> canonical spelling
    brand_freq = Counter()
    candidates = []
    seen_asins = set()
    dropped = Counter()
    for meta in read_jsonl(args.meta):
        asin = meta.get("asin")

        # brand spellings are collected from the whole file (not just candidates)
        # so that a missing brand can be looked up even when rare
        brand = clean_text(meta.get("brand"))[:120]
        if brand:
            brand_freq[brand] += 1

        if asin not in wanted:
            continue
        if asin in seen_asins:
            # the metadata file repeats some ASINs (same item, twice)
            dropped["duplicate_meta_row"] += 1
            continue
        seen_asins.add(asin)

        path = category_path(meta)
        leaf_freq.update(path[:CATEGORY_DEPTH])

        title = clean_text(meta.get("title"))[:300]
        price_usd = parse_price_usd(meta.get("price"))
        image_url = first_image(meta)
        department = department_of(path)

        if not title:
            dropped["no_title"] += 1
            continue
        if is_scraped_script(title):
            # scraped page script instead of a product name — unusable, and a
            # title must never be invented to fill the gap
            dropped["scraped_script_title"] += 1
            continue
        if price_usd is None:
            dropped["no_price"] += 1
            continue
        if not image_url:
            dropped["no_image"] += 1
            continue
        if args.drop_unknown_department and department == "Other":
            dropped["unknown_department"] += 1
            continue

        features = [clean_text(f)[:300] for f in text_list(meta, "feature", "features")]
        features = [f for f in features if f and not is_scraped_script(f)][: args.max_features]

        description = clean_text(text_list(meta, "description"))
        if is_scraped_script(description):
            description = ""
        if len(description) > args.max_description_chars:
            description = description[: args.max_description_chars].rsplit(" ", 1)[0] + " …"

        count = item_count[asin]
        candidates.append({
            "asin": asin,
            "title": title,
            "brand": brand or None,
            "priceUsd": round(price_usd, 2),
            "priceIdr": int(round(price_usd * args.usd_idr / 100.0) * 100),
            "department": department,
            "categoryPath": path,
            "description": description or None,
            "features": features,
            "imageUrl": image_url,
            "avgRating": round(rating_sum[asin] / count, 2) if count else None,
            "ratingCount": count,
            "nInteractions": count,
            "alsoBuy": text_list(meta, "also_buy"),
            "alsoView": text_list(meta, "also_view"),
        })

    # A real category name is one that repeats across the catalog: this throws
    # away one-off spec sentences that Amazon glued into the path field.
    vocabulary = {name for name, freq in leaf_freq.items() if freq >= args.min_leaf_count}
    log(f"  category vocabulary: {len(vocabulary):,} names from {len(leaf_freq):,} distinct path strings")

    # brands seen in the file become the lookup used to fill missing ones:
    # every spelling is a valid key, generic words are excluded so that
    # "Men's ..." can never become a brand
    brand_lookup = {b.lower(): b for b, _ in brand_freq.items()
                    if len(b) >= 3 and b.lower() not in GENERIC_BRAND_WORDS}

    for product in candidates:
        product["category"] = next(
            (name for name in reversed(product["categoryPath"][:CATEGORY_DEPTH])
             if name in vocabulary and name not in DEPARTMENTS),
            product["department"],
        )
        if product["brand"]:
            product["brandSource"] = "metadata"
        else:
            inferred = infer_brand(product["title"], brand_lookup)
            product["brand"] = inferred
            product["brandSource"] = "title_match" if inferred else None

    log(f"  candidates passing filters: {len(candidates):,} | dropped: {dict(dropped)}")
    stats["metadata_dropped"] = dict(dropped)
    stats["category_vocabulary_size"] = len(vocabulary)

    # keep the most-interacted items that survived the quality filters
    candidates.sort(key=lambda p: -p["nInteractions"])
    products = candidates[: args.max_products]
    if not products:
        log("no products survived the filters — nothing written")
        return 1
    log(f"  kept: {len(products):,}")

    # stable integer id (the shop + API use ints); asin stays the ML key
    products.sort(key=lambda p: (-p["nInteractions"], p["asin"]))
    kept = set()
    for index, product in enumerate(products, start=1):
        product["id"] = index
        kept.add(product["asin"])

    # keep only co-view links that point inside our own catalog
    for product in products:
        product["alsoBuy"] = [a for a in dict.fromkeys(product["alsoBuy"]) if a in kept][:20]
        product["alsoView"] = [a for a in dict.fromkeys(product["alsoView"]) if a in kept][:20]

    # ---------- pass 3: interactions for kept items only ----------
    log("pass 3/3: writing interactions …")
    interactions_path = OUT_DIR / "interactions.csv.gz"
    n_kept_rows = 0
    users = set()
    with gzip.open(interactions_path, "wt", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["user_id", "asin", "rating", "timestamp"])
        for row in read_jsonl(args.reviews):
            asin = row.get("asin")
            if asin not in kept:
                continue
            user_id = row.get("reviewerID") or row.get("user_id")
            writer.writerow([
                user_id,
                asin,
                to_float(row.get("overall") or row.get("rating")),
                to_int(row.get("unixReviewTime") or row.get("timestamp")),
            ])
            n_kept_rows += 1
            users.add(user_id)

    with open(OUT_DIR / "products.jsonl", "w", encoding="utf-8") as fh:
        for product in products:
            fh.write(json.dumps(product, ensure_ascii=False) + "\n")

    # ---------- summary ----------
    departments = Counter(p["department"] for p in products)
    top_categories = Counter(p["category"] for p in products).most_common(30)
    brands = Counter(p["brand"] for p in products if p["brand"])
    prices = sorted(p["priceUsd"] for p in products)
    interactions = sorted(p["nInteractions"] for p in products)

    def pct(values, q):
        if not values:
            return None
        return values[min(len(values) - 1, int(len(values) * q))]

    stats.update({
        "products_written": len(products),
        "interactions_written": n_kept_rows,
        "distinct_users": len(users),
        "departments": dict(departments),
        "top_categories": top_categories,
        "distinct_brands": len(brands),
        "top_brands": brands.most_common(15),
        "interactions_per_item": {
            "min": interactions[0],
            "median": pct(interactions, 0.5),
            "max": interactions[-1],
        },
        "sparsity": {
            "matrix_cells": len(products) * len(users),
            "filled": n_kept_rows,
            "density": round(n_kept_rows / (len(products) * len(users)), 8) if users else None,
        },
        "price_usd": {
            "min": prices[0],
            "p50": pct(prices, 0.5),
            "p90": pct(prices, 0.9),
            "max": prices[-1],
        },
        "catalog_has_description": sum(1 for p in products if p["description"]),
        "catalog_has_features": sum(1 for p in products if p["features"]),
        "catalog_has_brand": sum(1 for p in products if p["brand"]),
        "catalog_brand_from_metadata": sum(1 for p in products if p["brandSource"] == "metadata"),
        "catalog_brand_from_title": sum(1 for p in products if p["brandSource"] == "title_match"),
        "catalog_with_co_view": sum(1 for p in products
                                    if p["alsoBuy"] or p["alsoView"]),
        "usd_idr_rate_used": args.usd_idr,
        "min_interactions_filter": args.min_interactions,
    })

    with open(OUT_DIR / "stats.json", "w", encoding="utf-8") as fh:
        json.dump(stats, fh, indent=2, ensure_ascii=False)

    with open(OUT_DIR / "categories.json", "w", encoding="utf-8") as fh:
        json.dump({
            "departments": [d for d, _ in departments.most_common()],
            "categories": [c for c, _ in top_categories],
        }, fh, indent=2, ensure_ascii=False)

    log("")
    log(f"products:     {len(products):,} -> {OUT_DIR / 'products.jsonl'}")
    log(f"interactions: {n_kept_rows:,} from {len(users):,} users -> {interactions_path}")
    log(f"departments:  {dict(departments)}")
    log(f"top cats:     {[c for c, _ in top_categories[:10]]}")
    log(f"price USD:    min {prices[0]} / p50 {pct(prices, 0.5)} / p90 {pct(prices, 0.9)} / max {prices[-1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
