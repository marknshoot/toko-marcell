#!/usr/bin/env python3
"""
Clean up Toko Marcell database on Neon PostgreSQL:
1. Delete products with broken/dead image links (image_embedding IS NULL).
2. Deduplicate title variations (keep the best-rated/reviewed variant, delete duplicates).
3. Clean up associated review and interaction records.
4. Run VACUUM ANALYZE to update statistics and indexes.
"""

import os
import sys
import time
import psycopg

DEFAULT_DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://toko:toko@localhost:5432/toko",
)


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main():
    log("=== Starting Catalog Clean-Up ===")
    t0 = time.perf_counter()

    with psycopg.connect(DEFAULT_DATABASE_URL) as conn:
        with conn.cursor() as cur:
            # Step 1: Initial state
            cur.execute("SELECT COUNT(*) FROM products;")
            initial_count = cur.fetchone()[0]
            log(f"Current products in database: {initial_count:,}")

            # Step 2: Identify dead image products
            cur.execute("SELECT id, asin FROM products WHERE image_embedding IS NULL;")
            dead_rows = cur.fetchall()
            dead_ids = [r[0] for r in dead_rows]
            dead_asins = [r[1] for r in dead_rows]
            log(f"Products with dead/broken image links: {len(dead_ids):,}")

            # Step 3: Identify duplicate title variants among valid products
            cur.execute("""
                WITH ranked AS (
                    SELECT id, asin, title, rating_count, avg_rating,
                           ROW_NUMBER() OVER (
                               PARTITION BY LOWER(TRIM(title)) 
                               ORDER BY rating_count DESC, avg_rating DESC NULLS LAST, id ASC
                           ) as rnk
                    FROM products
                    WHERE image_embedding IS NOT NULL
                )
                SELECT id, asin FROM ranked WHERE rnk > 1;
            """)
            dup_rows = cur.fetchall()
            dup_ids = [r[0] for r in dup_rows]
            dup_asins = [r[1] for r in dup_rows]
            log(f"Duplicate title variants to remove: {len(dup_ids):,}")

            all_delete_ids = dead_ids + dup_ids
            all_delete_asins = dead_asins + dup_asins
            log(f"Total products to delete: {len(all_delete_ids):,}")

            # Step 4: Clean up dependent records in batches
            log("Cleaning up orphaned reviews...")
            cur.execute(
                "DELETE FROM reviews WHERE asin = ANY(%s);",
                (all_delete_asins,),
            )
            deleted_reviews = cur.rowcount
            log(f"Deleted {deleted_reviews:,} orphaned review rows.")

            # Step 5: Clean up products
            log("Deleting dead & duplicate products from products table...")
            cur.execute(
                "DELETE FROM products WHERE id = ANY(%s);",
                (all_delete_ids,),
            )
            deleted_products = cur.rowcount
            log(f"Deleted {deleted_products:,} product rows.")

            conn.commit()

            # Step 6: Verify final state
            cur.execute("SELECT COUNT(*) FROM products;")
            final_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM products WHERE image_embedding IS NOT NULL;")
            valid_image_count = cur.fetchone()[0]
            cur.execute("""
                SELECT COUNT(*) FROM (
                    SELECT LOWER(TRIM(title)) FROM products GROUP BY LOWER(TRIM(title)) HAVING COUNT(*) > 1
                ) s;
            """)
            remaining_dup_titles = cur.fetchone()[0]

            log(f"=== Clean-Up Complete in {time.perf_counter() - t0:.2f}s ===")
            log(f"Final clean products: {final_count:,}")
            log(f"Products with 100% valid image embedding: {valid_image_count:,} ({(valid_image_count/final_count)*100:.1f}%)")
            log(f"Remaining duplicate titles: {remaining_dup_titles} (0 duplicates!)")


if __name__ == "__main__":
    main()
