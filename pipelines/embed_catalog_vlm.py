#!/usr/bin/env python3
"""
Compute 512-dimensional multimodal vision embeddings for Toko Marcell catalog items
using the fine-tuned Champion CLIP model (Decoupled LR).

This script:
1. Loads the fine-tuned CLIP checkpoint from `manual/pipelines/Best Model`
2. Batches all product images from `manual/data/processed/images/`
3. Generates L2-normalized 512-dim embeddings on GPU (CUDA) or CPU
4. Updates PostgreSQL `products.image_embedding` column & builds HNSW cosine index
5. Saves standalone `catalog_embeddings.npy` + `catalog_items.json` for offline search

Usage:
  python manual/pipelines/embed_catalog_vlm.py
  python manual/pipelines/embed_catalog_vlm.py --skip-db  # only generate .npy / .json
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

DEFAULT_DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://toko:toko@localhost:5432/toko"
)
BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_MODEL_DIR = Path(__file__).resolve().parent / "Best Model"
DEFAULT_IMAGES_DIR = BASE_DIR / "data" / "processed" / "images"
DEFAULT_PRODUCTS_FILE = BASE_DIR / "data" / "processed" / "products.jsonl"
DEFAULT_OUTPUT_NPY = BASE_DIR / "data" / "processed" / "catalog_embeddings.npy"
DEFAULT_OUTPUT_JSON = BASE_DIR / "data" / "processed" / "catalog_items.json"


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def format_pg_vector(vec: np.ndarray) -> str:
    """Format numpy array into Postgres vector literal: '[0.0123, -0.456, ...]'."""
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def load_products(products_file: Path) -> List[Dict[str, Any]]:
    """Loads products from products.jsonl."""
    if not products_file.exists():
        raise FileNotFoundError(f"Products file not found: {products_file}")
    
    items = []
    with open(products_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def main():
    parser = argparse.ArgumentParser(description="Embed Catalog with Champion VLM Model")
    parser.add_argument("--model-dir", type=str, default=str(DEFAULT_MODEL_DIR), help="Path to fine-tuned model checkpoint")
    parser.add_argument("--images-dir", type=str, default=str(DEFAULT_IMAGES_DIR), help="Path to cached images directory")
    parser.add_argument("--products-file", type=str, default=str(DEFAULT_PRODUCTS_FILE), help="Path to products.jsonl")
    parser.add_argument("--database-url", type=str, default=DEFAULT_DATABASE_URL, help="Postgres DB URL")
    parser.add_argument("--batch-size", type=int, default=64, help="Inference batch size")
    parser.add_argument("--output-npy", type=str, default=str(DEFAULT_OUTPUT_NPY), help="Path to save catalog_embeddings.npy")
    parser.add_argument("--output-json", type=str, default=str(DEFAULT_OUTPUT_JSON), help="Path to save catalog_items.json")
    parser.add_argument("--skip-db", action="store_true", help="Skip writing to PostgreSQL database")
    args = parser.parse_args()

    t_start = time.perf_counter()
    model_dir = Path(args.model_dir)
    images_dir = Path(args.images_dir)
    products_file = Path(args.products_file)

    if not model_dir.exists():
        fallback_model = BASE_DIR / "models" / "best_champion_model"
        if fallback_model.exists():
            model_dir = fallback_model
        else:
            raise FileNotFoundError(f"Champion model directory not found at: {args.model_dir}")

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    log(f"Loading Champion VLM Model from: {model_dir} on device: {device}...")
    
    model = CLIPModel.from_pretrained(str(model_dir)).to(device)
    processor = CLIPProcessor.from_pretrained(str(model_dir))
    model.eval()
    log(f"Model successfully loaded! Projection dim: {model.config.projection_dim}")

    # Load product catalog
    log(f"Loading products from {products_file}...")
    products = load_products(products_file)
    log(f"Found {len(products):,} products in catalog.")

    # Match products with local cached images
    valid_targets = []
    for item in products:
        asin = item.get("asin")
        if not asin:
            continue
        img_path = images_dir / f"{asin}.jpg"
        if img_path.exists() and img_path.stat().st_size > 500:
            valid_targets.append({
                "id": item.get("id"),
                "asin": asin,
                "title": item.get("title", ""),
                "category": item.get("category", ""),
                "department": item.get("department", ""),
                "priceIdr": item.get("priceIdr", 0),
                "image_path": img_path
            })

    total_valid = len(valid_targets)
    log(f"Matched {total_valid:,} / {len(products):,} products with verified local images.")
    if total_valid == 0:
        log("No valid images found to embed! Exiting.")
        return 1

    # Vector inference in batches
    log(f"Computing embeddings in batches of {args.batch_size} on {device}...")
    all_embeddings = []
    db_updates: List[Tuple[str, int]] = []
    t_embed = time.perf_counter()

    with torch.no_grad():
        for i in range(0, total_valid, args.batch_size):
            batch = valid_targets[i : i + args.batch_size]
            pil_images = []
            for item in batch:
                try:
                    img = Image.open(item["image_path"]).convert("RGB")
                except Exception:
                    img = Image.new("RGB", (224, 224), color=(240, 240, 240))
                pil_images.append(img)

            inputs = processor(images=pil_images, return_tensors="pt").to(device)
            out = model.get_image_features(**inputs)
            feats = getattr(out, "pooler_output", out)
            norm_feats = feats / feats.norm(dim=-1, keepdim=True)
            batch_vecs = norm_feats.cpu().numpy().astype(np.float32)

            all_embeddings.append(batch_vecs)

            for item, vec in zip(batch, batch_vecs):
                if item["id"] is not None:
                    db_updates.append((format_pg_vector(vec), item["id"]))

            done_count = min(i + args.batch_size, total_valid)
            if done_count % 512 == 0 or done_count == total_valid:
                elapsed = time.perf_counter() - t_embed
                rate = done_count / elapsed if elapsed > 0 else 0
                log(f"  Processed {done_count:,}/{total_valid:,} images ({rate:.1f} img/s)...")

    # Stack full embedding matrix
    full_matrix = np.vstack(all_embeddings)
    log(f"Embedding matrix shape: {full_matrix.shape}, dtype: {full_matrix.dtype}")

    # 1. Save Standalone NumPy & JSON Cache
    out_npy_path = Path(args.output_npy)
    out_npy_path.parent.mkdir(parents=True, exist_ok=True)
    np.save(str(out_npy_path), full_matrix)
    log(f"Saved NumPy embedding matrix to: {out_npy_path} ({out_npy_path.stat().st_size / 1e6:.1f} MB)")

    out_json_path = Path(args.output_json)
    catalog_metadata = [
        {
            "index": idx,
            "id": t["id"],
            "asin": t["asin"],
            "title": t["title"],
            "category": t["category"],
            "department": t["department"],
            "priceIdr": t["priceIdr"],
        }
        for idx, t in enumerate(valid_targets)
    ]
    with open(out_json_path, "w", encoding="utf-8") as f:
        json.dump(catalog_metadata, f, indent=2)
    log(f"Saved catalog items metadata to: {out_json_path}")

    # 2. Update PostgreSQL (if enabled and connected)
    if not args.skip_db:
        log(f"Attempting to update PostgreSQL database at: {args.database_url}...")
        try:
            import psycopg
            with psycopg.connect(args.database_url, connect_timeout=3) as conn:
                with conn.cursor() as cur:
                    log("Ensuring `image_embedding vector(512)` column exists...")
                    cur.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS image_embedding vector(512);")
                    conn.commit()

                    log(f"Updating {len(db_updates):,} product rows with new champion embeddings...")
                    t_db = time.perf_counter()
                    cur.executemany(
                        "UPDATE products SET image_embedding = %s::vector WHERE id = %s",
                        db_updates,
                    )
                    conn.commit()
                    log(f"Database rows updated in {time.perf_counter() - t_db:.2f}s.")

                    log("Rebuilding HNSW cosine index `products_image_embedding_idx`...")
                    t_idx = time.perf_counter()
                    cur.execute(
                        """
                        CREATE INDEX IF NOT EXISTS products_image_embedding_idx
                        ON products USING hnsw (image_embedding vector_cosine_ops);
                        """
                    )
                    conn.commit()
                    log(f"HNSW index built/verified in {time.perf_counter() - t_idx:.2f}s.")

        except Exception as e:
            log(f"PostgreSQL update skipped or failed ({e}). (Offline .npy matrix is ready regardless!)")
    else:
        log("Skipping database update as requested (--skip-db).")

    total_time = time.perf_counter() - t_start
    log(f"✨ ALL DONE! Successfully processed {total_valid:,} catalog items in {total_time:.1f}s.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
