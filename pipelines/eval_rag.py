#!/usr/bin/env python3
"""
RAGAS-based retrieval evaluation harness for Toko Marcell Store Knowledge RAG.

Compares three store-policy retrieval strategies on bilingual (Indonesian + English)
shopper queries using standard RAGAS context retrieval metrics:
  1. direct_chunk          — dense vector similarity over store_knowledge chunks
  2. hypothetical_question — dense vector similarity over store_knowledge_questions
  3. hybrid_rrf            — RRF fusion of hypothetical questions + direct chunk + lexical keywords

Metrics (via ragas.evaluate):
  - id_based_context_precision (Mean Average Precision @ k over chunk IDs)
  - id_based_context_recall    (Recall @ k over chunk IDs)
  - non_llm_context_precision_with_reference
  - non_llm_context_recall

Run:
  python3 pipelines/eval_rag.py --out pipelines/results/rag_eval.json
"""

import argparse
import datetime
import json
import os
import pathlib
import subprocess
import sys
import types
import warnings

import numpy as np

warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

ROOT_DIR = pathlib.Path(__file__).resolve().parent.parent
API_DIR = ROOT_DIR / "api"
sys.path.insert(0, str(API_DIR))

# Compatibility shim for ragas 0.4.x with langchain-community >= 0.4 / 1.x
for _mod_name, _cls_name in [
    ("langchain_community.chat_models.vertexai", "ChatVertexAI"),
    ("langchain_community.llms", "VertexAI"),
]:
    _mod = sys.modules.get(_mod_name) or types.ModuleType(_mod_name)
    if not hasattr(_mod, _cls_name):
        setattr(_mod, _cls_name, type(_cls_name, (), {}))
    sys.modules[_mod_name] = _mod

from agent_tools import score_policy_chunks_lexical  # noqa: E402
from knowledge_seed import _chunk_markdown, _hypothetical_questions  # noqa: E402
from ragas import EvaluationDataset, SingleTurnSample, evaluate  # noqa: E402
from ragas.metrics import (  # noqa: E402
    IDBasedContextPrecision,
    IDBasedContextRecall,
    NonLLMContextPrecisionWithReference,
    NonLLMContextRecall,
)
from search import reciprocal_rank_fusion  # noqa: E402

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

DEFAULT_MODEL = os.environ.get(
    "TEXT_EMBED_MODEL",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
)


def _git_commit() -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _cosine_rank(qvec: np.ndarray, matrix: np.ndarray, ids: list[int], k: int) -> list[tuple[int, float]]:
    sims = matrix @ qvec
    order = np.argsort(-sims)
    out, seen = [], set()
    for idx in order:
        cid = ids[int(idx)]
        if cid in seen:
            continue
        seen.add(cid)
        out.append((cid, float(sims[int(idx)])))
        if len(out) >= k:
            break
    return out


def build_index(model):
    knowledge_dir = API_DIR / "knowledge"
    chunks = (
        _chunk_markdown(str(knowledge_dir / "size_charts.md"), "sizing")
        + _chunk_markdown(str(knowledge_dir / "store_policies.md"), "policy")
    )
    for i, c in enumerate(chunks, 1):
        c["id"] = i

    chunk_texts = [f"{c['title']}\n{c['content'][:500]}" for c in chunks]
    chunk_vecs = np.asarray(list(model.embed(chunk_texts)), dtype=np.float32)
    chunk_vecs /= np.linalg.norm(chunk_vecs, axis=1, keepdims=True) + 1e-12

    q_texts, q_cids = [], []
    for c in chunks:
        for q, _lang in _hypothetical_questions(c):
            q_texts.append(q)
            q_cids.append(c["id"])
    q_vecs = np.asarray(list(model.embed(q_texts)), dtype=np.float32)
    q_vecs /= np.linalg.norm(q_vecs, axis=1, keepdims=True) + 1e-12

    return chunks, chunk_vecs, q_vecs, q_cids


def run_ragas_eval(model_name: str = DEFAULT_MODEL, k: int = 3) -> dict:
    from fastembed import TextEmbedding

    cache_dir = os.environ.get("FASTEMBED_CACHE_DIR", "/tmp/fastembed_cache")
    model = TextEmbedding(model_name=model_name, cache_dir=cache_dir)
    chunks, chunk_vecs, q_vecs, q_cids = build_index(model)
    by_id = {c["id"]: c for c in chunks}
    chunk_ids = [c["id"] for c in chunks]
    lexical_tuples = [(c["id"], c["category"], c["title"], c["content"]) for c in chunks]

    samples_by_strategy = {"direct_chunk": [], "hypothetical_question": [], "hybrid_rrf": []}
    per_query = []

    query_vecs = np.asarray(list(model.embed([q for q, _ in LABELLED])), dtype=np.float32)
    query_vecs /= np.linalg.norm(query_vecs, axis=1, keepdims=True) + 1e-12

    for (query, expected_sub), qvec in zip(LABELLED, query_vecs, strict=False):
        ref_chunk = next(c for c in chunks if expected_sub.lower() in c["title"].lower())
        direct_ranked = _cosine_rank(qvec, chunk_vecs, chunk_ids, len(chunks))
        hyp_ranked = _cosine_rank(qvec, q_vecs, q_cids, len(chunks))
        lex_ranked = score_policy_chunks_lexical(query, lexical_tuples)
        hybrid_ranked = reciprocal_rank_fusion(
            [r for r in (hyp_ranked, direct_ranked, lex_ranked) if r], k=60
        )

        ranked_lists = {
            "direct_chunk": [cid for cid, _ in direct_ranked[:k]],
            "hypothetical_question": [cid for cid, _ in hyp_ranked[:k]],
            "hybrid_rrf": [cid for cid, _ in hybrid_ranked[:k]],
        }

        for strat, top_ids in ranked_lists.items():
            samples_by_strategy[strat].append(
                SingleTurnSample(
                    user_input=query,
                    retrieved_contexts=[by_id[cid]["content"] for cid in top_ids],
                    reference_contexts=[ref_chunk["content"]],
                    retrieved_context_ids=[str(cid) for cid in top_ids],
                    reference_context_ids=[str(ref_chunk["id"])],
                )
            )

        per_query.append({
            "query": query,
            "expected": expected_sub,
            "reference_chunk_id": ref_chunk["id"],
            "direct_top_ids": ranked_lists["direct_chunk"],
            "hyp_top_ids": ranked_lists["hypothetical_question"],
            "hybrid_top_ids": ranked_lists["hybrid_rrf"],
        })

    metrics = [
        IDBasedContextPrecision(),
        IDBasedContextRecall(),
        NonLLMContextPrecisionWithReference(),
        NonLLMContextRecall(),
    ]

    summaries = {}
    for strat, samples in samples_by_strategy.items():
        res = evaluate(
            dataset=EvaluationDataset(samples=samples),
            metrics=metrics,
            show_progress=False,
        )
        scores = res.scores
        summaries[strat] = {
            m_key: round(float(np.mean([row[m_key] for row in scores])), 4)
            for m_key in scores[0]
        }

    return {
        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
        "git_commit": _git_commit(),
        "framework": "ragas",
        "embedding_model": model_name,
        "k": k,
        "query_count": len(LABELLED),
        "strategies": summaries,
        "direct_chunk": summaries["direct_chunk"],
        "hypothetical_question": summaries["hypothetical_question"],
        "hybrid_rrf": summaries["hybrid_rrf"],
        "per_query": per_query,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="RAGAS evaluation for Toko Marcell Store Policy RAG")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    result = run_ragas_eval(model_name=args.model, k=args.k)
    print(f"\nRAGAS Store Policy Retrieval Evaluation ({result['query_count']} bilingual queries, k={args.k})")
    print(f"Model: {result['embedding_model']}")
    print("=" * 88)
    print(f"{'Strategy':<24} {'ID Precision':>14} {'ID Recall':>12} {'Ctx Precision':>15} {'Ctx Recall':>12}")
    print("-" * 88)
    for strat, s in result["strategies"].items():
        print(
            f"{strat:<24} "
            f"{s['id_based_context_precision']:>14.4f} "
            f"{s['id_based_context_recall']:>12.4f} "
            f"{s['non_llm_context_precision_with_reference']:>15.4f} "
            f"{s['non_llm_context_recall']:>12.4f}"
        )
    print("=" * 88)

    if args.out:
        p = pathlib.Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(result, indent=2) + "\n")
        print(f"Wrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
