#!/usr/bin/env python3
"""
Rigorous Comparative Multimodal Evaluation: Zero-Shot Base CLIP vs Fine-Tuned Fashion CLIP.

Target Alignment: AI Engineer (Multimodal AI / LLM) — PT. Kalbe Farma, Tbk.

Evaluates on held-out test split (manual/data/processed/clip_test.json, 539 items):
- Text-to-Image Recall@1, Recall@5, Recall@10
- Mean Reciprocal Rank (MRR)
- Average Retrieval Latency (ms)
- Automated Discovery of Top Rank Improvements & Error Cases for Failure Analysis

Run:
  /home/marcell/miniconda3/envs/deep-learning/bin/python manual/pipelines/eval_multimodal.py
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import List, Dict, Any, Tuple

import numpy as np
import torch
from PIL import Image
from transformers import CLIPModel, CLIPProcessor

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = BASE_DIR / "data" / "processed"
DEFAULT_FINE_TUNED_DIR = BASE_DIR / "models" / "fashion_clip"
DEFAULT_BASE_MODEL = "openai/clip-vit-base-patch32"


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def extract_features_batched(
    model: CLIPModel,
    processor: CLIPProcessor,
    test_items: List[Dict[str, Any]],
    batch_size: int,
    device: torch.device,
) -> Tuple[np.ndarray, np.ndarray, float]:
    """
    Batched inference extracting unit-normalized 512-dim visual and text embeddings.
    Returns:
      (image_embeddings: [N, 512], text_embeddings: [N, 512], avg_query_ms: float)
    """
    model.eval()
    all_image_embeds = []
    all_text_embeds = []

    t_start = time.perf_counter()

    with torch.no_grad():
        for i in range(0, len(test_items), batch_size):
            batch = test_items[i : i + batch_size]

            images = []
            captions = []
            for item in batch:
                try:
                    img = Image.open(item["image_path"]).convert("RGB")
                except Exception:
                    img = Image.new("RGB", (224, 224), (240, 240, 240))
                images.append(img)
                captions.append(item.get("caption") or item.get("title") or "fashion")

            inputs = processor(
                text=captions,
                images=images,
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=64,
            ).to(device)

            # Vision forward
            img_out = model.get_image_features(pixel_values=inputs["pixel_values"])
            img_feats = img_out.pooler_output if hasattr(img_out, "pooler_output") else img_out
            img_feats = img_feats / (img_feats.norm(dim=-1, keepdim=True) + 1e-8)

            # Text forward
            txt_out = model.get_text_features(
                input_ids=inputs["input_ids"], attention_mask=inputs["attention_mask"]
            )
            txt_feats = txt_out.pooler_output if hasattr(txt_out, "pooler_output") else txt_out
            txt_feats = txt_feats / (txt_feats.norm(dim=-1, keepdim=True) + 1e-8)

            all_image_embeds.append(img_feats.cpu().numpy())
            all_text_embeds.append(txt_feats.cpu().numpy())

    total_time = time.perf_counter() - t_start
    avg_ms = (total_time / len(test_items)) * 1000

    img_mat = np.vstack(all_image_embeds)
    txt_mat = np.vstack(all_text_embeds)

    return img_mat, txt_mat, avg_ms


def compute_retrieval_metrics(
    img_mat: np.ndarray, txt_mat: np.ndarray
) -> Tuple[Dict[str, float], List[int]]:
    """
    Computes Recall@1, 5, 10 and MRR for all-to-all cross-modal retrieval.
    Matrix: N text queries against N candidate images (Ground Truth is diagonal i == j).
    """
    # Cosine similarity matrix [N_text, N_images]
    sim_matrix = txt_mat @ img_mat.T
    n = len(sim_matrix)

    ranks = []
    r1 = 0
    r5 = 0
    r10 = 0
    rr_sum = 0.0

    for i in range(n):
        scores = sim_matrix[i]
        # Rank of ground truth candidate (item i)
        # Order descending
        sorted_indices = np.argsort(-scores)
        # 1-indexed rank
        rank = int(np.where(sorted_indices == i)[0][0]) + 1
        ranks.append(rank)

        if rank == 1:
            r1 += 1
        if rank <= 5:
            r5 += 1
        if rank <= 10:
            r10 += 1
        rr_sum += 1.0 / rank

    metrics = {
        "recall_at_1": round(r1 / n, 4),
        "recall_at_5": round(r5 / n, 4),
        "recall_at_10": round(r10 / n, 4),
        "mrr": round(rr_sum / n, 4),
        "mean_rank": round(float(np.mean(ranks)), 2),
        "median_rank": int(np.median(ranks)),
    }
    return metrics, ranks


def main():
    parser = argparse.ArgumentParser(description="Evaluate VLM Multimodal Retrieval")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--base-model", default=DEFAULT_BASE_MODEL)
    parser.add_argument("--fine-tuned-model", default=str(DEFAULT_FINE_TUNED_DIR))
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    parser.add_argument("--limit", type=int, default=0, help="Optional test set truncation")
    args = parser.parse_args()

    device = torch.device(args.device)
    test_file = Path(args.data_dir) / "clip_test.json"

    if not test_file.exists():
        log(f"Error: {test_file} not found. Run prepare_clip_dataset.py first!")
        sys.exit(1)

    with open(test_file, "r", encoding="utf-8") as f:
        test_items = json.load(f)

    if args.limit > 0:
        test_items = test_items[: args.limit]

    n_test = len(test_items)
    log(f"=== Starting Multimodal Retrieval Benchmark ===")
    log(f"Held-Out Test Set: {n_test} verified image-text pairs")
    log(f"Device: {device} | Batch Size: {args.batch_size}")

    # ── 1. Evaluate Zero-Shot Base CLIP ───────────────────────────────────────
    log(f"\n[1/2] Benchmarking Zero-Shot Base CLIP ({args.base_model})...")
    base_processor = CLIPProcessor.from_pretrained(args.base_model)
    base_model = CLIPModel.from_pretrained(args.base_model, use_safetensors=True).to(device)

    base_imgs, base_txts, base_lat = extract_features_batched(
        base_model, base_processor, test_items, args.batch_size, device
    )
    base_metrics, base_ranks = compute_retrieval_metrics(base_imgs, base_txts)
    base_metrics["latency_ms_per_item"] = round(base_lat, 2)
    del base_model
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    log(f"Base CLIP: Recall@1={base_metrics['recall_at_1']*100:.1f}%, "
        f"Recall@5={base_metrics['recall_at_5']*100:.1f}%, "
        f"MRR={base_metrics['mrr']:.4f}")

    # ── 2. Evaluate Fine-Tuned Fashion CLIP ───────────────────────────────────
    fine_tuned_path = Path(args.fine_tuned_model)
    if not fine_tuned_path.exists():
        log(f"Warning: Fine-tuned model directory {fine_tuned_path} not found.")
        log("Skipping fine-tuned evaluation. Run train_clip.py to train the model first.")
        ft_metrics = {
            "recall_at_1": 0.0,
            "recall_at_5": 0.0,
            "recall_at_10": 0.0,
            "mrr": 0.0,
            "mean_rank": 0.0,
            "median_rank": 0,
            "latency_ms_per_item": 0.0,
        }
        ft_ranks = [0] * n_test
    else:
        log(f"\n[2/2] Benchmarking Fine-Tuned Fashion CLIP ({fine_tuned_path})...")
        ft_processor = CLIPProcessor.from_pretrained(str(fine_tuned_path))
        ft_model = CLIPModel.from_pretrained(str(fine_tuned_path), use_safetensors=True).to(device)

        ft_imgs, ft_txts, ft_lat = extract_features_batched(
            ft_model, ft_processor, test_items, args.batch_size, device
        )
        ft_metrics, ft_ranks = compute_retrieval_metrics(ft_imgs, ft_txts)
        ft_metrics["latency_ms_per_item"] = round(ft_lat, 2)
        del ft_model
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        log(f"Fine-Tuned Fashion CLIP: Recall@1={ft_metrics['recall_at_1']*100:.1f}%, "
            f"Recall@5={ft_metrics['recall_at_5']*100:.1f}%, "
            f"MRR={ft_metrics['mrr']:.4f}")

    # ── 3. Failure Case & Improvement Analysis ────────────────────────────────
    improvements = []
    regressions = []

    for idx, (b_rank, f_rank) in enumerate(zip(base_ranks, ft_ranks)):
        item = test_items[idx]
        delta = b_rank - f_rank  # positive means fine-tuned ranked higher
        record = {
            "asin": item["asin"],
            "title": item["title"],
            "brand": item["brand"],
            "category": item["category"],
            "caption": item["caption"],
            "base_rank": b_rank,
            "fine_tuned_rank": f_rank,
            "rank_delta": delta,
        }
        if delta > 0:
            improvements.append(record)
        elif delta < 0:
            regressions.append(record)

    improvements.sort(key=lambda x: x["rank_delta"], reverse=True)
    regressions.sort(key=lambda x: x["rank_delta"])

    # ── 4. Print Comparison Markdown Table ────────────────────────────────────
    print("\n" + "=" * 76)
    print("        MULTIMODAL VLM BENCHMARK: TEXT-TO-IMAGE RETRIEVAL (N=539)       ")
    print("=" * 76)
    print(f"{'Metric':<24} | {'Zero-Shot Base CLIP':<18} | {'Fine-Tuned Fashion':<18} | {'Delta':<10}")
    print("-" * 76)

    def fmt_delta(ft_val, base_val, is_pct=True, is_inv=False):
        d = ft_val - base_val
        if is_inv:
            d = base_val - ft_val
        sign = "+" if d >= 0 else ""
        if is_pct:
            return f"{sign}{d*100:.1f} pp"
        return f"{sign}{d:.2f}"

    r1_d = fmt_delta(ft_metrics["recall_at_1"], base_metrics["recall_at_1"], is_pct=True)
    r5_d = fmt_delta(ft_metrics["recall_at_5"], base_metrics["recall_at_5"], is_pct=True)
    r10_d = fmt_delta(ft_metrics["recall_at_10"], base_metrics["recall_at_10"], is_pct=True)
    mrr_d = fmt_delta(ft_metrics["mrr"], base_metrics["mrr"], is_pct=False)
    rank_d = fmt_delta(ft_metrics["mean_rank"], base_metrics["mean_rank"], is_pct=False, is_inv=True)

    print(f"{'Text-to-Image Recall@1':<24} | {base_metrics['recall_at_1']*100:>16.1f}% | {ft_metrics['recall_at_1']*100:>16.1f}% | {r1_d:>10}")
    print(f"{'Text-to-Image Recall@5':<24} | {base_metrics['recall_at_5']*100:>16.1f}% | {ft_metrics['recall_at_5']*100:>16.1f}% | {r5_d:>10}")
    print(f"{'Text-to-Image Recall@10':<24} | {base_metrics['recall_at_10']*100:>16.1f}% | {ft_metrics['recall_at_10']*100:>16.1f}% | {r10_d:>10}")
    print(f"{'Mean Reciprocal Rank':<24} | {base_metrics['mrr']:>18.4f} | {ft_metrics['mrr']:>18.4f} | {mrr_d:>10}")
    print(f"{'Mean Rank (Lower=Better)':<24} | {base_metrics['mean_rank']:>18.1f} | {ft_metrics['mean_rank']:>18.1f} | {rank_d:>10}")
    print(f"{'Latency per Query (ms)':<24} | {base_metrics['latency_ms_per_item']:>16.1f}ms | {ft_metrics['latency_ms_per_item']:>16.1f}ms | {'--':>10}")
    print("=" * 76 + "\n")

    # ── 5. Save JSON Output ───────────────────────────────────────────────────
    out_file = BASE_DIR / "pipelines" / "multimodal_benchmark_results.json"
    results_payload = {
        "test_size": n_test,
        "base_model": args.base_model,
        "fine_tuned_model": args.fine_tuned_model,
        "metrics": {
            "base_clip": base_metrics,
            "fine_tuned_fashion_clip": ft_metrics,
            "delta": {
                "recall_at_1": round(ft_metrics["recall_at_1"] - base_metrics["recall_at_1"], 4),
                "recall_at_5": round(ft_metrics["recall_at_5"] - base_metrics["recall_at_5"], 4),
                "recall_at_10": round(ft_metrics["recall_at_10"] - base_metrics["recall_at_10"], 4),
                "mrr": round(ft_metrics["mrr"] - base_metrics["mrr"], 4),
            },
        },
        "top_rank_improvements": improvements[:5],
        "top_regressions": regressions[:3],
    }

    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results_payload, f, indent=2, ensure_ascii=False)
    log(f"Saved benchmark results and failure cases to: {out_file}")


if __name__ == "__main__":
    main()
