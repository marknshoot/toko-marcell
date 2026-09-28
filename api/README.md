# `manual/api` — Toko Marcell backend (FastAPI + Postgres)

Marcell's own backend, built from scratch. It owns **everything the frontend must not do**:
REST validation, the catalog, and (next) search, recommendations, events, checkout
confirmation and the shopping copilot.

```text
Browser → Next.js (manual/shop) → REST/JSON → FastAPI (this) → Postgres
```

**Hard rule:** Next never opens a database connection. The frontend only ever talks HTTP.

---

## Run it

```bash
cd manual/api
docker compose up -d --build      # api on :8001, Postgres on :5432 (pgvector:pg16)
curl -s localhost:8001/health     # {"status":"ok"}
```

Interactive API docs: **http://localhost:8001/docs**

## Seed the real catalog

The API creates the schema but never writes catalog rows — that is an **offline** job
(PLAN.md §3), so `docker compose up` stays a server and the seed is a separate tool.

```bash
# 1. build the dataset (once, ~8 min) — see ../pipelines/README.md
cd manual && python3 pipelines/build_catalog.py

# 2. load it into Postgres
cd api
docker compose run --rm seed              # upsert 6,000 products by asin
docker compose run --rm seed --reset      # wipe + reload (use after regenerating)
```

The `seed` service is behind a compose profile, so it never starts with `up`.

---

## Endpoints

| Method | Path | Notes |
|---|---|---|
| `GET` | `/health` | Liveness and cold-start server wakeup ping |
| `GET` | `/categories` | Facets with real counts: departments + top 24 categories (in-memory TTL cache ~2ms) |
| `GET` | `/products` | Paginated catalog (`limit`, `offset`, `department=`, `category=`) |
| `GET` | `/products/{id}` | Single product details · `404` missing · `422` invalid id |
| `GET` | `/search` | 3-way hybrid search: BM25 + dense text vectors + CLIP text with RRF ($k=60$) |
| `POST` | `/search/image` | Visual similarity search by uploading image bytes via CLIP ViT-B/32 |
| `POST` | `/search/reindex` | Rebuild in-memory BM25 index & refresh pgvector caches |
| `GET` | `/recs/popular` | Popularity baseline recommendations (Bayesian smoothed) |
| `GET` | `/recs/item/{asin}` | Item-to-item co-occurrence & category-aware collaborative recommendations |
| `GET` | `/recs/session` | Real-time session-based recommendations from user browsing history |
| `GET` | `/reviews/{asin}` | Real customer reviews and ratings for a given ASIN |
| `POST` | `/copilot/chat` | Tri-Modal LangChain Agent Copilot (planner, tool execution, RAG, styling) |
| `GET` | `/copilot/tools` | Whitelisted copilot tool definitions & schemas |
| `POST` | `/events` | Record user funnel event (`view`, `cart`, `search`) → `201` |
| `GET` | `/events/summary` | Funnel conversion KPIs computed from PostgreSQL events table |
| `POST` | `/checkout/confirm` | Create paid order with secret token → `201` · `422` bad payload |
| `GET` | `/orders/{token}` | Retrieve paid order receipt · `404` unknown token |

`/products` returns an envelope, never a bare array:

```json
{ "items": [ ... ], "total": 4670, "limit": 24, "offset": 0 }
```

A product is the shape the storefront renders (`camelCase`, numbers as numbers):

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

### Products in the catalog today

4,670 curated, deduplicated real products from the Amazon Reviews 2018 *Clothing, Shoes & Jewelry* dataset, with 46,700 real customer reviews and precomputed 384-dim (sentence) and 512-dim (CLIP) vector embeddings.
Full provenance: [`../pipelines/README.md`](../pipelines/README.md).

---

## Schema

```sql
products (
  id            INTEGER PRIMARY KEY,   -- stable int, ordered by popularity
  asin          TEXT NOT NULL UNIQUE,  -- the real product id; the ML/event key
  title         TEXT NOT NULL,
  brand         TEXT,
  price_usd     NUMERIC(10,2) NOT NULL,  -- source price, untouched
  price_idr     INTEGER NOT NULL,        -- shop display price (fixed demo rate)
  department    TEXT NOT NULL DEFAULT 'Other',
  category      TEXT NOT NULL DEFAULT '',
  category_path JSONB NOT NULL DEFAULT '[]',
  description   TEXT,
  features      JSONB NOT NULL DEFAULT '[]',   -- NLP/search text
  image_url     TEXT,
  avg_rating    NUMERIC(3,2),
  rating_count  INTEGER NOT NULL DEFAULT 0,    -- popularity signal
  also_buy      JSONB NOT NULL DEFAULT '[]',   -- co-view signal for recs
  also_view     JSONB NOT NULL DEFAULT '[]'
)
```

Notes and deliberate choices:

- `price_idr` is what the shop shows; `price_usd` keeps the dataset value so the
  conversion is always auditable. The rate is fixed (`--usd-idr 16000`) and documented —
  it is a demo conversion, not a live rate.
- `id` vs `asin`: ints for the shop and the cart, ASIN for events, recs and joins with
  `interactions.csv.gz`.
- Emptiness is allowed to be honest: 4,717 of 6,000 products have a description and
  5,321 have a brand. Nothing is invented to fill a field.
- **Migrations:** the schema is created with `CREATE TABLE IF NOT EXISTS` plus one
  one-time `DROP` of the legacy v1 4-row demo table (logged on startup). A real migration
  tool (Alembic) is still a MUST item — see PLAN §5 B3. Until then, `init_db()` is the
  single place that owns the schema.

## Events (funnel, M2/M2b)

One row per shopper action. `session_id` groups the actions of one visit — it is an
**anonymous id generated in the browser** (`sessionStorage` + `crypto.randomUUID()`),
**not a login**. No accounts exist in v1 (BRD BR-9) and none are needed: this id is the
only identity the funnel requires, and it identifies a visit, not a person. No names, no
emails, no PII.

Allowed `event_type` values — anything else is rejected with `422`:

| Event | Fired by | Extra fields |
|---|---|---|
| `view_product` | `TrackView` on the PDP mount | `asin` |
| `search` | debounced query in the catalog | `query`, `results_count` |
| `add_to_cart` | `CartProvider.addToCart` (covers every entry point) | `asin`, `qty`, `price_idr` |
| `checkout_start` | first render of `/checkout` with a non-empty cart | `qty`, `price_idr` |
| `purchase_mock` | "I have paid" click on `/checkout/qris` | `qty`, `price_idr` |

```bash
curl -s -X POST localhost:8001/events -H 'Content-Type: application/json' \
  -d '{"event_type":"view_product","session_id":"sess-abc12345","asin":"B000YXC2LI"}'
curl -s localhost:8001/events/summary | python3 -m json.tool
```

The frontend posts events **fire-and-forget**: `postEvent()` never throws and the UI never
waits on it, because losing a data point is cheaper than breaking a purchase. It uses
`keepalive: true` so a request still completes when the page navigates away in the same tick
(add-to-cart then redirect, or "I have paid" → success).

### Checkout (B5 — the authoritative step)

Before this existed, "paid" was a claim the browser made, and `/checkout/success` was a
**static page that confirmed an order to anyone who typed the URL**. Now the truth is a row
in `orders`, and the confirmation page can only display what the server holds.

```bash
curl -s -X POST localhost:8001/checkout/confirm -H 'Content-Type: application/json' \
  -d '{"items":[{"asin":"B000YXC2LI","qty":2}],"session_id":"sess-abc12345"}'
# → {"token":"...","status":"paid","total_idr":989400,"item_count":2,"items":[...]}

curl -s localhost:8001/orders/<token>
```

**The rule that matters: the client sends `{asin, qty}` and never a price.** The server looks
up `products.price_idr` for every line and computes the total itself, so a tampered request
cannot "pay" Rp 1. `ConfirmIn` sets `extra="forbid"`, which means a request that *tries* to
send a total is rejected with a `422` instead of being silently ignored — the rule is
enforced at the edge and visible in the logs.

Other consequences of putting the order on the server:

- The page `/checkout/confirm/[token]` **fetches the order**, so it survives a refresh, a new
  tab and another device. Without a valid token it renders a 404, not a fake receipt.
- `purchase_mock` is now written **by the server inside the confirm handler**, because the
  server is the only party that knows the order exists. The funnel's last step is therefore
  authoritative, not browser-asserted.
- `session_id` is **required** on confirm. A purchase that cannot be attributed to a visit is
  useless to the funnel, and `events.session_id` is `NOT NULL` — allowing null produced a
  500 from the database layer where a 422 belonged.
- The cart line gained `asin` for this, so the localStorage key moved to `toko-cart-v2`. A v1
  cart line has no asin and therefore cannot be priced; the key bump is honest where a silent
  migration would have produced unpriceable lines.

Known simplification: **confirm is not idempotent.** A double-click would create two orders.
The button disables while the request is in flight, but a real system wants an
`Idempotency-Key` header. Worth saying out loud rather than discovering in a demo.

## How to read the funnel numbers honestly

`/events/summary` returns its own `caveats` array. Three matter:

1. **`search_to_pdp` is a session-level proxy** — a session that both searched and viewed a
   product. The product *click* is not recorded as its own event yet, so it is not a
   click-through rate.
2. **`results_count` for `search` counts matches among the products currently loaded**, not the
   whole catalog, because filtering is still client-side. Zero-result rate becomes a real
   metric only when `/search` moves to the server (Phase 5).
3. `purchase_mock` is no longer browser-asserted — it is written by the confirm handler. This
   one is now trustworthy, which is why it is worth keeping the other two visible.

Rates are `null`, never `0.0`, when a denominator is missing — an empty events table cannot
produce a misleading "0% conversion".

## Search (M3/M4 — the lexical baseline)

`GET /search?q=…` ranks the catalog with **BM25** over title, brand, category, department,
features and description. It is in-process and hand-written (`search.py`, no dependency): the
plan asks for an in-process BM25 baseline (PLAN §6), and a baseline is only worth having if it
is *real*, because the hybrid ranker has to beat something honest.

| | |
|---|---|
| Scoring | Robertson & Zaragoza BM25, `k1=1.5`, `b=0.75`, IDF with `+1` smoothing |
| Fields | weighted term frequencies: title 3.0 · brand 2.0 · category 1.5 · department 1.0 · features 1.0 · description 0.5 |
| Tokenizer | lowercase → accent folding → alphanumeric → stopwords → plural folding |
| Index | built lazily on the first search (1.5 s for 6,000 docs), then ~17–50 ms per query |
| Response | `{items, total, limit, offset, query, mode, deduped, took_ms}` + a `score` per item |
| Filters | `department=`, `category=` — resolved to a set of ids before ranking |

`mode` is part of the response because this endpoint is meant to become **hybrid**
(BM25 + embeddings): the UI and the eval harness should be able to tell which ranker produced
a result set without inferring it from a deployment.

**Duplicate titles are removed** (`deduped: true`). 842 of the 6,000 products (14%) are
variants sharing a title with a different ASIN, so without this a query like `jeans` returns
the same title twice inside the top 10. Dedupe happens *before* pagination, so `total` counts
results a shopper can actually reach. Fuzzy near-duplicate handling belongs to the diversity@k
step, not here.

`POST /search/reindex` rebuilds the in-memory index — needed after
`docker compose run --rm seed`, because the index lives for the life of the process.

### What BM25 does and does not do (measured)

```
'levis 501'                 64 results   → the actual 501s, four distinct variants
'waterproof jacket'        351 results   → raincoats and watertight shells
'shoes for women'         3543 results   → women's shoes
'white sneakers'           511 results   → canvas sneakers, Converse
'zzzz'                       0 results   → 0.05 ms
```

Working: exact terms and product IDs, attribute words, plurals (`shoes` and `shoe` score
identically after folding).

Not working, by construction: typos, synonyms, and conversational queries. That gap is exactly
what the embedding half of hybrid search adds — which is why the baseline is measured *first*,
so the improvement becomes a number rather than a claim.

## Not built yet (planned, in order)

Search · embeddings half of hybrid search (pgvector, `vector(384)` column is already in the
schema) · the ≥30-query eval with Recall@10 / nDCG@10 against this BM25 baseline (M5–M6) ·
recommendations (`/recs`, cold-start → popular) · copilot with tools. All of them live here,
in Python — never in Next.

---

## Reliability, Caching & Defensive Security Architecture

Designed to operate robustly on free-tier cloud infrastructure (Render + Neon Postgres + Vercel):

### 1. Multi-Tier Caching
- **Edge CDN Caching (Next.js ISR):** Product detail routes export `revalidate = 300` (5 minutes) for automatic stale-while-revalidate caching on Vercel's global Edge CDN, reducing TTFB to <40ms.
- **HTTP Cache-Control Headers:** Public catalog endpoints (`/products`, `/products/{id}`, `/categories`) emit `public, max-age=60, s-maxage=300, stale-while-revalidate=60` headers.
- **In-Memory TTL Cache:** `/categories` facets query uses an in-memory cache with 300s TTL, offloading repetitive `GROUP BY` aggregate scans from Neon PostgreSQL (benchmarked latency drops from ~60ms to <3ms). Re-indexing (`/search/reindex`) automatically invalidates this cache.

### 2. Defensive Security & Resource Safeguards
- **AI Token Safeguard:** `/copilot/chat` enforces strict Pydantic payload boundaries (`max_length=2000` chars per message, max 20 messages in conversation history, max 8MB image payload) to prevent prompt abuse and token depletion on Gemini API quotas.
- **Origin-Locked CORS:** Restricted to trusted production and staging origins (`localhost` + `*.vercel.app`), preventing third-party domain hijack.
- **Reverse Proxy IP Forwarding:** Uvicorn runs with `--proxy-headers` for accurate client IP resolution behind Render's reverse proxy.
- **HTTP Security Headers:** Frontend enforces `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, and restricted `Permissions-Policy`.
