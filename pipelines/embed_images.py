#!/usr/bin/env python3
"""
Generate 512-dimensional multimodal vision embeddings (CLIP ViT-B/32) for catalog products
and write them to the Postgres `products.image_embedding` column with an HNSW cosine index.

Model: Qdrant/clip-ViT-B-32-vision (via fastembed ONNX runtime)
Dimension: 512
Input: Product images (downloaded and cached locally from Amazon CDN)

Run:
  python3 pipelines/embed_images.py [--limit 100]
"""

import argparse
import io
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import psycopg
from fastembed import ImageEmbedding
from PIL import Image

DEFAULT_DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://toko:toko@localhost:5432/toko"
)
BATCH_SIZE = 64
DOWNLOAD_WORKERS = 32
MODEL_NAME = "Qdrant/clip-ViT-B-32-vision"

PROCESSED_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"
IMAGE_CACHE_DIR = PROCESSED_DIR / "images"


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def format_vector(vec) -> str:
    """Format float array into Postgres vector string literal: [v1, v2, ...]"""
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def download_one_image(item: tuple[int, str, str]) -> tuple[int, str, Path | None]:
    """Downloads an image for an ASIN if not already cached."""
    prod_id, asin, url = item
    if not url:
        return prod_id, asin, None

    dest = IMAGE_CACHE_DIR / f"{asin}.jpg"
    if dest.exists() and dest.stat().st_size > 500:
        return prod_id, asin, dest

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                )
            },
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = resp.read()
            if len(data) > 500:
                dest.write_bytes(data)
                return prod_id, asin, dest
    except Exception:
        pass

    return prod_id, asin, None


def main():
    parser = argparse.ArgumentParser(description="Embed Catalog Images using CLIP")
    parser.add_argument("--database-url", default=DEFAULT_DATABASE_URL)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--download-workers", type=int, default=DOWNLOAD_WORKERS)
    parser.add_argument("--limit", type=int, default=0, help="Optional limit for dry runs")
    args = parser.parse_args()

    IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    t_start = time.perf_counter()

    log(f"Connecting to Postgres at {args.database_url}...")
    with psycopg.connect(args.database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "ALTER TABLE products ADD COLUMN IF NOT EXISTS image_embedding vector(512);"
            )
            conn.commit()

            query = """
                SELECT id, asin, image_url
                FROM products
                WHERE image_url IS NOT NULL AND image_embedding IS NULL
                ORDER BY id ASC
            """
            if args.limit > 0:
                query += f" LIMIT {args.limit}"

            cur.execute(query)
            targets = cur.fetchall()

    if not targets:
        log("No products needing image embedding found (all already embedded).")
        return 0

    log(f"Found {len(targets):,} products needing image embeddings.")

    log(f"Downloading images using {args.download_workers} parallel threads...")
    t_dl = time.perf_counter()
    downloaded_paths = {}
    completed_count = 0

    with ThreadPoolExecutor(max_workers=args.download_workers) as pool:
        futures = {pool.submit(download_one_image, item): item for item in targets}
        for fut in as_completed(futures):
            prod_id, asin, path = fut.result()
            if path:
                downloaded_paths[prod_id] = path
            completed_count += 1
            if completed_count % 1000 == 0 or completed_count == len(targets):
                log(f"  Downloaded/checked {completed_count:,}/{len(targets):,} images ({len(downloaded_paths):,} valid)...")

    log(f"Downloaded {len(downloaded_paths):,} images in {time.perf_counter() - t_dl:.1f}s.")

    log(f"Loading CLIP model {MODEL_NAME} via fastembed...")
    t_model = time.perf_counter()
    model_path = os.path.expanduser("~/.cache/fastembed/models--Qdrant--clip-ViT-B-32-vision")
    embed_model = ImageEmbedding(model_name=MODEL_NAME, specific_model_path=model_path)
    log(f"Model loaded in {time.perf_counter() - t_model:.1f}s.")

    valid_items = [(pid, asin, downloaded_paths[pid]) for pid, asin, _ in targets if pid in downloaded_paths]
    total_valid = len(valid_items)
    log(f"Encoding {total_valid:,} images in batches of {args.batch_size}...")

    total_embedded = 0
    t_embed = time.perf_counter()

    with psycopg.connect(args.database_url) as conn:
        with conn.cursor() as cur:
            for i in range(0, total_valid, args.batch_size):
                batch = valid_items[i : i + args.batch_size]
                batch_pids = [b[0] for b in batch]
                batch_paths = [str(b[2]) for b in batch]

                vecs = list(embed_model.embed(batch_paths, batch_size=len(batch)))

                updates = [(format_vector(v), pid) for pid, v in zip(batch_pids, vecs)]
                cur.executemany(
                    "UPDATE products SET image_embedding = %s::vector WHERE id = %s",
                    updates,
                )
                conn.commit()

                total_embedded += len(updates)
                elapsed = time.perf_counter() - t_embed
                rate = total_embedded / elapsed if elapsed > 0 else 0
                if total_embedded % 256 == 0 or total_embedded == total_valid:
                    log(f"  Embedded {total_embedded:,}/{total_valid:,} images ({rate:.1f} img/s)...")

            log("Building HNSW cosine index `products_image_embedding_idx` on products(image_embedding)...")
            t_idx = time.perf_counter()
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS products_image_embedding_idx
                ON products USING hnsw (image_embedding vector_cosine_ops);
                """
            )
            conn.commit()
            log(f"Index created in {time.perf_counter() - t_idx:.1f}s.")

    log(f"All done! {total_embedded:,} products embedded in {time.perf_counter() - t_start:.1f}s.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
