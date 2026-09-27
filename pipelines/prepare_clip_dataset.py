#!/usr/bin/env python3
"""
Prepare Vision-Language Model (VLM) Dataset for Contrastive Fine-Tuning.

Extracts verified (image, text) pairs from Toko Marcell catalog:
- Validates local cached image existence and integrity (> 500 bytes, readable JPEG)
- Builds rich descriptive fashion text (Brand, Title, Category, Department, Key Fabric Features)
- Splits into deterministic 80% Train, 10% Validation, 10% Test partitions (Seed 42)
- Exports clip_train.json, clip_val.json, clip_test.json and summary statistics

Run:
  /home/marcell/miniconda3/envs/deep-learning/bin/python manual/pipelines/prepare_clip_dataset.py
"""

import json
import os
import random
import sys
import time
from pathlib import Path
from PIL import Image

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data" / "processed"
PRODUCTS_FILE = DATA_DIR / "products.jsonl"
IMAGES_DIR = DATA_DIR / "images"

TRAIN_SPLIT = 0.80
VAL_SPLIT = 0.10
TEST_SPLIT = 0.10
RANDOM_SEED = 42


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def clean_text(s: str) -> str:
    if not s:
        return ""
    return " ".join(s.split()).strip()


def build_fashion_caption(prod: dict) -> str:
    brand = clean_text(prod.get("brand", ""))
    title = clean_text(prod.get("title", ""))
    cat = clean_text(prod.get("category", ""))
    dept = clean_text(prod.get("department", ""))
    features = prod.get("features") or []

    # Clean first 2 feature bullets (typically composition and fit)
    feat_snippets = []
    for f in features[:2]:
        cleaned = clean_text(f)
        if cleaned and len(cleaned) < 120:
            feat_snippets.append(cleaned)

    parts = []
    if brand and not title.lower().startswith(brand.lower()):
        parts.append(f"{brand} -")
    parts.append(title)

    meta_parts = []
    if cat:
        meta_parts.append(cat)
    if dept and dept != "All":
        meta_parts.append(dept)
    if meta_parts:
        parts.append(f"({' / '.join(meta_parts)})")

    if feat_snippets:
        parts.append(f"— {'; '.join(feat_snippets)}")

    return " ".join(parts).strip()


def main():
    log("=== Starting VLM Dataset Preparation ===")
    if not PRODUCTS_FILE.exists():
        log(f"Error: {PRODUCTS_FILE} does not exist.")
        sys.exit(1)

    if not IMAGES_DIR.exists():
        log(f"Error: {IMAGES_DIR} does not exist.")
        sys.exit(1)

    valid_pairs = []
    skipped_no_img = 0
    skipped_corrupt = 0
    categories_count = {}

    log(f"Reading catalog from {PRODUCTS_FILE}...")
    with open(PRODUCTS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            prod = json.loads(line)
            asin = prod.get("asin")
            if not asin:
                continue

            img_path = IMAGES_DIR / f"{asin}.jpg"
            if not img_path.exists() or img_path.stat().st_size < 500:
                skipped_no_img += 1
                continue

            # Verify image can be decoded
            try:
                with Image.open(img_path) as im:
                    im.verify()
            except Exception:
                skipped_corrupt += 1
                continue

            caption = build_fashion_caption(prod)
            cat = prod.get("category", "General Fashion")
            dept = prod.get("department", "Unisex")

            categories_count[cat] = categories_count.get(cat, 0) + 1

            valid_pairs.append({
                "id": prod.get("id"),
                "asin": asin,
                "title": prod.get("title", ""),
                "brand": prod.get("brand", ""),
                "category": cat,
                "department": dept,
                "price_idr": prod.get("priceIdr", 0),
                "image_path": str(img_path),
                "relative_image_path": f"images/{asin}.jpg",
                "caption": caption,
            })

    total = len(valid_pairs)
    log(f"Total valid image-text pairs: {total}")
    log(f"Skipped missing image: {skipped_no_img}")
    log(f"Skipped corrupt image: {skipped_corrupt}")

    # Deterministic split
    random.seed(RANDOM_SEED)
    random.shuffle(valid_pairs)

    n_train = int(total * TRAIN_SPLIT)
    n_val = int(total * VAL_SPLIT)
    n_test = total - n_train - n_val

    train_data = valid_pairs[:n_train]
    val_data = valid_pairs[n_train : n_train + n_val]
    test_data = valid_pairs[n_train + n_val :]

    log(f"Splits: Train={len(train_data)} ({TRAIN_SPLIT*100:.0f}%), "
        f"Val={len(val_data)} ({VAL_SPLIT*100:.0f}%), "
        f"Test={len(test_data)} ({TEST_SPLIT*100:.0f}%)")

    train_file = DATA_DIR / "clip_train.json"
    val_file = DATA_DIR / "clip_val.json"
    test_file = DATA_DIR / "clip_test.json"
    summary_file = DATA_DIR / "clip_dataset_summary.json"

    with open(train_file, "w", encoding="utf-8") as f:
        json.dump(train_data, f, indent=2, ensure_ascii=False)
    with open(val_file, "w", encoding="utf-8") as f:
        json.dump(val_data, f, indent=2, ensure_ascii=False)
    with open(test_file, "w", encoding="utf-8") as f:
        json.dump(test_data, f, indent=2, ensure_ascii=False)

    summary = {
        "total_pairs": total,
        "splits": {
            "train": len(train_data),
            "val": len(val_data),
            "test": len(test_data),
        },
        "skipped_missing_image": skipped_no_img,
        "skipped_corrupt_image": skipped_corrupt,
        "category_distribution": dict(
            sorted(categories_count.items(), key=lambda x: x[1], reverse=True)[:15]
        ),
        "sample_pair": train_data[0] if train_data else None,
    }

    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    log(f"Saved train dataset: {train_file}")
    log(f"Saved val dataset: {val_file}")
    log(f"Saved test dataset: {test_file}")
    log(f"Saved summary: {summary_file}")
    log("=== VLM Dataset Preparation Complete ===")


if __name__ == "__main__":
    main()
