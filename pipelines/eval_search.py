#!/usr/bin/env python3
"""
IR Evaluation Harness for Toko Marcell Search (M5 & M6).

Compares:
  - Baseline: In-process BM25 (lexical)
  - Treatment: Hybrid Search (BM25 + fastembed all-MiniLM-L6-v2 via pgvector + RRF)

Queries:
  32 queries across 4 categories:
  - Exact Brand / SKU (8 queries)
  - Category & Explicit Features (8 queries)
  - Semantic / Intent / Functional (8 queries)
  - Indonesian / Casual / Cross-lingual (8 queries)

Metrics:
  - HitRate@10 (Recall@10): 1 if >=1 relevant item in top 10, else 0
  - Precision@10: relevant items / 10
  - MRR@10: Mean Reciprocal Rank of first relevant item
  - nDCG@10: Normalized Discounted Cumulative Gain with graded relevance (0, 1, 2)

Run:
  python3 pipelines/eval_search.py [--api-url http://localhost:8001]
"""

import argparse
import json
import math
import sys
import time
import urllib.parse
import urllib.request

DEFAULT_API_URL = "http://localhost:8001"

# ── Benchmark Queries & Relevance Specs ───────────────────────────────────────
# Each query defines:
#   - query: text
#   - category: group label
#   - must_match: tokens/phrases where at least one gives grade 1
#   - exact_match: tokens/phrases where at least one gives grade 2
#   - disallowed: tokens/categories that disqualify (grade 0)

BENCHMARK_QUERIES = [
    # ── Group 1: Exact Brand & SKU (8 queries) ───────────────────────────────
    {
        "query": "levis 501",
        "group": "Brand/SKU",
        "exact_match": ["501", "levi strauss 501"],
        "must_match": ["levi", "jean", "denim"],
        "disallowed": ["shoelace", "sock"],
    },
    {
        "query": "calvin klein boxer briefs",
        "group": "Brand/SKU",
        "exact_match": ["calvin klein", "boxer brief", "boxer"],
        "must_match": ["calvin", "klein", "brief", "underwear"],
        "disallowed": ["perfume", "dress"],
    },
    {
        "query": "dickies 874",
        "group": "Brand/SKU",
        "exact_match": ["874", "dickies 874"],
        "must_match": ["dickies", "work pant", "pant", "trouser"],
        "disallowed": [],
    },
    {
        "query": "carhartt jacket",
        "group": "Brand/SKU",
        "exact_match": ["carhartt", "jacket", "coat"],
        "must_match": ["carhartt", "outerwear", "duck"],
        "disallowed": ["beanie", "sock"],
    },
    {
        "query": "skechers memory foam",
        "group": "Brand/SKU",
        "exact_match": ["memory foam", "skechers"],
        "must_match": ["skechers", "sneaker", "shoe", "foam"],
        "disallowed": [],
    },
    {
        "query": "under armour heatgear",
        "group": "Brand/SKU",
        "exact_match": ["heatgear", "under armour"],
        "must_match": ["under armour", "compression", "shirt", "short"],
        "disallowed": [],
    },
    {
        "query": "adidas superstar",
        "group": "Brand/SKU",
        "exact_match": ["superstar", "adidas superstar"],
        "must_match": ["adidas", "sneaker", "shoe", "originals"],
        "disallowed": [],
    },
    {
        "query": "ray ban aviator",
        "group": "Brand/SKU",
        "exact_match": ["aviator", "ray-ban", "ray ban"],
        "must_match": ["sunglass", "glasses", "eyewear"],
        "disallowed": [],
    },

    # ── Group 2: Category & Explicit Feature (8 queries) ─────────────────────
    {
        "query": "white leather sneakers",
        "group": "Category/Attr",
        "exact_match": ["white", "leather", "sneaker"],
        "must_match": ["sneaker", "shoe", "athletic"],
        "disallowed": ["boot", "sandal", "heel"],
    },
    {
        "query": "black waterproof boots",
        "group": "Category/Attr",
        "exact_match": ["waterproof", "boot", "black"],
        "must_match": ["boot", "waterproof", "leather"],
        "disallowed": ["sandal", "sneaker"],
    },
    {
        "query": "slim fit stretch jeans",
        "group": "Category/Attr",
        "exact_match": ["slim fit", "stretch", "jean", "denim"],
        "must_match": ["jean", "pants", "denim"],
        "disallowed": ["jacket", "shirt"],
    },
    {
        "query": "fleece lined hoodie",
        "group": "Category/Attr",
        "exact_match": ["fleece", "hoodie", "hooded"],
        "must_match": ["sweatshirt", "hood", "pullover", "jacket"],
        "disallowed": ["sock", "glove"],
    },
    {
        "query": "polarized sunglasses",
        "group": "Category/Attr",
        "exact_match": ["polarized", "sunglass"],
        "must_match": ["eyewear", "glasses", "uv"],
        "disallowed": ["case", "cleaner"],
    },
    {
        "query": "genuine leather bifold wallet",
        "group": "Category/Attr",
        "exact_match": ["bifold", "wallet", "leather"],
        "must_match": ["wallet", "card case", "leather"],
        "disallowed": ["handbag", "belt"],
    },
    {
        "query": "yoga leggings high waist",
        "group": "Category/Attr",
        "exact_match": ["yoga", "legging", "high waist"],
        "must_match": ["tights", "pants", "athletic", "spandex"],
        "disallowed": ["top", "bra"],
    },
    {
        "query": "down winter parka coat",
        "group": "Category/Attr",
        "exact_match": ["down", "parka", "winter", "coat"],
        "must_match": ["jacket", "outerwear", "insulat"],
        "disallowed": ["swim", "shorts"],
    },

    # ── Group 3: Semantic / Intent / Functional (8 queries) ──────────────────
    {
        "query": "raincoat for winter storm",
        "group": "Semantic/Intent",
        "exact_match": ["raincoat", "waterproof", "storm", "rain jacket"],
        "must_match": ["jacket", "coat", "parka", "windbreaker", "outerwear"],
        "disallowed": ["wallet", "dress"],
    },
    {
        "query": "comfy shoes for all day standing",
        "group": "Semantic/Intent",
        "exact_match": ["walking shoe", "comfort", "cushion", "clog"],
        "must_match": ["shoe", "sneaker", "footwear", "loafer", "insole"],
        "disallowed": ["high heel", "stiletto"],
    },
    {
        "query": "formal wedding guest dress",
        "group": "Semantic/Intent",
        "exact_match": ["cocktail dress", "evening gown", "formal dress"],
        "must_match": ["dress", "gown", "maxi", "lace", "party"],
        "disallowed": ["t-shirt", "pajama", "swimsuit"],
    },
    {
        "query": "breathable clothes for hot summer",
        "group": "Semantic/Intent",
        "exact_match": ["linen", "breathable", "summer", "cool"],
        "must_match": ["shirt", "shorts", "tee", "tank", "cotton", "dress"],
        "disallowed": ["fleece", "heavy winter", "wool coat"],
    },
    {
        "query": "sweat wicking gym workout clothes",
        "group": "Semantic/Intent",
        "exact_match": ["moisture wicking", "dry fit", "compression", "workout"],
        "must_match": ["athletic", "gym", "short", "shirt", "legging", "tank"],
        "disallowed": ["suit", "leather jacket"],
    },
    {
        "query": "cozy loungewear for working from home",
        "group": "Semantic/Intent",
        "exact_match": ["lounge", "jogger", "sweatpant", "pajama"],
        "must_match": ["pant", "fleece", "cotton", "robe", "hoodie", "sleepwear"],
        "disallowed": ["tie", "dress shoes"],
    },
    {
        "query": "heavy duty outdoor work trousers",
        "group": "Semantic/Intent",
        "exact_match": ["cargo pant", "work pant", "utility", "carpenter"],
        "must_match": ["pant", "jean", "trouser", "canvas", "denim"],
        "disallowed": ["dress", "skirt"],
    },
    {
        "query": "minimalist casual everyday wristwatch",
        "group": "Semantic/Intent",
        "exact_match": ["analog watch", "minimalist watch", "wrist watch"],
        "must_match": ["watch", "dial", "leather band", "stainless steel"],
        "disallowed": ["necklace", "ring"],
    },

    # ── Group 4: Indonesian / Casual / Cross-lingual (8 queries) (M6) ────────
    {
        "query": "baju santai rumahan adem",
        "group": "Indonesian/M6",
        "exact_match": ["lounge", "t-shirt", "cotton tee", "sleepwear", "pajama"],
        "must_match": ["shirt", "top", "tee", "tank", "short", "casual"],
        "disallowed": ["heavy coat", "winter boot"],
    },
    {
        "query": "sepatu lari empuk nyaman",
        "group": "Indonesian/M6",
        "exact_match": ["running shoe", "cushion", "trail running", "sneaker"],
        "must_match": ["running", "shoe", "athletic", "sneaker"],
        "disallowed": ["high heel", "boot"],
    },
    {
        "query": "celana kantor formal hitam",
        "group": "Indonesian/M6",
        "exact_match": ["black dress pant", "dress trousers", "slacks"],
        "must_match": ["black", "pant", "trouser", "formal", "suit"],
        "disallowed": ["short", "sweatpant", "jeans"],
    },
    {
        "query": "jaket musim hujan anti air",
        "group": "Indonesian/M6",
        "exact_match": ["rain jacket", "waterproof", "raincoat", "windbreaker"],
        "must_match": ["jacket", "coat", "hooded", "water resistant"],
        "disallowed": ["swimwear", "tank top"],
    },
    {
        "query": "tas ransel laptop kerja",
        "group": "Indonesian/M6",
        "exact_match": ["laptop backpack", "backpack", "daypack"],
        "must_match": ["backpack", "bag", "pack", "laptop"],
        "disallowed": ["wallet", "clutch"],
    },
    {
        "query": "kaos polo polos pria",
        "group": "Indonesian/M6",
        "exact_match": ["polo shirt", "men's polo", "solid polo"],
        "must_match": ["polo", "shirt", "collar", "short sleeve"],
        "disallowed": ["dress", "skirt"],
    },
    {
        "query": "kacamata hitam anti silau",
        "group": "Indonesian/M6",
        "exact_match": ["polarized sunglass", "anti glare", "sunglasses"],
        "must_match": ["sunglass", "glasses", "eyewear", "polarized"],
        "disallowed": ["watch", "wallet"],
    },
    {
        "query": "dompet kulit asli pria",
        "group": "Indonesian/M6",
        "exact_match": ["leather wallet", "bifold wallet", "men's wallet"],
        "must_match": ["wallet", "leather", "billfold", "bifold"],
        "disallowed": ["dress", "shoes"],
    },
]


def judge_item(spec: dict, item: dict) -> int:
    """Grade an item 0 (irrelevant), 1 (partially relevant), or 2 (highly relevant)."""
    text = " ".join([
        item.get("title") or "",
        item.get("brand") or "",
        item.get("category") or "",
        item.get("department") or "",
        " ".join(item.get("features") or []),
        (item.get("description") or "")[:300],
    ]).lower()

    # Check disqualifiers
    for bad in spec.get("disallowed", []):
        if bad.lower() in text:
            return 0

    # Grade 2: Matches exact target attributes
    for exact in spec.get("exact_match", []):
        if exact.lower() in text:
            return 2

    # Grade 1: Matches general intent / category
    matches_must = 0
    for must in spec.get("must_match", []):
        if must.lower() in text:
            matches_must += 1

    if matches_must >= 1:
        return 1

    return 0


def compute_dcg(grades: list[int], k: int = 10) -> float:
    dcg = 0.0
    for i, g in enumerate(grades[:k]):
        dcg += (2**g - 1) / math.log2(i + 2)
    return dcg


def compute_ndcg(grades: list[int], k: int = 10) -> float:
    dcg = compute_dcg(grades, k)
    ideal_grades = sorted(grades, reverse=True)
    idcg = compute_dcg(ideal_grades, k)
    if idcg <= 0.0:
        return 0.0
    return round(dcg / idcg, 4)


def compute_mrr(grades: list[int], k: int = 10) -> float:
    for i, g in enumerate(grades[:k]):
        if g >= 1:
            return round(1.0 / (i + 1), 4)
    return 0.0


def fetch_search_results(api_url: str, query: str, mode: str, limit: int = 10) -> list[dict]:
    params = urllib.parse.urlencode({"q": query, "mode": mode, "limit": limit})
    url = f"{api_url}/search?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "TokoMarcell-Eval/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("items", [])
    except Exception as e:
        print(f"Error querying {url}: {e}", file=sys.stderr)
        return []


def run_eval(api_url: str):
    print(f"\nEvaluating Search: BM25 Baseline vs Hybrid (FastEmbed + pgvector + RRF)")
    print(f"Target API: {api_url}")
    print(f"Total benchmark queries: {len(BENCHMARK_QUERIES)}")
    print("=" * 80)

    results = []

    for i, spec in enumerate(BENCHMARK_QUERIES, 1):
        q = spec["query"]
        group = spec["group"]

        # Run BM25
        t0 = time.perf_counter()
        bm25_items = fetch_search_results(api_url, q, mode="bm25", limit=10)
        bm25_time = (time.perf_counter() - t0) * 1000

        # Run Hybrid
        t0 = time.perf_counter()
        hybrid_items = fetch_search_results(api_url, q, mode="hybrid", limit=10)
        hybrid_time = (time.perf_counter() - t0) * 1000

        # Grade items
        bm25_grades = [judge_item(spec, item) for item in bm25_items]
        hybrid_grades = [judge_item(spec, item) for item in hybrid_items]

        # Calculate metrics
        bm25_p10 = sum(1 for g in bm25_grades if g >= 1) / 10.0
        hybrid_p10 = sum(1 for g in hybrid_grades if g >= 1) / 10.0

        bm25_hr10 = 1.0 if any(g >= 1 for g in bm25_grades) else 0.0
        hybrid_hr10 = 1.0 if any(g >= 1 for g in hybrid_grades) else 0.0

        bm25_mrr = compute_mrr(bm25_grades, k=10)
        hybrid_mrr = compute_mrr(hybrid_grades, k=10)

        bm25_ndcg = compute_ndcg(bm25_grades, k=10)
        hybrid_ndcg = compute_ndcg(hybrid_grades, k=10)

        results.append({
            "query": q,
            "group": group,
            "bm25_p10": bm25_p10,
            "hybrid_p10": hybrid_p10,
            "bm25_hr10": bm25_hr10,
            "hybrid_hr10": hybrid_hr10,
            "bm25_mrr": bm25_mrr,
            "hybrid_mrr": hybrid_mrr,
            "bm25_ndcg": bm25_ndcg,
            "hybrid_ndcg": hybrid_ndcg,
            "bm25_time": bm25_time,
            "hybrid_time": hybrid_time,
        })

        sys.stdout.write(f"\r  Evaluating [{i}/{len(BENCHMARK_QUERIES)}]: {q:<35}")
        sys.stdout.flush()

    print("\n" + "=" * 80)
    print(f"{'Query':<35} | {'Group':<14} | {'BM25 P@10':<9} | {'Hyb P@10':<9} | {'BM25 nDCG':<9} | {'Hyb nDCG':<9}")
    print("-" * 95)
    for r in results:
        print(
            f"{r['query']:<35} | {r['group']:<14} | "
            f"{r['bm25_p10']:<9.2f} | {r['hybrid_p10']:<9.2f} | "
            f"{r['bm25_ndcg']:<9.2f} | {r['hybrid_ndcg']:<9.2f}"
        )

    # ── Group Breakdown ───────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("SUMMARY BY QUERY GROUP:")
    print("-" * 80)
    print(f"{'Group':<18} | {'N':<3} | {'BM25 P@10':<9} | {'Hyb P@10':<9} | {'BM25 nDCG':<9} | {'Hyb nDCG':<9} | {'BM25 MRR':<8} | {'Hyb MRR':<8}")
    print("-" * 80)

    groups = sorted(set(r["group"] for r in results))
    for grp in groups:
        sub = [r for r in results if r["group"] == grp]
        n = len(sub)
        avg_bm25_p = sum(r["bm25_p10"] for r in sub) / n
        avg_hyb_p = sum(r["hybrid_p10"] for r in sub) / n
        avg_bm25_ndcg = sum(r["bm25_ndcg"] for r in sub) / n
        avg_hyb_ndcg = sum(r["hybrid_ndcg"] for r in sub) / n
        avg_bm25_mrr = sum(r["bm25_mrr"] for r in sub) / n
        avg_hyb_mrr = sum(r["hybrid_mrr"] for r in sub) / n

        print(
            f"{grp:<18} | {n:<3} | "
            f"{avg_bm25_p:<9.3f} | {avg_hyb_p:<9.3f} | "
            f"{avg_bm25_ndcg:<9.3f} | {avg_hyb_ndcg:<9.3f} | "
            f"{avg_bm25_mrr:<8.3f} | {avg_hyb_mrr:<8.3f}"
        )

    # ── Macro Averages ────────────────────────────────────────────────────────
    total_n = len(results)
    tot_bm25_p = sum(r["bm25_p10"] for r in results) / total_n
    tot_hyb_p = sum(r["hybrid_p10"] for r in results) / total_n
    tot_bm25_hr = sum(r["bm25_hr10"] for r in results) / total_n
    tot_hyb_hr = sum(r["hybrid_hr10"] for r in results) / total_n
    tot_bm25_ndcg = sum(r["bm25_ndcg"] for r in results) / total_n
    tot_hyb_ndcg = sum(r["hybrid_ndcg"] for r in results) / total_n
    tot_bm25_mrr = sum(r["bm25_mrr"] for r in results) / total_n
    tot_hyb_mrr = sum(r["hybrid_mrr"] for r in results) / total_n

    print("-" * 80)
    print(
        f"{'OVERALL (Macro)':<18} | {total_n:<3} | "
        f"{tot_bm25_p:<9.3f} | {tot_hyb_p:<9.3f} | "
        f"{tot_bm25_ndcg:<9.3f} | {tot_hyb_ndcg:<9.3f} | "
        f"{tot_bm25_mrr:<8.3f} | {tot_hyb_mrr:<8.3f}"
    )
    print("=" * 80)
    print(f"Overall HitRate@10 (Recall@10): BM25 = {tot_bm25_hr:.1%} vs Hybrid = {tot_hyb_hr:.1%}")
    p10_gain = ((tot_hyb_p - tot_bm25_p) / tot_bm25_p) * 100 if tot_bm25_p > 0 else 0
    ndcg_gain = ((tot_hyb_ndcg - tot_bm25_ndcg) / tot_bm25_ndcg) * 100 if tot_bm25_ndcg > 0 else 0
    print(f"P@10 Relative Gain:  {p10_gain:+.1f}%")
    print(f"nDCG@10 Relative Gain: {ndcg_gain:+.1f}%")

    return results


def main():
    parser = argparse.ArgumentParser(description="Evaluate BM25 vs Hybrid Search")
    parser.add_argument("--api-url", default=DEFAULT_API_URL, help="Base API URL")
    args = parser.parse_args()

    run_eval(args.api_url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
