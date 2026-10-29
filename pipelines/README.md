# `pipelines/` — data engineering, embeddings, and retrieval evaluation harnesses

Everything that runs **offline**. This folder turns an open research dataset into the real data Toko
Marcell serves online: the product catalog, the bilingual vector embeddings, the recommendation tables,
the review corpus, and the evaluation harnesses (IR, RAGAS, and collaborative filtering).

There is no mock data. `../shop` no longer ships a hard-coded product array — if the pipeline has not
run, the shop is empty.

```text
data/raw (2.8 GB, gitignored) → build_catalog.py → data/processed (gitignored)
        → seed.py → PostgreSQL → embed_* → recs/reviews → served by ../api
```

> **Fast path:** you do not need any of this to run the app. `../data/seed/init.sql.gz` is a
> committed dump of the cleaned database, so `docker compose up` boots a fully populated stack in
> seconds. This folder exists for provenance and reproducibility.

---

## 1. Source dataset

| | |
|---|---|
| Dataset | **Amazon Reviews 2018**, category *Clothing, Shoes & Jewelry* |
| Released by | McAuley Lab, UC San Diego — [dataset page](https://cseweb.ucsd.edu/~jmcauley/datasets/amazon_v2/) |
| Paper | Ni, Li, McAuley — *Justifying recommendations using distantly-labeled reviews and fine-grained aspects* (EMNLP 2019) |
| Files | `Clothing_Shoes_and_Jewelry_5.json.gz` (reviews, 1.27 GB) · `meta_Clothing_Shoes_and_Jewelry.json.gz` (metadata, 1.57 GB) |
| Licence | Free for **research / non-commercial** use. Product text and images remain Amazon's property; images are hot-linked, never re-hosted. |

**Why this dataset.** It is the canonical recommender-systems benchmark, so HR@10 / nDCG@10 numbers
are comparable with published results instead of being self-reported in a vacuum. It also carries
exactly the fields the product needs: real titles, brands, prices, category trees, feature bullets and
descriptions (search + NLP), user_id / asin / rating / timestamp (recommendations), and `also_buy` /
`also_view` (a ready-made co-view signal).

**Why not the 2023 refresh.** `McAuley-Lab/Amazon-Reviews-2023` was tried first and rejected on
evidence: in `Amazon_Fashion`, **90% of items have no price** and the `categories` field is empty,
which makes a shop catalog impossible.

---

## 2. Reproduce

```bash
# 1. download raw files (~2.8 GB, resumable, gitignored)
bash pipelines/download_data.sh

# 2. build the catalog + interactions (~8 min, stdlib only, no pandas)
python3 pipelines/build_catalog.py

# 3. load it into Postgres
cd api && docker compose run --rm seed --reset
```

Outputs land in `data/processed/` (gitignored — regenerate instead of committing):

| File | Size | What it is |
|---|---|---|
| `products.jsonl` | ~6.6 MB | 6,000 real products → seeds Postgres |
| `interactions.csv.gz` | ~43 MB | `user_id, asin, rating, timestamp` — 3,363,680 rows |
| `categories.json` | small | departments + top category names → shop filter chips |
| `stats.json` | small | **every number quoted in this README** |

Peak memory stays flat (three streaming passes, no pandas) because the build machine has little RAM to
spare.

---

## 3. What the catalog build does

```text
pass 1  reviews   → interactions per item + rating sum          (11,285,464 reviews read)
pick    top candidates by interactions (8× the catalog size)
pass 2  metadata → quality filters, category vocabulary, brand lookup
pass 3  reviews   → interactions.csv.gz for the kept items only
```

### Real-data traps this handles (each found the hard way)

| Trap | Handling |
|---|---|
| Every value is a **string**, including numbers (`"overall": "5.0"`) | `to_float` / `to_int` coercion |
| `category`, `description`, `imageURLHighRes` are **Python-repr strings**, not JSON | `ast.literal_eval` with a regex fallback |
| Amazon glues **spec bullets onto the category path** (`"Rubber sole"`, `"Imported"`) | only the first 4 path nodes are considered, plus a 30-entry spec stoplist |
| Category branches are near-unique junk strings | a name must repeat ≥150 times among candidates to count as a category (131 survive) |
| **Duplicate ASINs** (361 repeated metadata rows) | de-duplicated; the drop count is recorded |
| `brand` missing on many rows — including *Levi's 501* | matched against brand spellings in the file; `brandSource` records `metadata` vs `title_match` |
| 65% of items have **no price**, 39% have **no image** | both required, so candidates are drawn from an 8× larger pool |
| Prices are ranges (`"$11.13 - $117.19"`) | lowest variant price is taken |
| Some scraped rows carry HTML/script junk in the title | dropped (`scraped_script_title`: 621) |

### Filters applied to become a product

`title` present · price parseable and between $1 and $1000 · image URL present · ≥6 interactions.
Recorded drops from `stats.json`: **11,358 for no price**, **1,786 for no image**, **361 duplicate
metadata rows**, **621 scraped-script titles**. The top **6,000** products by interaction count are
kept (all distinct ASINs).

---

## 4. What the catalog actually contains (build = 6,000 products)

| Metric | Value |
|---|---|
| Products | 6,000 (all distinct ASINs) |
| Departments | Women 3,409 · Men 1,887 · Girls 151 · Boys 131 · Baby 116 · Other 306 |
| Categories | all 6,000 have one; top are Lingerie 452, Jeans 212, Novelty 212, Casual 192, Fashion Sneakers 183, Shoes 164, Running 160, Flats 154 |
| Brands | 5,371 products (90%) — 2,954 matched from metadata, 2,417 from the title; 1,500 distinct brands |
| Description | 4,689 products |
| Feature bullets | 5,957 products |
| Co-view links (`also_buy` / `also_view`, restricted to the catalog) | 3,453 products |
| Interactions | 3,363,680 rows from 972,184 users |
| Interactions per product | min 198 · median 333 · max 19,693 |
| Price (USD) | min 1.00 · median 17.86 · p90 49.99 · max 275.00 |
| Matrix | 3,363,680 / 5,833,104,000 cells = **0.058% dense** |

### 6,000 built → 4,670 served

After the build, `cleanup_catalog.py` runs against the live database and removes:

1. products with a **dead image CDN link** (`image_embedding IS NULL`), and
2. **duplicate title variants**, keeping the best-rated/most-reviewed row.

Dependent reviews are deleted too, then `VACUUM ANALYZE` runs. The shipped seed and the storefront
copy therefore read **4,670 products / 46,700 reviews**.

---

## 5. Vector embeddings

| Script | Model | Dim | Column | Index |
|---|---|:---:|---|---|
| `embed_catalog.py` | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (default) or `all-MiniLM-L6-v2` via `fastembed` | 384 | `products.embedding_ml` / `products.embedding` | HNSW cosine |
| `embed_images.py` | `Qdrant/clip-ViT-B-32-vision` (`fastembed` ONNX) | 512 | `products.image_embedding` | HNSW cosine |

Text representation for MiniLM is `title + brand + category + features + description` — the same
fields BM25 indexes, so the two rankers see the same evidence and their disagreement is genuinely
about *method*, not data.

`download_images.py` prefetches and caches catalog images to `data/processed/images/` (40 threads) so
`embed_images.py` does not re-hit Amazon's CDN.

```bash
# Multilingual embeddings (default served column: embedding_ml):
python3 pipelines/embed_catalog.py \
  --model sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 \
  --column embedding_ml

# English MiniLM embeddings (products.embedding):
python3 pipelines/embed_catalog.py

# Pre-trained CLIP vision embeddings (products.image_embedding):
python3 pipelines/embed_images.py --overwrite
```

---

## 5b. Sizing & store policy knowledge (authored, bilingual RAG)

Sizing and policy guidance comes from the **authored knowledge base**:

| File | What |
|---|---|
| `api/knowledge/size_charts.md` | the store's bilingual sizing guide: TB/BB tops chart, waist bottoms chart, footwear conversion, and per-brand cut rules for Dickies, Levi's, Carhartt, Champion, Birkenstock and TOMS |
| `api/knowledge/size_chart.json` | the same values in machine-readable form, consumed by `api/sizing.py`'s `recommend_size` |
| `api/knowledge/store_policies.md` | store identity, QRIS demo checkout rules, shipping/ongkir rates, and 7-day return/exchange policy |

`api/knowledge_seed.py` splits the markdown files by section header, embeds each chunk + 3–5 bilingual
(ID+EN) hypothetical questions using `TEXT_EMBED_MODEL` (`paraphrase-multilingual-MiniLM-L12-v2`), and
populates `store_knowledge` and `store_knowledge_questions`. At query time, `lookup_store_policy` fuses
hypothetical-question vectors, direct-chunk vectors, and IDF-weighted bilingual lexical keywords via
Reciprocal Rank Fusion (`k=60`).

---

## 6. Recommendations

Recommendations are built offline by `build_recs.py` and served by three API endpoints. Every rail
comes from the real 3.36 M-interaction log plus the catalog's own `also_buy`/`also_view` graph:

1. **Popularity, Bayesian-smoothed:** $\text{score} = \frac{n \cdot \bar{r} + m \cdot C}{n + m}$ with
   `m = 100`, `C = 4.31`.
2. **Item-to-item co-occurrence:** cosine similarity over per-item user sets, capped at 5,000 sampled
   users for ultra-popular items.
3. **Metadata boosts:** `also_buy` adds `+0.50` and `also_view` adds `+0.30`.
4. **Title dedupe + cold fill:** variants collapsed; ≤12 recs/item; backfill from category then
   department popularity when fewer than 6 remain.

---

## 7. Reviews

`extract_reviews.py` streams the 1.27 GB raw reviews file and keeps up to **10 authentic reviews per
catalog ASIN** (rating, summary, comment, verified flag, date), writing them to the `reviews` table —
**46,700** rows in the seed.

---

## 8. Evaluation harnesses at a glance

| Harness | Protocol | Metrics |
|---|---|---|
| `eval_search.py` | 32 queries in 4 groups (brand/SKU, category+features, semantic, Indonesian/casual), 5 modes | HitRate@10, Precision@10, MRR@10, nDCG@10 |
| `eval_rag.py` | 12 bilingual (ID+EN) policy & sizing queries evaluated with **RAGAS** (`direct_chunk` vs `hypothetical_question` vs `hybrid_rrf`) | `id_based_context_precision`, `id_based_context_recall`, `non_llm_context_precision_with_reference`, `non_llm_context_recall` |
| `eval_recs.py` | sequential leave-one-out, ≥5 interactions/user, random + popularity baselines | HR@10, HR@5, MRR@10, nDCG@10 |
| `eda_interactions.py` | interaction EDA | volume, coverage, sparsity, long tail, rating distribution, temporal span |
| `tests/test_pipeline_math.py` | unit tests for pipeline maths (no DB) | — |

All harnesses print human-readable tables and write machine-readable JSON artifacts to `pipelines/results/`.

---

## 9. Script reference

| Script | Purpose |
|---|---|
| `download_data.sh` | resumable download of the two raw Amazon files |
| `build_catalog.py` | 3-pass streaming build → `products.jsonl`, `interactions.csv.gz`, `stats.json` |
| `seed.py` | upsert catalog → Postgres (offline compose profile) |
| `cleanup_catalog.py` | remove dead-image and duplicate-title rows (6,000 → 4,670) |
| `download_images.py` | prefetch product images to the local cache |
| `embed_catalog.py` | 384-d text embeddings + HNSW (`--model`/`--column` for multilingual `embedding_ml`) |
| `embed_images.py` | pre-trained `fastembed` CLIP (`Qdrant/clip-ViT-B-32-vision`) 512-d image embeddings + HNSW |
| `build_recs.py` | co-occurrence + metadata boosts + Bayesian popularity + category/department backfill |
| `extract_reviews.py` | ≤10 real reviews per ASIN → `reviews` |
| `eda_interactions.py` | interaction EDA |
| `eval_search.py` | 5-mode BM25 / vector / hybrid / rerank / trimodal IR harness |
| `eval_rag.py` | RAGAS evaluation harness for store policy & sizing RAG |
| `eval_recs.py` | recommendation HR/nDCG harness |
| `migrate_to_neon.py` | local → Neon serverless migration (schema, catalog, embeddings, indexes) |

---

### Related docs

- [`BENCHMARKS.md`](./BENCHMARKS.md) — the source ledger for every headline number
- [`../README.md`](../README.md) — project overview and architecture
- [`../api/README.md`](../api/README.md) — how the served catalog, search and copilot work
