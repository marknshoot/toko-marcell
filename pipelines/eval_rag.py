#!/usr/bin/env python3
"""
RAG retrieval comparison harness (Phase 5).

Compares two store-policy retrieval strategies on a small labelled query set:

  A. direct-chunk  — embed the query, match against ``store_knowledge.embedding``
  B. hyp-question  — embed the query, match against the per-chunk hypothetical
                     questions (``store_knowledge_questions.embedding``), then
                     return the parent chunk (deduped)

Both run against the live DB (needs the Docker stack). Each labelled query names
the expected parent chunk by a title substring; we report HitRate@1 / HitRate@3
and MRR@3 for each strategy, plus a per-query breakdown, and write a JSON
artifact.

Run:
  python3 pipelines/eval_rag.py --database-url postgresql://toko:toko@localhost:5432/toko \
    --out pipelines/results/rag_eval.json
"""

import argparse
import datetime
import json
import os
import subprocess
import sys

# Labelled queries: (query, expected_title_substring). Mix of ID + EN, and
# phrasings that deliberately differ from the chunk headers so the question
# index has a chance to help.
LABELLED = [
    ("ongkir ke surabaya berapa lama", "shipping"),
    ("gratis ongkir mulai berapa", "shipping"),
    ("kalau ukuran salah bisa tukar?", "return"),
    ("berapa lama batas retur barang", "return"),
    ("cara bayarnya gimana ya", "payment"),
    ("apakah pembayaran cuma simulasi", "payment"),
    ("dickies 874 ambil ukuran berapa", "dickies"),
    ("does champion run large or small", "champion"),
    ("levis 501 sama 505 beda apa", "levi"),
    ("birkenstock mending naik atau turun size", "birkenstock"),
    ("tinggi 170 berat 65 ukuran apa", "tops"),
    ("tabel ukuran celana berdasarkan pinggang", "bottoms"),
]

DEFAULT_DB = os.environ.get("DATABASE_URL", "postgresql://toko:toko@localhost:5432/toko")


def _embed(query, model):
    return [float(x) for x in next(iter(model.embed([query])))]


def _vec(v):
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def _direct_chunk(cur, qvec, k):
    cur.execute(
        """
        SELECT title
        FROM store_knowledge
        WHERE embedding IS NOT NULL
        ORDER BY embedding <=> %s::vector ASC
        LIMIT %s
        """,
        (_vec(qvec), k),
    )
    return [r[0] for r in cur.fetchall()]


def _hyp_question(cur, qvec, k):
    # pull extra question matches then dedupe to parent chunks, preserving order
    cur.execute(
        """
        SELECT k.title, k.id
        FROM store_knowledge_questions q
        JOIN store_knowledge k ON k.id = q.chunk_id
        WHERE q.embedding IS NOT NULL
        ORDER BY q.embedding <=> %s::vector ASC
        LIMIT %s
        """,
        (_vec(qvec), k * 5),
    )
    titles, seen = [], set()
    for title, cid in cur.fetchall():
        if cid in seen:
            continue
        seen.add(cid)
        titles.append(title)
        if len(titles) >= k:
            break
    return titles


def _hit_rank(titles, expected_sub):
    exp = expected_sub.lower()
    for i, t in enumerate(titles):
        if exp in (t or "").lower():
            return i + 1  # 1-based rank
    return 0


def _metrics(ranks, k=3):
    n = len(ranks)
    hr1 = sum(1 for r in ranks if r == 1) / n
    hrk = sum(1 for r in ranks if 1 <= r <= k) / n
    mrr = sum((1.0 / r) if r else 0.0 for r in ranks) / n
    return {"hr@1": round(hr1, 4), f"hr@{k}": round(hrk, 4), f"mrr@{k}": round(mrr, 4)}


def _git_commit():
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def run(db_url, k=3):
    import psycopg
    from fastembed import TextEmbedding

    model_name = os.environ.get("TEXT_EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    model = TextEmbedding(model_name=model_name, cache_dir=os.environ.get("FASTEMBED_CACHE_DIR", "/tmp/fastembed_cache"))

    direct_ranks, hyp_ranks, per_query = [], [], []
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('store_knowledge_questions')")
            if cur.fetchone()[0] is None:
                print("store_knowledge_questions table is missing — run knowledge_seed first.", file=sys.stderr)
                return None
            for query, expected in LABELLED:
                qvec = _embed(query, model)
                d_titles = _direct_chunk(cur, qvec, k)
                h_titles = _hyp_question(cur, qvec, k)
                d_rank = _hit_rank(d_titles, expected)
                h_rank = _hit_rank(h_titles, expected)
                direct_ranks.append(d_rank)
                hyp_ranks.append(h_rank)
                per_query.append({
                    "query": query,
                    "expected": expected,
                    "direct_rank": d_rank,
                    "hyp_rank": h_rank,
                })

    result = {
        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
        "git_commit": _git_commit(),
        "embedding_model": model_name,
        "k": k,
        "query_count": len(LABELLED),
        "direct_chunk": _metrics(direct_ranks, k),
        "hypothetical_question": _metrics(hyp_ranks, k),
        "per_query": per_query,
    }
    return result


def main():
    ap = argparse.ArgumentParser(description="Compare direct-chunk vs hypothetical-question RAG retrieval")
    ap.add_argument("--database-url", default=DEFAULT_DB)
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    result = run(args.database_url, k=args.k)
    if result is None:
        return 1

    d, h = result["direct_chunk"], result["hypothetical_question"]
    print(f"\nRAG retrieval comparison ({result['query_count']} labelled queries, k={args.k})")
    print("=" * 60)
    print(f"{'strategy':<24} {'HR@1':>7} {'HR@'+str(args.k):>7} {'MRR@'+str(args.k):>8}")
    print("-" * 60)
    print(f"{'direct-chunk':<24} {d['hr@1']:>7.3f} {d['hr@'+str(args.k)]:>7.3f} {d['mrr@'+str(args.k)]:>8.3f}")
    print(f"{'hypothetical-question':<24} {h['hr@1']:>7.3f} {h['hr@'+str(args.k)]:>7.3f} {h['mrr@'+str(args.k)]:>8.3f}")
    print("=" * 60)

    if args.out:
        import pathlib
        p = pathlib.Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w") as f:
            json.dump(result, f, indent=2)
        print(f"Wrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
