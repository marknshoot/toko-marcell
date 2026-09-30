# `api/` — Toko Marcell backend (FastAPI + PostgreSQL/pgvector)

Marcell's own backend, built from scratch. It owns **everything the frontend must not do**: REST
validation, the catalog, hybrid search, recommendations, reviews, the funnel event log,
server-authoritative checkout, and the grounded AI copilot.

```text
Browser ──HTTP──► Next.js (../shop) ──REST/JSON──► FastAPI (this) ──SQL──► PostgreSQL 16 + pgvector
```

**Hard rule:** Next never opens a database connection. The frontend only ever talks HTTP. That keeps
the client free of SQL, credentials and business rules, and makes the API the single place where a
product, a price, or a policy can be authoritative.

> **Live:** [https://toko-marcell-api.onrender.com/docs](https://toko-marcell-api.onrender.com/docs)
> (Render free tier — the first request after idle can take 30–50 s to wake; the storefront shows a
> cold-start overlay for exactly this).

---

## Run it

```bash
cd manual/api
docker compose up -d --build        # api on :8001 → container :8000, Postgres on :5432 (pgvector/pg16)
curl -s localhost:8001/health       # {"status":"ok"}
```

Interactive docs: **http://localhost:8001/docs**

The root [`docker-compose.yml`](../docker-compose.yml) also boots the storefront; `api/docker-compose.yml`
is the backend-only stack. Both mount [`../data/seed/init.sql.gz`](../data/seed/init.sql.gz), so a fresh
database restores the complete catalog, vectors, knowledge base and demo history within seconds.

### Seeding the catalog (offline, by design)

The API **creates the schema but never writes catalog rows** — that is an offline job, so
`docker compose up` stays a server and the ML pipeline stays independent of request handling.

```bash
# 1. build the dataset once (~8 min) — see ../pipelines/README.md
cd manual && python3 pipelines/build_catalog.py

# 2. load it into Postgres (the `seed` service is behind a profile, so `up` never starts it)
cd api
docker compose run --rm seed              # upsert by asin
docker compose run --rm seed --reset      # wipe + reload (after regenerating)
```

After a reseed, call `POST /search/reindex` (or restart the container): the BM25 index lives for the
life of the process and would otherwise serve a stale catalog.

---

## Configuration

| Variable | Required | Default | Purpose |
|---|:---:|---|---|
| `DATABASE_URL` | ✅ | — | Postgres connection string (Neon in production, `postgresql://toko:toko@db:5432/toko` in compose) |
| `GEMINI_API_KEY` | for copilot | — | Google AI Studio key; without it `/copilot/chat` returns `503`, everything else works |
| `GEMINI_MODEL` | | `gemini-3.5-flash-lite` (code) / `gemini-2.5-flash` (compose) | Any Gemini chat model |
| `CORS_ORIGINS` | | `http://localhost:3000,https://toko-marcell.vercel.app` | Comma-separated allowlist; `*` opens it up |
| `HF_MODEL_REPO` | | `Marcell-Kristianto/toko-marcell-clip` | HF repo for the champion ONNX text encoder |
| `CLIP_MODEL_DIR` / `CLIP_MODEL_PATH` | | auto-detected | Optional local paths for the champion CLIP **PyTorch** weights |
| `CLIP_VISION_ONNX_PATH` | | auto-detected | Explicit path to the champion ONNX **vision** encoder |
| `CLIP_VISION_ONNX_PREFER` | | `int8` | Prefer `int8` or `fp32` ONNX vision encoder |
| `FASTEMBED_CACHE_DIR` | | `/tmp/fastembed_cache` | Cache for MiniLM/CLIP fastembed models |
| `HF_HUB_CACHE` | | `/tmp/hf_cache` | Cache for the ONNX cross-encoder |

Models are loaded **lazily and cached per process**, so a model-download problem cannot stop the API
from booting, and catalog browsing never pays for a search model it does not use.

---

## Endpoints

| Method | Path | Notes |
|---|---|---|
| `GET` | `/health` | Liveness; also the target of the frontend cold-start ping |
| `GET` | `/categories` | Facets with real counts (departments + top 24 categories); in-memory TTL cache (~2 ms) |
| `GET` | `/products` | Paginated catalog (`limit≤24`, `offset`, `department=`, `category=`) |
| `GET` | `/products/{id}` | Single product · `404` missing · `422` invalid id |
| `GET` | `/products/{id}/reviews` | Reviews + rating breakdown for a numeric product id |
| `GET` | `/search` | Hybrid/BM25/vector/trimodal search: `q`, `mode`, `department`, `category`, `limit`, `offset` |
| `POST` | `/search/image` | Visual similarity search from an uploaded image (multipart `file`) |
| `POST` | `/search/reindex` | Rebuild the in-memory BM25 index and drop the facet cache → `202` |
| `GET` | `/recs/popular` | Popularity baseline (optionally by `department`) |
| `GET` | `/recs/item/{asin}` | Item-to-item recommendations with a category-popularity fallback |
| `GET` | `/recs/session` | Session-based recommendations from the current visit's events; cold → popularity |
| `GET` | `/reviews/{asin}` | Reviews for an ASIN · `404` if the product is unknown |
| `POST` | `/copilot/chat` | Agentic copilot turn (planner → tools → grounded synthesis) |
| `GET` | `/copilot/tools` | The bound tool schema (names, parameters, descriptions) |
| `POST` | `/events` | Record a funnel event → `201` · `422` on unknown type |
| `GET` | `/events/summary` | Funnel KPIs + explicit caveats |
| `POST` | `/checkout/confirm` | Create a paid order from `{items:[{asin,qty}], session_id}` → `201` |
| `GET` | `/orders/{token}` | Read an order back by its secret token · `404` unknown |

### Response envelopes, never bare arrays

`/products` and `/search` return:

```json
{ "items": [ ... ], "total": 4670, "limit": 24, "offset": 0 }
```

`/search` adds `{ "query", "mode", "deduped": true, "took_ms", "score" }` per item so the UI and the
eval harness can tell *which ranker* produced a result set without guessing from a deployment.

A product is the shape the storefront renders (camelCase, numbers as numbers):

```json
{
  "id": 1, "asin": "B000YXC2LI",
  "title": "Levi's Men's 501 Original-Fit Jean", "brand": "Levi's",
  "priceUsd": 30.92, "priceIdr": 494700,
  "department": "Men", "category": "Jeans",
  "categoryPath": ["Men", "Clothing", "Jeans"],
  "description": "A cultural icon. Worn by generations …",
  "features": ["100% Cotton", "Imported"],
  "imageUrl": "https://images-na.ssl-images-amazon.com/images/I/31Y1h0pJtLL.jpg",
  "avgRating": 4.2, "ratingCount": 19693,
  "alsoBuy": ["B00B58FQ5K"], "alsoView": []
}
```

---

## Search — how it ranks

Search is the heart of the backend, so the reasoning is documented where the code lives
(`search.py`, `main.py`) and summarized here.

### BM25 (the lexical baseline)

- **Scoring:** Robertson & Zaragoza BM25, `k1=1.5`, `b=0.75`, IDF with `+1` smoothing so a term in
  every document scores 0, not negative.
- **Field weights:** title 3.0 · brand 2.0 · category 1.5 · department 1.0 · features 1.0 ·
  description 0.5. Weighted term frequencies are a practical alternative to one index per field, and
  BM25's saturation handles fractional frequencies correctly.
- **Tokenizer:** lowercase → NFKD accent folding → alphanumeric → stopwords → plural fold
  (`sneakers→sneaker`, `jeans→jean`, `watches→watch`). It is a small suffix folder, **not** a Porter
  stemmer — deliberate, and documented.
- **Index:** hand-written, in-process, zero dependency. Built lazily on first search, cached for the
  process, rebuilt by `/search/reindex`.
- **Dedup:** duplicates by normalized title are removed **before** pagination, so `total` counts
  rows a shopper can actually reach (842 of the 6,000 built products are title-sharing variants).

**What it deliberately cannot do:** typos, synonyms, phrases, iconography. Those are the jobs of the
other rankers — which is exactly why the baseline is measured first.

### Dense + cross-modal (the vector half)

- 384-d `all-MiniLM-L6-v2` sentence embeddings → `products.embedding` (HNSW cosine).
- 512-d CLIP embeddings → `products.image_embedding` (HNSW cosine).
- `/search?mode=` switches ranker: `bm25`, `vector`, `hybrid` (default: BM25+vector), `trimodal`
  (BM25+dense+CLIP-text). All fused lists go through **Reciprocal Rank Fusion** with `k=60`.

$$
\text{RRF}(d) = \sum_{m \in M} \frac{1}{60 + r_m(d)}
$$

RRF is chosen over score normalization because BM25 scores are unbounded positives while cosine is
bounded; RRF needs only ranks, so heterogeneous rankers combine with no calibration.

### Image search (`POST /search/image`)

1. Validate MIME (`image/*`) and size (50 B–10 MB).
2. Encode with the **fine-tuned champion CLIP vision tower** — always. It is served as a quantized
   ONNX model (`champion_vision_encoder_int8.onnx`, ~96 MB) via ONNX Runtime, so it needs no `torch`.
   Preprocessing replicates `CLIPImageProcessor` exactly (verified: embedding cosine 1.0 vs the
   official processor, 0.991 vs the fp32 encoder).
3. Cosine search against `image_embedding`, ordered by HNSW, returning `visualSimilarity` per item.

**There is deliberately no zero-shot fallback.** The stored `image_embedding` vectors were produced by
the fine-tuned champion; mixing in fastembed's zero-shot CLIP would silently return wrong results. If
no champion encoder is available the endpoint returns **`503`** (“fine-tuned CLIP vision encoder is not
loaded”) rather than a 500 or a wrong answer. A corrupt upload that still passes the MIME/size checks
returns `400`.

The response includes a `timings: { embed_ms, db_ms }` breakdown so encoding cost is never confused
with database cost. To regenerate the ONNX encoder:
`python3 pipelines/export_vision_onnx.py` — see [`../pipelines/README.md`](../pipelines/README.md).

### Stage-2 cross-encoder reranking

`reranker.py` re-scores the retrieved candidates with `Xenova/ms-marco-MiniLM-L-6-v2` through ONNX
Runtime (~15–25 ms for ~20 candidates). A cross-encoder sees query and document together, capturing
interactions a bi-encoder cannot. If the model cannot be loaded, it logs and returns the Stage-1
order — graceful degradation, never a hard failure.

### Evaluation

`../pipelines/eval_search.py` evaluates **32 queries** across four groups (exact brand/SKU, category +
features, semantic/intent, Indonesian/casual) and reports HitRate@10, Precision@10, MRR@10 and nDCG@10
for BM25 vs hybrid, with per-group breakdown and relative gains.

---

## Recommendations

Recommendations are the second half of the product story (search answers "find this"; recs answer
"what next"). The serving layer is deliberately built so that **every request has an answer, even on
a cold visit** — a fallback chain rather than a single model.

| Endpoint | Mechanism | Cold fallback |
|---|---|---|
| `/recs/popular` | live query ordered by `rating_count DESC, avg_rating DESC`, optional department filter | — |
| `/recs/item/{asin}` | offline item-to-item table `item_recommendations` (co-occurrence + metadata boosts), up to `limit` | same category, then department popularity (`category_popularity_fallback`) |
| `/recs/session` | last 5 `view_product`/`add_to_cart` events for the anonymous session → union of their item recs, excluding already-seen ASINs | global popularity (`cold_popularity`) |

Every response carries a `strategy` field (`item_cf`, `category_popularity_fallback`, `session_cf`,
`cold_popularity`, `popularity_baseline`) so the UI and any dashboard can tell *how* a rail was
produced instead of inferring it.

### How the offline table is built (`../pipelines/build_recs.py`)

1. **Popularity, Bayesian-smoothed** — `score = (n·r̄ + m·C) / (n + m)` with `m = 100`, `C = 4.31`,
   so a 3-review item cannot outrank a 20k-review item. Global top-50 + top-30 per department,
   title-deduped.
2. **Item-to-item co-occurrence** — cosine similarity over per-item user sets,
   `cos = co(i,j) / (√n_i · √n_j)`, capped at 5,000 sampled users for ultra-popular items.
3. **Metadata boosts** — `also_buy` `+0.50`, `also_view` `+0.30`.
4. **Title dedupe + cold fill** — variants collapsed; ≤12 recs/item; backfill from category then
   department popularity when fewer than 6 remain.

This is why the store has **no long-lived user profile** and still personalizes: identity is the
anonymous `sessionStorage` id, so the online signal is the current visit, and the offline co-occurrence
table carries the long-horizon behaviour. It is the cold-start problem answered at four levels
(session → item → category → global).

### Evaluation (`../pipelines/eval_recs.py`)

Sequential **leave-one-out** on users with ≥5 interactions: target = the user's final interaction,
context = the penultimate item (a realistic "current PDP"), everything else masked. Reports HR@10,
HR@5, MRR@10 and nDCG@10 against random and global-popularity baselines.

---

## Reviews

`GET /reviews/{asin}` and `GET /products/{id}/reviews` return an aggregate (count, average, 1–5 star
breakdown) plus up to `limit` authentic reviews. These are real Amazon reviews extracted for the
catalog (`../pipelines/extract_reviews.py`, ≤10 per ASIN), stored in the `reviews` table — the seed
contains **46,700** of them. The copilot can reason over them for aspect-level social proof
(e.g. "buyers say it runs small").

---

## The AI copilot

The copilot is an **agentic RAG loop**, not a chat completion with the catalog pasted in.

```text
Node 1  Planner / guardrail   one Gemini call with the tool schema bound → 0..n tool calls
Node 2  Tool fan-out          asyncio.gather over deterministic DB tools
Node 3  Grounded synthesis    second Gemini call that may only use tool evidence
```

**Bound tools (4):**

| Tool | Backed by | Purpose |
|---|---|---|
| `search_catalog` | BM25 + pgvector + reranker, with department/category/budget filters | product discovery, styling, budgets |
| `get_product_details` | `products` by ASIN or id | fabric, specs, exact IDR price |
| `search_by_image` | CLIP 512-d vs `image_embedding` | "find something like this photo" |
| `lookup_store_policy` | `store_knowledge` (384-d RAG) | sizing (TB/BB), shipping, returns, QRIS demo |

`agent_tools.py` also implements `get_product_reviews` as a deterministic helper for aspect-level
social proof. There is deliberately **no order-status tool**: checkout is a demo simulation with no
fulfillment, shipping or tracking to report on. (The `/orders/{token}` REST endpoint exists only so
the receipt page can render a confirmed order; it is not exposed to the model.)

**Grounding guarantees:**

- Every product in a reply comes from a tool result; the prompt forbids invented brands, ASINs, stock
  or discounts, and the tool results are the only catalog data in context.
- Prices are always IDR, always from `products.price_idr`.
- Sizing answers must apply authored brand overrides (Dickies 874, Levi's 505 vs 501, Carhartt,
  Champion, Birkenstock, TOMS) from `knowledge/size_charts.md`.
- Off-topic, coding, SQL, academic, medical/legal and prompt-injection requests get **zero tool
  calls** and a warm refusal that steers back to the store.
- Tool evidence is compacted before synthesis (≤4 products, ≤4 features, 200-char description,
  300-char policy chunks) to protect the context window and API quota.

**Knowledge ingestion.** `knowledge_seed.py` splits `knowledge/*.md` by section header, embeds each
chunk with MiniLM and upserts into `store_knowledge` (22 chunks, HNSW index). It is idempotent: it
returns early if the table already has rows. Force a reload with
`python3 knowledge_seed.py`.

**Session context.** The orchestrator reads the shopper's last few browsing/cart events by session id
and appends them to the system prompt, so "have you got these in black?" has a referent.

See [`../pipelines/failure_case_analysis.md`](../pipelines/failure_case_analysis.md) for the
grounding/persona rationale.

---

## Events, funnel & checkout

### Events (`M2`)

One row per shopper action, grouped by an **anonymous** `session_id` generated in the browser
(`sessionStorage` + `crypto.randomUUID()`). No accounts, no PII.

| Event | Fired by | Extra fields |
|---|---|---|
| `view_product` | PDP mount | `asin` |
| `search` | debounced catalog query | `query`, `results_count` |
| `add_to_cart` | cart provider (every entry point) | `asin`, `qty`, `price_idr` |
| `checkout_start` | first render of `/checkout` with a non-empty cart | `qty`, `price_idr` |
| `purchase_mock` | written **by the server** inside checkout confirm | `qty`, `price_idr` |

```bash
curl -s -X POST localhost:8001/events -H 'Content-Type: application/json' \
  -d '{"event_type":"view_product","session_id":"sess-abc12345","asin":"B000YXC2LI"}'
curl -s localhost:8001/events/summary | python3 -m json.tool
```

The frontend posts events **fire-and-forget** (`keepalive: true`, errors swallowed): losing a data
point is cheaper than breaking a purchase.

`GET /events/summary` returns `by_type`, `sessions_by_step`, `rates` and `search`, and — importantly —
its own `caveats` array. Three matter:

1. `search_to_pdp` is a **session-level proxy** (search + view_product in one session), not a CTR.
2. `results_count` counts matches among currently loaded products, because filtering is client-side.
3. `purchase_mock` is now written by the confirm handler (server-side), so it is authoritative.

Rates are `null`, never `0.0`, when a denominator is missing — an empty events table cannot produce a
misleading "0% conversion".

### Checkout — the authoritative step

Before this existed, "paid" was a browser claim and `/checkout/success` confirmed an order to anyone
who typed the URL. Now the truth is a row in `orders`.

```bash
curl -s -X POST localhost:8001/checkout/confirm -H 'Content-Type: application/json' \
  -d '{"items":[{"asin":"B000YXC2LI","qty":2}],"session_id":"sess-abc12345"}'
# → {"token":"tk_...","status":"paid","total_idr":989400,"item_count":2,"items":[...]}

curl -s localhost:8001/orders/<token>
```

**The rule that matters: the client sends `{asin, qty}` and never a price.** The server looks up
`products.price_idr` for every line and computes the total itself. `ConfirmIn` sets `extra="forbid"`,
so a request that tries to send a total is rejected with `422` instead of being silently ignored —
the rule is enforced at the edge and visible in the logs.

Consequences of putting the order on the server:

- `/checkout/confirm/[token]` **fetches** the order, so it survives refresh, a new tab, or another
  device. An invalid token renders a 404, not a fake receipt.
- `purchase_mock` is written by the server inside the confirm handler, so the funnel's last step is
  authoritative.
- `session_id` is **required** on confirm; a purchase that cannot be attributed to a visit is useless.
- The client cart gained `asin`, so the localStorage key moved to `toko-cart-v2`. A v1 cart line has no
  asin and cannot be priced; the key bump is honest where a silent migration would have produced
  unpriceable lines.

**Known simplification:** confirm is **not idempotent** — a double-click could create two orders. The
button disables in flight, but a production system wants an `Idempotency-Key` header.

---

## Caching & defensive security

Designed to be reliable on free infrastructure (Render + Neon + Vercel).

### Multi-tier caching

- **Edge ISR** (`../shop`): product detail routes export `revalidate = 300` → stale-while-revalidate on
  Vercel's CDN.
- **HTTP Cache-Control**: `/products`, `/products/{id}` and `/categories` emit
  `public, max-age=60, s-maxage=300, stale-while-revalidate=60` (product detail uses a longer TTL).
- **In-memory TTL**: `/categories` aggregates are cached for 300 s, turning a `GROUP BY` scan
  (~60 ms) into ~2 ms. `/search/reindex` invalidates it.

### Defensive boundaries

- **AI token safeguard** — `/copilot/chat`: ≤2,000 chars/message, ≤20 messages, ≤8 MB image reference.
- **Server-authoritative checkout** — see above; a tampered price is a `422`, not a discount.
- **Image search bounds** — MIME `image/*`, 50 B–10 MB.
- **CORS** — configurable allowlist plus an explicit `^https://.*\.vercel\.app$` regex for preview
  deployments. The shipped Render blueprint uses `CORS_ORIGINS=*` so any preview URL works without a
  redeploy; this is a deliberate tradeoff because the API is stateless, credential-free and sets no
  cookies. It is documented rather than hidden.
- **Frontend headers** (`../shop/next.config.mjs`) — `nosniff`, `DENY`, strict referrer policy, and
  `camera=(), microphone=(), geolocation=()`.
- **Friendly errors** — the copilot returns a clean message on upstream failures instead of leaking
  exception text to the shopper.

---

## Schema

`init_db()` runs at import time and owns every table. It uses
`CREATE TABLE IF NOT EXISTS` and then **diffs `information_schema`** to `ALTER TABLE ADD COLUMN`
whatever an existing database is missing — because `IF NOT EXISTS` is a no-op on an existing table,
and a silently missing column reads like a broken query rather than a missing migration. It also drops
the legacy v1 demo table (4 hand-written rows, no `asin`) once.

```sql
products (
  id INTEGER PRIMARY KEY, asin TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL, brand TEXT,
  price_usd NUMERIC(10,2) NOT NULL, price_idr INTEGER NOT NULL,
  department TEXT, category TEXT, category_path JSONB,
  description TEXT, features JSONB, image_url TEXT,
  avg_rating NUMERIC(3,2), rating_count INTEGER NOT NULL DEFAULT 0,
  also_buy JSONB, also_view JSONB,
  embedding vector(384), image_embedding vector(512)
)
events           (id BIGSERIAL, session_id, event_type, asin, query, results_count, qty, price_idr, created_at)
orders           (id BIGSERIAL, token UNIQUE, total_idr, item_count, status, session_id, created_at)
order_items      (id BIGSERIAL, order_id → orders, asin, title, qty, unit_price_idr)
item_recommendations (asin PRIMARY KEY, recs JSONB)
reviews          (id SERIAL, asin, rating, summary, comment, author, verified, review_date, created_at)
store_knowledge  (id SERIAL, category, title, content, embedding vector(384))
```

Indexes: `products(department)`, `products(category)`, HNSW on `products.embedding` and
`products.image_embedding`, HNSW on `store_knowledge.embedding`, plus events/orders/reviews indexes.

`pgvector` is enabled defensively: if the extension is unavailable the API still boots, just without
an `embedding` column (search is disabled rather than crashing).

---

## Testing

```bash
# Pure unit tests — no database needed (also what CI runs)
python3 -m pytest tests/test_search_unit.py

# Full API suite — needs a running Postgres (docker compose up), the champion vision ONNX for image tests, and a Gemini key for the copilot tests
python3 -m pytest tests/
```

**76 test cases** across 9 files, grouped by concern:

| Suite | Cases | Covers |
|---|:---:|---|
| `test_search_unit.py` | 11 | stemmer, tokenizer, BM25 scoring, dedupe, facet filtering |
| `test_search_api.py` | 12 | modes, semantic/SKU queries, plural equivalence, filters, reindex |
| `test_catalog_api.py` | 9 | health, facets, pagination boundaries, detail 404/422 |
| `test_checkout_and_events_api.py` | 14 | valid/invalid events, **price-tampering rejection**, duplicate-ASIN aggregation, order lookup |
| `test_copilot_api.py` | 11 | tool registry, greeting & off-topic guardrails, the 7 shopping/ops use cases |
| `test_recs_api.py` | 6 | popular, department filter, item CF + fallback, session cold vs warm |
| `test_reviews_api.py` | 5 | review aggregates, limits, 404s |
| `test_search_image_api.py` | 4 | valid image, department filter, bad MIME, tiny file |
| `test_agent_tools_real.py` | 4 | real-tool grounding against the live DB |

CI (`.github/workflows/ci.yml`) runs the offline-safe subset (`test_search_unit.py` +
`pipelines/tests/test_pipeline_math.py`) because hosted runners have no Postgres or Gemini key.

---

## Design decisions & known gaps

**Decisions**

- **Offline seeding.** The API never writes catalog rows; a pipeline does. Request handling stays
  independent of data generation.
- **BM25 by hand.** A baseline is only useful if it is real enough to beat, and at this catalog size an
  in-process index is faster to operate than a service.
- **RRF over score normalization.** Removes per-ranker calibration entirely.
- **Rerank as an optional stage.** Best quality when available, Stage-1 order when not.
- **Server owns the price.** The single most important security property of the demo.
- **Anonymous session ids.** Funnel attribution without accounts or PII.
- **Honest nulls and caveats.** Missing denominators return `null`; the funnel returns its own caveats.

**Known gaps (deliberately visible)**

- No owner authentication; access is by unguessable token/URL.
- `/checkout/confirm` is not idempotent (no `Idempotency-Key` yet).
- Client-side filtering means `results_count` is not catalog-wide.
- No Alembic; `init_db()` is additive, not a versioned migration system.
- Reindex is manual after a reseed.
- The copilot is bound to 4 tools. `get_product_reviews` exists as a helper but is not advertised to
  the planner, and there is intentionally **no order-status tool** (checkout is a simulation).
- Render free tier cold-starts (see the cold-start overlay in the storefront).

---

### Related docs

- [`../README.md`](../README.md) — project overview, research study, architecture
- [`../pipelines/README.md`](../pipelines/README.md) — dataset provenance and offline pipelines
- [`../shop/README.md`](../shop/README.md) — storefront internals
