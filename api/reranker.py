"""
Stage 2 Cross-Encoder Reranker using ONNX Runtime.

Model: ms-marco-MiniLM-L-6-v2 (quantized ONNX, ~15-25ms inference for 20 candidates).
Takes Stage 1 retrieval candidates (from BM25 + pgvector RRF) and scores (query, text) pairs
with deep cross-attention to elevate the most contextually relevant products.
"""

import os
import threading
from typing import Any

_RERANKER_SESSION = None
_RERANKER_TOKENIZER = None
_RERANKER_LOCK = threading.Lock()
_INIT_FAILED = False


def _get_reranker():
    """Lazily load ONNX model and tokenizer for ms-marco-MiniLM-L-6-v2."""
    global _RERANKER_SESSION, _RERANKER_TOKENIZER, _INIT_FAILED
    if _RERANKER_SESSION is not None:
        return _RERANKER_SESSION, _RERANKER_TOKENIZER
    if _INIT_FAILED:
        return None, None

    with _RERANKER_LOCK:
        if _RERANKER_SESSION is not None:
            return _RERANKER_SESSION, _RERANKER_TOKENIZER
        if _INIT_FAILED:
            return None, None

        try:
            from huggingface_hub import hf_hub_download
            import onnxruntime as ort
            from tokenizers import Tokenizer

            cache_dir = os.environ.get("HF_HUB_CACHE", "/tmp/hf_cache")
            model_path = hf_hub_download(
                "Xenova/ms-marco-MiniLM-L-6-v2",
                "onnx/model.onnx",
                cache_dir=cache_dir,
            )
            tokenizer_path = hf_hub_download(
                "Xenova/ms-marco-MiniLM-L-6-v2",
                "tokenizer.json",
                cache_dir=cache_dir,
            )

            tokenizer = Tokenizer.from_file(tokenizer_path)
            tokenizer.enable_truncation(max_length=256)

            # Opt for low threading in containers
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = 2
            opts.inter_op_num_threads = 1
            session = ort.InferenceSession(model_path, sess_options=opts)

            _RERANKER_SESSION = session
            _RERANKER_TOKENIZER = tokenizer
            return _RERANKER_SESSION, _RERANKER_TOKENIZER
        except Exception as e:
            print(f"[reranker] Warning: Failed to load ONNX cross-encoder: {e}. Falling back to baseline rank.")
            _INIT_FAILED = True
            return None, None


def rerank(
    query: str,
    candidates: list[dict[str, Any]],
    text_key: str = "title",
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Rerank candidate items using the cross-encoder.

    Args:
        query: The user query string
        candidates: List of candidate dicts (e.g. products or reviews)
        text_key: Key in candidate dict to score against (or callable)
        limit: Max number of top candidates to return

    Returns:
        List of candidate dicts, sorted by cross-encoder score descending.
    """
    if not candidates:
        return []
    if len(candidates) <= 1:
        return candidates[:limit]

    if os.environ.get("ENABLE_RERANKER", "true").strip().lower() not in ("1", "true", "yes"):
        # Cross-encoder disabled on memory-constrained deployments: keep the Stage-1 order.
        # Only the copilot's search_catalog uses this; the /search endpoint never did.
        return candidates[:limit]

    session, tokenizer = _get_reranker()
    if session is None or tokenizer is None:
        return candidates[:limit]

    try:
        import numpy as np

        doc_texts = []
        for c in candidates:
            if callable(text_key):
                doc_texts.append(text_key(c))
            elif isinstance(c.get(text_key), str):
                base_text = c[text_key]
                brand = c.get("brand") or ""
                dept = c.get("department") or ""
                cat = c.get("category") or ""
                doc_texts.append(f"{brand} {base_text} - {dept} {cat}".strip())
            else:
                doc_texts.append(str(c.get(text_key, "")))

        pairs = [(query, text) for text in doc_texts]

        tokenizer.no_padding()
        encoded = tokenizer.encode_batch(pairs)
        max_len = max((len(e.ids) for e in encoded), default=16)

        input_ids = np.array([e.ids + [0] * (max_len - len(e.ids)) for e in encoded], dtype=np.int64)
        attention_mask = np.array([e.attention_mask + [0] * (max_len - len(e.attention_mask)) for e in encoded], dtype=np.int64)
        token_type_ids = np.array([e.type_ids + [0] * (max_len - len(e.type_ids)) for e in encoded], dtype=np.int64)

        outputs = session.run(
            None,
            {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
                "token_type_ids": token_type_ids,
            },
        )
        scores = outputs[0].flatten().tolist()

        scored_candidates = []
        for cand, score in zip(candidates, scores):
            cand_copy = dict(cand)
            cand_copy["cross_encoder_score"] = float(score)
            scored_candidates.append(cand_copy)

        scored_candidates.sort(key=lambda x: x["cross_encoder_score"], reverse=True)
        return scored_candidates[:limit]
    except Exception as e:
        print(f"[reranker] Error during cross-encoder inference: {e}")
        return candidates[:limit]
