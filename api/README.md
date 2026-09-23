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
| `GET` | `/health` | liveness |
| `GET` | `/products` | paginated catalog. `limit` 1–24 (default 24), `offset` ≥ 0, `department=`, `category=` |
| `GET` | `/products/{id}` | one product · `404` missing · `422` id not an integer |
| `GET` | `/categories` | facets with real counts: departments + top 24 category names |
| `POST` | `/events` | record one funnel event → `201` · `422` if malformed |
| `GET` | `/events/summary` | funnel KPIs computed from the events table. `since_days` (default 30) |

`/products` returns an envelope, never a bare array:

```json
{ "items": [ ... ], "total": 6000, "limit": 24, "offset": 0 }
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

6,000 real products from the Amazon Reviews 2018 *Clothing, Shoes & Jewelry* dataset:
Women 3,405 · Men 1,897 · Other 307 · Girls 152 · Boys 128 · Baby 111.
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

### How to read the numbers honestly

`/events/summary` returns its own `caveats` array. Two matter:

1. **`search_to_pdp` is a session-level proxy** — a session that both searched and viewed a
   product. The product *click* is not recorded as its own event yet, so it is not a
   click-through rate.
2. **`purchase_mock` is browser-asserted.** Until checkout is confirmed server-side (B5),
   "paid" is a claim from the client. That is why the QRIS screen says *demo*.
3. **`results_count` for `search` counts matches among the products currently loaded**, not the
   whole catalog, because filtering is still client-side. Zero-result rate becomes a real
   metric only when `/search` moves to the server (Phase 5).

Rates are `null`, never `0.0`, when a denominator is missing — an empty events table cannot
produce a misleading "0% conversion".

## Not built yet (planned, in order)

Search (`/search`, hybrid BM25 + embeddings) · recommendations (`/recs`, cold-start →
popular) · server-side checkout confirm (B5) · copilot with tools. All of them live here, in
Python — never in Next.
