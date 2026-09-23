# `pipelines/` — dataset → catalog + interactions

This folder turns an **open research dataset** into the real data Toko Marcell runs on:
the product catalog that FastAPI serves, and the interaction log the recommendation
model will be trained and evaluated on.

Nothing here is a mock. `manual/shop` no longer ships a hard-coded product array.

---

## 1. Source

| | |
|---|---|
| Dataset | **Amazon Reviews 2018**, category *Clothing, Shoes & Jewelry* |
| Released by | McAuley Lab, UC San Diego — [dataset page](https://cseweb.ucsd.edu/~jmcauley/datasets/amazon_v2/) |
| Paper | Ni, Li, McAuley — *Justifying recommendations using distantly-labeled reviews and fine-grained aspects* (EMNLP 2019) |
| Files | `categoryFilesSmall/Clothing_Shoes_and_Jewelry_5.json.gz` (reviews, 1.27 GB) · `metaFiles2/meta_Clothing_Shoes_and_Jewelry.json.gz` (metadata, 1.57 GB) |
| Licence | Free for **research / non-commercial** use. Product text and images remain Amazon's property; images are hot-linked, never re-hosted. |

**Why this dataset.** It is the canonical recommender-systems benchmark, so HR@10 /
nDCG@10 numbers are comparable with published results instead of being self-reported
in a vacuum. It also carries exactly the fields the product needs: real titles, brands,
prices, category trees, feature bullets and descriptions (search + NLP), plus
user_id / asin / rating / timestamp (recs), plus `also_buy` / `also_view` (co-view).

**Why not the 2023 refresh.** `McAuley-Lab/Amazon-Reviews-2023` was tried first and
rejected on evidence: in `Amazon_Fashion`, **90% of items have no price** and the
`categories` field is empty, which makes a shop catalog impossible.

> `plan/` had "synthetic events" for recs (M11–M12). That is upgraded: interactions are now
> **real** — 3.4M genuine user–item rows with timestamps, so time-based train/test splits
> and HR@10 are meaningful, not self-graded.

---

## 2. Reproduce

```bash
cd manual
bash pipelines/download_data.sh                  # ~2.8 GB into data/raw/ (gitignored)
python3 pipelines/build_catalog.py               # ~8 min, stdlib only, no pandas
```

The catalog then goes into Postgres through the API's offline seed job:

```bash
cd api && docker compose run --rm seed --reset
```

Outputs land in `data/processed/` (also gitignored — regenerate instead of committing):

| File | Size | What it is |
|---|---|---|
| `products.jsonl` | 6.6 MB | 6,000 real products → seeds Postgres |
| `interactions.csv.gz` | 43 MB | `user_id, asin, rating, timestamp` — 3,407,740 rows |
| `categories.json` | small | departments + top category names → shop filter chips |
| `stats.json` | small | every number quoted in this README |

Peak memory stays flat (three streaming passes, ~7 GB machine, no pandas) because the
machine that builds this has little RAM to spare.

---

## 3. What the pipeline does

```
pass 1  reviews   -> interactions per item + rating sum          (11,285,464 reviews)
pick    top 48,000 items by interactions (8x the catalog size)
pass 2  metadata -> quality filters, category vocabulary, brand lookup
pass 3  reviews   -> interactions.csv.gz for the kept items only
```

### Real-data traps this handles (each one was found the hard way)

| Trap | Handling |
|---|---|
| Every value is a **string**, including numbers (`"overall": "5.0"`) | `to_float` / `to_int` coercion |
| `category`, `description`, `imageURLHighRes` are **Python-repr strings**, not JSON | `ast.literal_eval` with a regex fallback |
| Amazon glues **spec bullets onto the category path** (`"Rubber sole"`, `"Imported"`, `"Machine Wash"`) | only the first 4 path nodes are considered, plus a 30-entry spec stoplist |
| Category branches are near-unique junk strings | a name must repeat ≥ 150 times among candidates to count as a category (131 survive) |
| **Duplicate ASINs** (361 repeated rows in the metadata file) | de-duplicated, the drop count is recorded |
| `brand` missing on 80% of rows — including *Levi's 501* | matched against every brand spelling in the file; `brandSource` records `metadata` vs `title_match` |
| 65% of items have **no price**, 39% have **no image** | both are required, so candidates are drawn from 8x the final catalog |
| Prices are ranges (`"$11.13 - $117.19"`) | lowest variant price is taken |
| The category file is not 100% clothing (books, luggage) | department is recorded; `--drop-unknown-department` available |

### Filters applied to become a product

`title` present · price parseable and between $1 and $1000 · image URL present ·
≥ 6 interactions. Out of 48,000 candidates: **11,496 dropped for no price**, 1,823 for no
image, 361 duplicate rows, and the top 6,000 by interaction count were kept.

---

## 4. What the catalog actually contains

| Metric | Value |
|---|---|
| Products | 6,000 (all distinct ASINs) |
| Departments | Women 3,405 · Men 1,897 · Other 307 · Girls 152 · Boys 128 · Baby 111 |
| Categories | all 6,000 have a category; popular ones are Lingerie (456), Jeans (214), Novelty (212), Casual (190), Fashion Sneakers (183), Shoes (161), Running (158), Flats (150) |
| Brands | 5,321 products (89%) — 2,942 from metadata, 2,379 matched from the title; 1,480 distinct brands |
| Description | 4,717 products |
| Feature bullets | 5,958 products |
| Co-view links (`also_buy` / `also_view`, restricted to the catalog) | 3,454 products |
| Interactions | 3,407,740 rows from 972,119 users |
| Interactions per product | min 201 · median 337 · max 19,693 |
| Price (USD) | min 1.00 · median 17.90 · p90 49.99 · max 275.00 |
| Matrix sparsity | 3,407,740 / 5,832,714,000 cells = **0.058% dense** |

The long tail is real: the most-reviewed item has 19,693 interactions while the minimum
in the catalog is 201 — a 98x spread over just the kept items.

---

## 5. Decisions you may want to revisit

1. **Prices are USD, the shop shows IDR.** `--usd-idr` (default `16000`) is a *fixed demo
   rate*, recorded in `stats.json` and surfaced in the README. It is a presentation
   conversion, not a live rate. Real prices are kept untouched in `priceUsd`.
2. **The catalog is multi-brand — decided, not accidental** (Levi's, Dickies, TOMS, Birkenstock,
   Timex…), superseding the "single-brand shop" line that used to be in `PLAN.md` §2. Multi-brand
   is **not** multi-vendor: one seller, one catalog, one checkout, no marketplace mechanics
   (BRD BR-8). The alternative — filtering to one brand — was rejected because it would shrink the
   catalog and weaken the recs signal without improving the product story.
3. **`products.id` is a stable integer** (`1..6000`, ordered by interaction count) because
   FastAPI and the cart use ints; `asin` stays the ML/event key. Deterministic across runs.
4. **Images are hot-linked** from Amazon's CDN rather than downloaded, to respect the
   dataset licence and to keep the repo light.
5. **`data/processed/` is gitignored.** The catalog is 6.7 MB and regenerable; committing
   it is an option if you want the demo to run without an 8-minute pipeline.

---

## 6. What this unlocks (next)

| Task | Uses |
|---|---|
| Hybrid search + IR eval (PLAN M3–M6) | `title` + `features` + `description` + `categoryPath` as the searchable text |
| Embeddings (pgvector, already in the image) | same text columns |
| Recs: popularity baseline → CF / co-view / LightGBM (M7–M10) | `interactions.csv.gz` (3.4M real interactions, timestamps for time-based splits) |
| Session/event log + funnel KPIs (M2, M11–M12) | interactions converted to browsed→cart→purchase events instead of purely synthetic ones |
| EDA (M14) | sparsity, long-tail, price spread already in `stats.json` |
| Copilot tools (A1–A6) | reuses `/search`, `/products`, `/recs` over the real catalog |
| NLP | review text is in the raw file if you want sentiment / summarisation / FAQ RAG |
