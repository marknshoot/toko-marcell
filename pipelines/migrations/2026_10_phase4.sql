-- ============================================================================
-- Toko Marcell — Phase 4a migration (2026-10)
-- ============================================================================
-- Applies the Phase 4a DATA-LAYER schema changes to a target database.
--
-- WHAT THIS ADDS (all idempotent):
--   1. products.embedding_ml  vector(384)   + HNSW cosine index
--      (opt-in multilingual embedding column; MiniLM `embedding` stays default)
--
-- WHAT THIS DOES **NOT** DO:
--   It only creates schema. It does NOT populate embedding_ml with data. The
--   data is produced by the offline pipeline and is also included in the
--   committed seed dump (data/seed/init.sql.gz). To load the data into a target
--   DB (e.g. Neon) without a full reseed, see the "Data load" section of
--   pipelines/README.md.
--
-- NOTE: sizing is served entirely from the authored knowledge base
--   (api/knowledge/size_charts.md + size_chart.json). There are no
--   review-derived fit tables any more — product_fit / brand_fit were removed,
--   and this migration no longer creates them. If an older database still has
--   them, they are inert and can be dropped:
--       DROP TABLE IF EXISTS product_fit, brand_fit;
--
-- SAFE TO RUN AGAINST A LIVE DB: every statement uses IF NOT EXISTS and only
-- ADDS objects — it never drops or alters existing columns/tables/data.
--
-- Usage (owner runs this at merge time against the real target):
--   psql "$DATABASE_URL" -f pipelines/migrations/2026_10_phase4.sql
-- ============================================================================

BEGIN;

-- pgvector must already be enabled (it is, for the existing `embedding` column).
CREATE EXTENSION IF NOT EXISTS vector;

-- ── 1. Multilingual embedding column (opt-in) ───────────────────────────────
ALTER TABLE products ADD COLUMN IF NOT EXISTS embedding_ml vector(384);

CREATE INDEX IF NOT EXISTS products_embedding_ml_idx
    ON products USING hnsw (embedding_ml vector_cosine_ops);

COMMIT;

-- ============================================================================
-- Verification (run after loading data):
--   SELECT count(embedding_ml) FROM products;                -- expect 4670
-- ============================================================================
