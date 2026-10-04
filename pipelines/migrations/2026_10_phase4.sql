-- ============================================================================
-- Toko Marcell — Phase 4a migration (2026-10)
-- ============================================================================
-- Applies the Phase 4a DATA-LAYER schema changes to a target database.
--
-- WHAT THIS ADDS (all idempotent):
--   1. products.embedding_ml  vector(384)   + HNSW cosine index
--      (opt-in multilingual embedding column; MiniLM `embedding` stays default)
--   2. product_fit  table (per-ASIN review-derived fit signal)
--   3. brand_fit    table (per-brand fit aggregate)
--
-- WHAT THIS DOES **NOT** DO:
--   It only creates schema. It does NOT populate embedding_ml, product_fit or
--   brand_fit with data. The data is produced by the offline pipelines and is
--   also included in the committed seed dump (data/seed/init.sql.gz). To load
--   the data into a target DB (e.g. Neon) without a full reseed, see the
--   "Data load" section of pipelines/README.md.
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

-- ── 2. Per-ASIN fit signal ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS product_fit (
    asin        TEXT PRIMARY KEY,
    n_mentions  INTEGER NOT NULL DEFAULT 0,
    share_small NUMERIC(5,4) NOT NULL DEFAULT 0,
    share_tts   NUMERIC(5,4) NOT NULL DEFAULT 0,
    share_large NUMERIC(5,4) NOT NULL DEFAULT 0,
    fit_score   NUMERIC(5,4) NOT NULL DEFAULT 0,
    label       TEXT NOT NULL DEFAULT 'insufficient_data'
);

-- ── 3. Per-brand fit aggregate ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS brand_fit (
    brand       TEXT PRIMARY KEY,
    n_mentions  INTEGER NOT NULL DEFAULT 0,
    share_small NUMERIC(5,4) NOT NULL DEFAULT 0,
    share_tts   NUMERIC(5,4) NOT NULL DEFAULT 0,
    share_large NUMERIC(5,4) NOT NULL DEFAULT 0,
    fit_score   NUMERIC(5,4) NOT NULL DEFAULT 0,
    label       TEXT NOT NULL DEFAULT 'insufficient_data'
);

COMMIT;

-- ============================================================================
-- Verification (run after loading data):
--   SELECT count(*) FROM product_fit;                        -- expect ~6000
--   SELECT count(*) FROM brand_fit;                          -- expect ~1495
--   SELECT count(embedding_ml) FROM products;                -- expect 4670
-- ============================================================================
