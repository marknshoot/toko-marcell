#!/usr/bin/env python3
"""
IR Evaluation Harness for Toko Marcell Search (M5 & M6).

Compares search modes:
  - bm25:           In-process BM25 (lexical baseline)
  - vector:         Dense MiniLM via pgvector
  - hybrid:         BM25 + MiniLM + RRF (default treatment)
  - hybrid+rerank:  Hybrid + Stage-2 cross-encoder reranking
  - trimodal:       BM25 + MiniLM + CLIP text→image + RRF

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
  python3 pipelines/eval_search.py [--api-url http://localhost:8001] [--modes bm25,hybrid,vector]
  python3 pipelines/eval_search.py --out pipelines/results/search_eval.json
"""

import argparse
import datetime
import json
import math
import subprocess
import sys
import time
import urllib.parse
import urllib.request

DEFAULT_API_URL = "http://localhost:8001"
ALL_MODES = ["bm25", "vector", "hybrid", "hybrid+rerank", "trimodal"]

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

    for bad in spec.get("disallowed", []):
        if bad.lower() in text:
            return 0

    for exact in spec.get("exact_match", []):
        if exact.lower() in text:
            return 2

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


def fetch_search_results(api_url: str, query: str, mode: str, rerank: bool = False, limit: int = 10) -> list[dict]:
    """Fetch search results from the API. mode is the search mode; if rerank is True, &rerank=true is appended."""
    api_mode = mode.replace("+rerank", "")  # strip our synthetic suffix
    params = {"q": query, "mode": api_mode, "limit": limit}
    if rerank:
        params["rerank"] = "true"
    url = f"{api_url}/search?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "TokoMarcell-Eval/2.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("items", [])
    except Exception as e:
        print(f"Error querying {url}: {e}", file=sys.stderr)
        return []


def _git_commit_short():
    """Return the current short git commit hash, or 'unknown'."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def eval_mode(api_url: str, mode: str) -> list[dict]:
    """Evaluate one search mode on all benchmark queries. Returns per-query results."""
    is_rerank = "+rerank" in mode
    results = []
    for i, spec in enumerate(BENCHMARK_QUERIES, 1):
        q = spec["query"]
        group = spec["group"]

        t0 = time.perf_counter()
        items = fetch_search_results(api_url, q, mode, rerank=is_rerank, limit=10)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        grades = [judge_item(spec, item) for item in items]
        p10 = sum(1 for g in grades if g >= 1) / 10.0
        hr10 = 1.0 if any(g >= 1 for g in grades) else 0.0
        mrr = compute_mrr(grades, k=10)
        ndcg = compute_ndcg(grades, k=10)

        results.append({
            "query": q,
            "group": group,
            "mode": mode,
            "p10": p10,
            "hr10": hr10,
            "mrr": mrr,
            "ndcg": ndcg,
            "time_ms": round(elapsed_ms, 2),
        })

        sys.stdout.write(f"\r  [{mode}] Evaluating [{i}/{len(BENCHMARK_QUERIES)}]: {q:<35}")
        sys.stdout.flush()
    print()
    return results


def group_summary(results: list[dict]) -> dict:
    """Compute per-group and overall averages from per-query results."""
    groups = sorted(set(r["group"] for r in results))
    by_group = {}
    for grp in groups:
        sub = [r for r in results if r["group"] == grp]
        n = len(sub)
        by_group[grp] = {
            "n": n,
            "p10": round(sum(r["p10"] for r in sub) / n, 4),
            "hr10": round(sum(r["hr10"] for r in sub) / n, 4),
            "mrr": round(sum(r["mrr"] for r in sub) / n, 4),
            "ndcg": round(sum(r["ndcg"] for r in sub) / n, 4),
        }
    n = len(results)
    overall = {
        "n": n,
        "p10": round(sum(r["p10"] for r in results) / n, 4),
        "hr10": round(sum(r["hr10"] for r in results) / n, 4),
        "mrr": round(sum(r["mrr"] for r in results) / n, 4),
        "ndcg": round(sum(r["ndcg"] for r in results) / n, 4),
    }
    return {"by_group": by_group, "overall": overall}


def run_eval(api_url: str, modes: list[str]):
    """Run evaluation across all requested modes. Returns structured results dict."""
    print(f"\nEvaluating Search across modes: {', '.join(modes)}")
    print(f"Target API: {api_url}")
    print(f"Total benchmark queries: {len(BENCHMARK_QUERIES)}")
    print("=" * 80)

    all_results = {}
    for mode in modes:
        per_query = eval_mode(api_url, mode)
        summary = group_summary(per_query)
        all_results[mode] = {
            "per_query": per_query,
            "summary": summary,
        }

    # ── Print comparison table ─────────────────────────────────────────────────
    print("\n" + "=" * 100)
    header_modes = "  ".join(f"{'P@10':>6} {'nDCG':>6} {'MRR':>6}" for _ in modes)
    mode_labels = "  ".join(f"{'--- ' + m + ' ---':^20}" for m in modes)
    print(f"{'Group':<18} | {mode_labels}")
    print("-" * 100)

    groups = sorted(set(r["group"] for r in all_results[modes[0]]["per_query"]))
    for grp in groups:
        parts = [f"{grp:<18} |"]
        for m in modes:
            s = all_results[m]["summary"]["by_group"].get(grp, {})
            parts.append(f" {s.get('p10', 0):>6.3f} {s.get('ndcg', 0):>6.3f} {s.get('mrr', 0):>6.3f}")
        print("  ".join(parts))

    print("-" * 100)
    parts = [f"{'OVERALL (Macro)':<18} |"]
    for m in modes:
        s = all_results[m]["summary"]["overall"]
        parts.append(f" {s['p10']:>6.3f} {s['ndcg']:>6.3f} {s['mrr']:>6.3f}")
    print("  ".join(parts))
    print("=" * 100)

    # HitRate line
    for m in modes:
        hr = all_results[m]["summary"]["overall"]["hr10"]
        print(f"  {m}: HitRate@10 = {hr:.1%}")

    # Relative gains vs bm25 baseline if present
    if "bm25" in all_results:
        bm25_ndcg = all_results["bm25"]["summary"]["overall"]["ndcg"]
        bm25_mrr = all_results["bm25"]["summary"]["overall"]["mrr"]
        for m in modes:
            if m == "bm25":
                continue
            m_ndcg = all_results[m]["summary"]["overall"]["ndcg"]
            m_mrr = all_results[m]["summary"]["overall"]["mrr"]
            ndcg_gain = ((m_ndcg - bm25_ndcg) / bm25_ndcg) * 100 if bm25_ndcg > 0 else 0
            mrr_gain = ((m_mrr - bm25_mrr) / bm25_mrr) * 100 if bm25_mrr > 0 else 0
            print(f"  {m} vs bm25: nDCG@10 {ndcg_gain:+.1f}% | MRR@10 {mrr_gain:+.1f}%")

    return all_results


def main():
    parser = argparse.ArgumentParser(description="Evaluate Search across modes")
    parser.add_argument("--api-url", default=DEFAULT_API_URL, help="Base API URL")
    parser.add_argument(
        "--modes",
        default="bm25,vector,hybrid",
        help=f"Comma-separated search modes to evaluate. Available: {','.join(ALL_MODES)}",
    )
    parser.add_argument("--out", default=None, help="Write machine-readable JSON results to this path")
    args = parser.parse_args()

    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    for m in modes:
        if m not in ALL_MODES:
            print(f"Unknown mode: {m}. Available: {', '.join(ALL_MODES)}", file=sys.stderr)
            return 1

    all_results = run_eval(args.api_url, modes)

    if args.out:
        import pathlib
        out_path = pathlib.Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        output = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "git_commit": _git_commit_short(),
            "api_url": args.api_url,
            "query_count": len(BENCHMARK_QUERIES),
            "modes": {},
        }
        for mode, data in all_results.items():
            output["modes"][mode] = {
                "summary": data["summary"],
                "per_query": data["per_query"],
            }
        with open(out_path, "w") as f:
            json.dump(output, f, indent=2)
        print(f"\nResults written to {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
