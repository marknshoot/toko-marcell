#!/usr/bin/env python3
"""
Prefetch and cache catalog product images from Amazon CDN to data/processed/images/.
"""

import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import psycopg

DEFAULT_DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://toko:toko@localhost:5432/toko"
)
PROCESSED_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"
IMAGE_CACHE_DIR = PROCESSED_DIR / "images"
WORKERS = 40


def download_one(row):
    pid, asin, url = row
    dest = IMAGE_CACHE_DIR / f"{asin}.jpg"
    if dest.exists() and dest.stat().st_size > 500:
        return True
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
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = resp.read()
            if len(data) > 500:
                dest.write_bytes(data)
                return True
    except Exception:
        pass
    return False


def main():
    IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Connecting to database to get image URLs...")
    with psycopg.connect(DEFAULT_DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, asin, image_url FROM products WHERE image_url IS NOT NULL")
            rows = cur.fetchall()

    print(f"Total catalog products with image_url: {len(rows):,}")
    t0 = time.perf_counter()
    success = 0
    done = 0

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(download_one, row) for row in rows]
        for fut in as_completed(futures):
            done += 1
            if fut.result():
                success += 1
            if done % 1000 == 0 or done == len(rows):
                elapsed = time.perf_counter() - t0
                rate = done / elapsed if elapsed > 0 else 0
                print(f"  Processed {done:,}/{len(rows):,} ({success:,} valid images, {rate:.1f} img/s)...", flush=True)

    total_time = time.perf_counter() - t0
    print(f"Finished downloading {success:,} images in {total_time:.1f}s!", flush=True)


if __name__ == "__main__":
    main()
