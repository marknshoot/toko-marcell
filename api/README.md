# Toko Marcell API — Technical Reference

FastAPI service for bilingual product search, deterministic sizing, recommendations, the LangGraph
sales copilot, and the operations KPI dashboard.

| | |
|---|---|
| **Runtime** | Python 3.12, FastAPI, Uvicorn |
| **Database** | PostgreSQL 16 + pgvector (HNSW cosine indexes) |
| **Embeddings** | `fastembed` ONNX — 384-d multilingual MiniLM |
| **Agent** | LangChain v1 `create_agent` on LangGraph, checkpointed in PostgreSQL |
| **Migrations** | Alembic, hand-written SQL, head revision `0005` |
| **Tests** | pytest (unit + integration), Ruff |

---

## Contents

- [Architecture](#architecture)
- [Request lifecycle](#request-lifecycle)
- [Module map](#module-map)
- [REST API](#rest-api)
- [Key endpoint contracts](#key-endpoint-contracts)
- [Search internals](#search-internals)
- [Store knowledge RAG](#store-knowledge-rag)
- [Copilot and tools](#copilot-and-tools)
- [Environment variables](#environment-variables)
- [Database schema](#database-schema)
- [Operations](#operations)
- [Testing](#testing)

---

## Architecture

```mermaid
flowchart TB
    FE[Next.js storefront] -->|HTTP / JSON| API[FastAPI]
    API --> SEARCH[search.py<br/>BM25 + fastembed + RRF]
    API --> SIZING[sizing.py<br/>deterministic charts]
    API --> RECS[routers/recs.py]
    API --> AGENT[copilot_agent.py<br/>LangGraph]
    AGENT --> TOOLS[copilot_tools.py<br/>7 deterministic tools]
    AGENT --> RAG[knowledge_seed.py<br/>store knowledge retrieval]
    SEARCH --> PG[(PostgreSQL 16<br/>pgvector)]
    SIZING --> PG
    RECS --> PG
    RAG --> PG
    AGENT --> PG
    API --> KPI[routers/admin.py<br/>/admin/kpi]
    KPI --> PG
```

Design rules:

- **Raw SQL over an ORM.** Queries are explicit `psycopg` statements; SQLAlchemy exists only as
  Alembic's migration connection layer. Column and table names are composed with
  `psycopg.sql.Identifier` / `sql.SQL`, never interpolated from user input.
- **Business invariants live in code.** Prices are read server-side, outfit totals are summed and
  validated in Python, and sizing refuses to guess.
- **Every degraded path is explicit.** Missing LLM key → `503`; an unset legacy `ADMIN_TOKEN` disables
  `/search/reindex` with `503`; empty denominator → `null` in metrics rather than a fabricated `0.0`.
- **Startup is self-healing.** `init_db()` runs `alembic upgrade head` and seeds the knowledge base
  when it is empty or stale. Idempotent revisions adopt pre-Alembic databases without a manual stamp.

---

## Request lifecycle

1. **Startup (`main.lifespan`)** — configure logging, run migrations, seed knowledge, open the
   `psycopg_pool` connection pool, ensure copilot checkpoint tables, start daily thread TTL cleanup.
2. **Middleware** — permissive CORS for `CORS_ORIGINS` plus `*.vercel.app` previews; proxy trust is
   opt-in through `TRUST_PROXY` so rate limiting has the correct client IP.
3. **Routing** — FastAPI validates path/query/body models. Pydantic rejects unknown fields on write
   contracts (`extra="forbid"`) so typos fail loudly.
4. **Data access** — pooled psycopg connections (`DB_POOL_MIN` / `DB_POOL_MAX`).
5. **Response** — JSON. Collections are wrapped in objects (`{"items": [...]}`), never bare arrays, so
   fields such as `total`, `strategy`, or `caveat` can be added without a breaking change.

---

## Module map

| Path | Responsibility |
|---|---|
| `main.py` | App factory, lifespan, CORS, router registration |
| `config.py` | Cached `Settings` (pydantic-settings), all environment variables |
| `db.py` | Connection pool and dependency helpers |
| `migrate.py` | Alembic programmatic runner |
| `search.py` | BM25 index, multilingual embeddings, RRF, ID→EN query rewrite |
| `sizing.py` | Category → size-kind resolution and deterministic size advice |
| `reranker.py` | Optional cross-encoder stage-2 reranker |
| `schemas.py` | Pydantic request/response contracts |
| `agent_tools.py` | Copilot-facing catalog search and sizing adapters |
| `copilot_agent.py` | LangGraph state machine, prompt, turn recording |
| `copilot_llm.py` | Provider chain (OpenRouter → generic fallback → Gemini) |
| `copilot_memory.py` | `PostgresSaver` checkpointer and thread TTL cleanup |
| `copilot_tools.py` | Seven deterministic LangChain tools |
| `copilot_metrics.py` | Token/cost estimation and turn telemetry |
| `guardrail.py` | Regex + LLM classification: greeting, off-topic, injection, on-topic |
| `knowledge_seed.py` | Knowledge chunking, hypothetical questions, embedding cache, seeding |
| `rate_limiter.py` | Per-IP copilot limits and write limits |
| `routers/` | HTTP endpoints grouped by domain |

---

## REST API

Base URL locally: `http://localhost:8001`. Interactive OpenAPI docs: `/docs`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness probe; also used by the storefront cold-start check |
| `GET` | `/categories` | Department and category facets with counts (5-minute cache) |
| `GET` | `/products` | Paginated catalog, optional `department` / `category` filter |
| `GET` | `/products/{product_id}` | Single product; `404` when missing, `422` for invalid id |
| `GET` | `/products/{product_id}/reviews` | Reviews and rating breakdown |
| `GET` | `/search` | Hybrid, dense, or BM25 retrieval with filters and pagination |
| `GET` | `/search/insights` | Search quality: top queries, zero-result queries, search-to-PDP rate |
| `POST` | `/search/reindex` | Rebuild the in-memory BM25 index and clear facet cache (admin) |
| `GET` | `/sizing/{asin}` | Size kind and required measurements for a product |
| `POST` | `/sizing/recommend` | Deterministic size recommendation from body measurements |
| `GET` | `/recs/popular` | Department popularity baseline |
| `GET` | `/recs/item/{asin}` | Item-to-item recommendations with popularity fallback |
| `GET` | `/recs/session` | Recommendations from the current anonymous session |
| `POST` | `/copilot/chat` | One agentic copilot turn |
| `POST` | `/copilot/chat/stream` | Same turn streamed as Server-Sent Events |
| `GET` | `/copilot/tools` | Names of the seven deterministic tools |
| `POST` | `/copilot/feedback` | Thumbs up/down for a thread |
| `GET` | `/copilot/threads/{thread_id}` | Replay a thread's display messages |
| `DELETE` | `/copilot/threads/{thread_id}` | Delete checkpointed conversation state |
| `POST` | `/events` | Record a funnel event |
| `GET` | `/events/summary` | Funnel, revenue, cohort, and unit-economics summary |
| `POST` | `/checkout/confirm` | Create a paid order from server-priced items |
| `GET` | `/orders/{token}` | Read an order by its secret token |
| `GET` | `/admin/kpi` | Bearer-JWT-gated executive KPI dashboard payload (legacy token fallback) |

Common status codes:

| Code | Meaning |
|---|---|
| `200` / `201` / `202` | Success / created / accepted |
| `204` | Deleted |
| `400` | Invalid request semantics |
| `401` | Missing/invalid admin credentials |
| `403` | Invalid legacy admin token (`/search/reindex`) |
| `404` | Unknown product, ASIN, order, or thread |
| `422` | Schema validation failure (FastAPI/Pydantic) |
| `429` | Rate limited; `Retry-After` is set |
| `503` | Feature disabled: legacy admin token unset for reindex, no LLM key, or model unavailable |

---

## Key endpoint contracts

### `GET /search`

Hybrid retrieval over the product catalog: BM25 + 384-d multilingual dense vectors fused with RRF.

| Query parameter | Type | Default | Notes |
|---|---|---:|---|
| `q` | string | required | 1–100 characters |
| `mode` | enum | `hybrid` | `hybrid`, `dense`, `bm25` (`vector` = deprecated alias for `dense`) |
| `limit` | int | 24 | 1–24 |
| `offset` | int | 0 | Pagination offset |
| `department` | string | — | Exact facet filter |
| `category` | string | — | Exact facet filter |
| `rerank` | bool | false | Applies the cross-encoder only when `ENABLE_RERANKER=true` |

```bash
curl -s "http://localhost:8001/search?q=sepatu%20running&limit=3"
```

```json
{
  "items": [
    {
      "id": 3398,
      "asin": "B004BUZF9G",
      "title": "Nike Women's Free Running Shoe",
      "brand": "NIke",
      "priceUsd": 49.99,
      "priceIdr": 799800,
      "department": "Women",
      "category": "Running",
      "categoryPath": ["Women", "Running"],
      "description": "...",
      "features": ["..."],
      "imageUrl": "https://...",
      "avgRating": 4.45,
      "ratingCount": 304,
      "alsoBuy": [],
      "alsoView": [],
      "score": 0.032
    }
  ],
  "total": 113,
  "limit": 3,
  "offset": 0,
  "query": "sepatu running",
  "mode": "hybrid",
  "reranked": false,
  "deduped": true,
  "rewritten": false,
  "fallback_query": null,
  "original_query": null,
  "took_ms": 42.71
}
```

Behavior worth knowing:

- Results are deduplicated by normalized title **before** pagination.
- A zero-result query is retried once with a deterministic Indonesian → English rewrite. When that
  succeeds, `rewritten=true` and `fallback_query` carries the translated query.
- A query containing only stopwords returns an empty page with `200`, not an error.
- `score` is rank-fusion evidence, not a probability. Compare rankings, not raw values across modes.

### `GET /search/insights`

Aggregates the `events` table over a window (`since_days`, default 30).

```json
{
  "since_days": 30,
  "total_searches": 1420,
  "zero_result_searches": 97,
  "zero_result_rate": 0.0683,
  "top_zero_result_queries": [{"query": "kemeja linen", "count": 9}],
  "top_queries": [{"query": "sepatu running", "count": 84, "zero_result": false}],
  "search_to_pdp_rate": 0.4127
}
```

`search_to_pdp_rate` is session-level: distinct sessions with a search event divided into sessions
with both a search and a `view_product` event.

### `POST /sizing/recommend`

Deterministic, zero-LLM sizing. Only the measurement required by the product's size kind is needed.

```bash
curl -s -X POST http://localhost:8001/sizing/recommend \
  -H 'Content-Type: application/json' \
  -d '{"asin":"B000YPIEXQ","foot_length_cm":27.0}'
```

```json
{
  "asin": "B000YPIEXQ",
  "applicable": true,
  "size_kind": "footwear",
  "recommendation": "... tabel standar mengarah ke US 9 / UK 8 / EU 42.5.",
  "suggested": {"foot_length_cm": 27.0, "us": 9.0, "uk": 8.0, "eu": 42.5},
  "source": "standard",
  "confidence": "high",
  "required_inputs": ["foot_length_cm"],
  "disclaimer": "Panduan ukuran ini adalah estimasi umum Toko Marcell...",
  "reason": null
}
```

Request bounds (body-plausible, `extra="forbid"`):

| Field | Range |
|---|---|
| `height_cm` | 50–250 |
| `weight_kg` | 10–400 |
| `foot_length_cm` | 10–40 |
| `waist_cm` | 30–200 |
| `head_circumference_cm` | 30–100 |

Missing inputs or unsupported categories return `applicable=false` plus `required_inputs` — the
engine never invents a size. `GET /sizing/{asin}` returns the same `size_kind` contract before any
measurements are supplied.

### `GET /recs/item/{asin}`

Item-to-item recommendations for the product page and cart.

```json
{
  "asin": "B000YXC2LI",
  "items": [
    {"id": 204, "asin": "B000ABC123", "title": "...", "priceIdr": 349000, "imageUrl": "..."}
  ],
  "count": 4,
  "strategy": "item_cf"
}
```

If the offline table is exhausted, `strategy` becomes `category_popularity_fallback` and the endpoint
fills the response with same-category then department-popular products. A cold or unknown ASIN still
returns a usable list rather than an error.

### `POST /copilot/chat`

One agentic turn. History is stored in the LangGraph checkpointer, not sent by the client.

```bash
curl -s -X POST http://localhost:8001/copilot/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "session_id": "session-12345678",
    "thread_id": "thread-abc123",
    "message": "Rekomendasi outfit kerja pria budget 500 ribu",
    "context": {"page_asin": null, "referenced_asins": []}
  }'
```

```json
{
  "reply": "Berikut outfit kerja yang muat di budget Rp500.000 ...",
  "thread_id": "thread-abc123",
  "products": [
    {"ref": 1, "asin": "B000ABC123", "title": "...", "priceIdr": 189000, "imageUrl": "..."}
  ],
  "ui_actions": [],
  "tool_calls": [{"name": "build_outfit", "args": {"budget_idr": 500000}}],
  "guardrail": null,
  "took_ms": 1840.22
}
```

- `guardrail` is `greeting`, `off_topic`, or `injection` when the turn short-circuited; those replies
  never call the LLM tools.
- `ui_actions` contains server-validated `add_to_cart` actions. The browser executes them; the model
  cannot set a price.
- `products` is capped at six and uses stable `ref` numbers so follow-ups can say "yang kedua".
- `503` when no LLM provider is configured. `429` with `Retry-After` when rate-limited.
- `POST /copilot/chat/stream` emits `token` events followed by one `done` event with the same payload.

### Admin authentication

The dashboard uses Argon2id passwords plus a dual-token session:

| Endpoint | Purpose |
|---|---|
| `POST /auth/login` | Verify `{username, password}`; returns a 15-minute access JWT and sets an HttpOnly `refresh_token` cookie (`Path=/auth`, `SameSite=Lax`, `Secure` over HTTPS). |
| `POST /auth/refresh` | Rotate the refresh token (old row marked `revoked`) and return a new access JWT. Replaying an already-revoked token revokes every active token for that user. |
| `POST /auth/logout` | Revoke the presented refresh token and clear the cookie with `Max-Age=0`. |
| `GET /auth/me` | Return the authenticated admin's database identity (`id`, `username`, `role`). |

The demo seed creates `admin` / `marcell-admin-2026`. Refresh tokens are opaque random values; only
their SHA-256 digest is stored in `admin_refresh_tokens`. Set `JWT_SECRET_KEY` to a 32+ byte secret
in production (`openssl rand -hex 32`).

```bash
curl -s -c cookies.txt -X POST http://localhost:8001/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"marcell-admin-2026"}'
```

### `GET /admin/kpi`

Executive dashboard payload. Requires `Authorization: Bearer <access_token>`. The legacy
`X-Admin-Token: <ADMIN_TOKEN>` header and `?token=` query parameter remain accepted for scripts.

```bash
curl -s "http://localhost:8001/admin/kpi?since_days=30" \
  -H "Authorization: Bearer $ACCESS_TOKEN"
```

Top-level sections:

| Section | Contents |
|---|---|
| `metadata` | Window, generated timestamp, `synthetic`, `environment`, caveat |
| `revenue` | Order count, revenue in IDR, AOV |
| `funnel` | Session counts and view→cart→checkout→order rates |
| `cohorts` | Copilot vs non-copilot sessions, conversion, AOV, caveat |
| `unit_economics` | Total cost, cost per turn/conversation/resolved conversation/assisted order |
| `search_health` | Search volume, zero-result rate, search-to-PDP, top queries |
| `copilot_quality` | Outcomes, unanswered rate, deflection rate, p50/p95 latency, thumbs ratio |

`401` when neither a valid Bearer JWT nor a matching legacy token is supplied. Metrics are
explicitly marked as synthetic demo data — the endpoint never presents simulated revenue as audited
revenue.

---

## Search internals

### BM25

`search.py` implements Robertson & Zaragoza BM25 (`k1=1.5`, `b=0.75`) over the 4,670-product catalog.
The index lives in process and rebuilds in under a second; there is no external search service.

- Field weights: title 3.0, brand 2.0, category 1.5, department 1.0, features 1.0, description 0.5.
- Tokenization: lowercase → NFKD accent folding → alphanumeric tokens → stopword removal → plural
  folding.
- A deterministic Indonesian → English vocabulary handles multi-word phrases before single tokens
  (`celana jeans` → `jeans`, `sepatu lari` → `running shoes`).
- Documents are deduplicated by normalized title, because 842 of the 6,000 source products are
  variants that share a title.

### Dense retrieval

Queries are embedded with
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` through `fastembed` (quantized ONNX,
384 dimensions, `FASTEMBED_THREADS` pinned for small containers). The configured column
(`TEXT_EMBED_COLUMN`, default `embedding_ml`) is queried with cosine distance through a pgvector HNSW
index, returning the top 100 candidates.

### Reciprocal Rank Fusion

RRF merges rankings without score calibration:

```
score(d) = Σ 1 / (k + rank_i(d))     with k = 60
```

The hybrid path fuses the BM25 and dense top-100 lists, then deduplicates and paginates. If one
retriever fails or returns nothing, the other list is used directly rather than failing the request.

### Optional reranking

When `ENABLE_RERANKER=true` and `rerank=true`, a cross-encoder rescores the top `max(limit+offset, 50)`
fused candidates. It is disabled by default because the ONNX model is expensive on small instances;
the lexical + dense + RRF stack is the production path.

---

## Store knowledge RAG

`knowledge_seed.py` reads authored Markdown/JSON from `api/knowledge/` and maintains two tables:

- `store_knowledge` — **34 policy and sizing chunks**, each with a 384-d embedding.
- `store_knowledge_questions` — **120 hypothetical shopper questions** in Indonesian and English,
  each embedded and linked to its parent chunk.

Retrieval (`lookup_store_policy`) fuses three ranked lists with RRF (`k=60`):

1. Direct chunk-vector similarity.
2. Hypothetical-question-vector similarity, deduplicated to parent chunks.
3. IDF-weighted bilingual lexical keyword matching.

A 10-minute, 256-entry TTL cache absorbs repeated questions. The seed is skipped when
`SKIP_KNOWLEDGE_SEED=true`, and precomputed vectors in `knowledge_seed_cache.json` avoid recomputing
embeddings on every boot.

Evaluation uses RAGAS (`pipelines/eval_rag.py`) with ID-based and non-LLM context metrics at `k=3`:
**1.0000 Context Recall**, **0.9167 Context Precision** on 12 bilingual queries.

---

## Copilot and tools

`create_agent` runs on a LangGraph state machine. The graph holds message history in PostgreSQL via
`PostgresSaver`, keyed by the browser-generated `thread_id`; a sidecar activity table enables TTL
cleanup of abandoned threads.

### Guardrails

`guardrail.classify()` returns `(label, canned_reply)` or `(None, None)`:

| Label | Action |
|---|---|
| `injection` | Fixed refusal; never reaches the agent or tools |
| `greeting` | Fast-path canned greeting; no model call |
| `off_topic` | Fixed, polite scope deflection |
| `on_topic` | Proceeds to the agent loop |

`GUARDRAIL_LLM_MODE=always` uses the LLM classifier for every non-injection message;
`ambiguous` lets decisive regex labels short-circuit and only sends ambiguous messages to the LLM.
Trimming older turns from the classifier prompt prevents multi-turn injection from hiding in history.

### The seven tools

| Tool | Responsibility | Invariant |
|---|---|---|
| `search_catalog` | BM25 + multilingual dense search with filters | One ID→EN retry on empty results |
| `get_product_details` | Resolve a product by `ref`, ASIN, id, or title fragment | Reads the server-side price |
| `recommend_size` | Category-aware deterministic sizing | Returns `applicable=false` instead of guessing |
| `build_outfit` | Top + bottom + footwear within a budget | `total <= budget` summed and verified in code |
| `lookup_store_policy` | Policy and sizing RAG | Grounded citations from `store_knowledge` |
| `add_to_cart` | Emit a validated UI action | Price re-read from `products`; qty bounds enforced |
| `flag_unanswered` | Record an unanswerable question | Sets the turn outcome and backlog entry |

### Provider chain

1. **OpenRouter** (`OPENROUTER_API_KEY`, `LLM_MODEL`, `LLM_BASE_URL`, optional
   `LLM_FALLBACK_MODELS`).
2. **Generic OpenAI-compatible fallback** (`LLM_FALLBACK_BASE_URL`, `LLM_FALLBACK_API_KEY`,
   `LLM_FALLBACK_MODEL`).
3. **Google Gemini** (`GEMINI_API_KEY`, `GEMINI_MODEL`).

`COPILOT_FAKE_LLM=1` swaps in a deterministic fake tool-calling model for CI: no network, no key, and
stable assertions.

### Turn telemetry

Every turn writes to `copilot_turns`: guardrail label, outcome (`answered`, `guardrail`,
`unanswered`, `cart`, `error`), tools called, latency, token counts, model, estimated `cost_usd`, and
the `unanswered_question` when applicable. `GET /admin/kpi` and `GET /events/summary` aggregate these
rows into the unit-economics and quality panels.

---

## Environment variables

Settings are loaded by `config.Settings` from `../.env` and `.env`, with process environment
variables taking precedence. Unknown variables are ignored.

### Core

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql://toko:toko@localhost:5432/toko` | PostgreSQL + pgvector DSN |
| `DB_POOL_MIN` / `DB_POOL_MAX` | `1` / `5` | Connection pool bounds |
| `CORS_ORIGINS` | local + Vercel storefront | Comma-separated allowlist; `*` opens it |
| `ADMIN_TOKEN` | empty | Legacy static token for `/search/reindex` and `/admin/kpi` scripts |
| `JWT_SECRET_KEY` | dev placeholder | Signs 15-minute admin access JWTs; set a strong secret in production |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `15` | Access JWT lifetime |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | Rotating refresh-token lifetime |
| `TRUST_PROXY` | `false` | Trust `X-Forwarded-For` (set `true` behind Render) |
| `LOG_LEVEL` | `INFO` | Python logging level |

### Embeddings and search

| Variable | Default | Purpose |
|---|---|---|
| `TEXT_EMBED_MODEL` | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | 384-d multilingual model |
| `TEXT_EMBED_COLUMN` | `embedding_ml` | Vector column queried by `/search` and the copilot |
| `FASTEMBED_CACHE_DIR` | `/tmp/fastembed_cache` | ONNX model cache |
| `FASTEMBED_THREADS` | `1` | ONNX intra-op threads (memory control) |
| `HF_HUB_CACHE` | `/tmp/hf_cache` | Hugging Face cache for the optional reranker |
| `ENABLE_RERANKER` | `false` | Enable the cross-encoder stage-2 reranker |
| `SKIP_KNOWLEDGE_SEED` | `false` | Skip startup knowledge verification/seeding |

### Copilot

| Variable | Default | Purpose |
|---|---|---|
| `OPENROUTER_API_KEY` | empty | Preferred provider; empty falls through the chain |
| `LLM_MODEL` | `openrouter/free` | OpenRouter model id |
| `LLM_BASE_URL` | `https://openrouter.ai/api/v1` | OpenAI-compatible endpoint |
| `LLM_FALLBACK_MODELS` | empty | Comma-separated OpenRouter fallbacks for 429/503 |
| `LLM_SITE_URL` | storefront URL | OpenRouter attribution header |
| `LLM_FALLBACK_BASE_URL` / `_API_KEY` / `_MODEL` | empty | Generic OpenAI-compatible fallback |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | empty | Gemini fallback credentials |
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` | Gemini model id |
| `GUARDRAIL_LLM_MODE` | `always` | `always` or `ambiguous` classifier policy |
| `COPILOT_RATE_LIMIT_PER_MIN` | `10` | Per-IP requests per minute |
| `COPILOT_RATE_LIMIT_PER_DAY` | `100` | Per-IP requests per day |
| `COPILOT_FAKE_LLM` | unset | `1` enables the deterministic fake model |
| `LLM_INPUT_COST_PER_MILLION` | `0.15` | USD per 1M input tokens for cost estimates |
| `LLM_OUTPUT_COST_PER_MILLION` | `0.60` | USD per 1M output tokens for cost estimates |

---

## Database schema

Defined by Alembic revisions `0001` → `0005`. Revision `0005` drops the retired `products.image_embedding`
column and its HNSW index.

```mermaid
erDiagram
    products ||--o{ reviews : "asin"
    products ||--o{ item_recommendations : "asin"
    orders ||--o{ order_items : "order_id"
    store_knowledge ||--o{ store_knowledge_questions : "chunk_id"
    orders ||--o{ events : "session correlation"
```

### Catalog

**`products`**

| Column | Type | Notes |
|---|---|---|
| `id` | `INTEGER PK` | Internal product id |
| `asin` | `TEXT UNIQUE NOT NULL` | Amazon-style product key used across the app |
| `title` | `TEXT NOT NULL` | Display title; dedupe key in search |
| `brand` | `TEXT` | Brand facet |
| `price_usd` | `NUMERIC(10,2) NOT NULL` | Source price |
| `price_idr` | `INTEGER NOT NULL` | Server-authoritative IDR price |
| `department` | `TEXT NOT NULL` | Top-level facet |
| `category` | `TEXT NOT NULL` | Category facet |
| `category_path` | `JSONB` | Hierarchical path |
| `description` | `TEXT` | Long description (low BM25 weight) |
| `features` | `JSONB` | Bullet list |
| `image_url` | `TEXT` | Product image |
| `avg_rating` / `rating_count` | `NUMERIC(3,2)` / `INTEGER` | Popularity baseline |
| `also_buy` / `also_view` | `JSONB` | Source co-purchase signals |
| `embedding` | `vector(384)` | English MiniLM embedding (optional) |
| `embedding_ml` | `vector(384)` | Multilingual embedding (default search column) |

Indexes: `products_department_idx`, `products_category_idx`, HNSW cosine indexes on `embedding` and
`embedding_ml`.

### Funnel and orders

**`events`** — append-only shopper telemetry.

| Column | Type |
|---|---|
| `id` | `BIGSERIAL PK` |
| `session_id` | `TEXT NOT NULL` |
| `event_type` | `TEXT NOT NULL` (`view_product`, `search`, `add_to_cart`, `checkout_start`, `purchase_mock`, copilot events) |
| `asin`, `query` | `TEXT` |
| `results_count`, `qty`, `price_idr` | `INTEGER` |
| `created_at` | `TIMESTAMPTZ DEFAULT now()` |

Indexes: `events_session_idx`, `events_type_created_idx`.

**`orders`** — one row per confirmed order.

| Column | Type |
|---|---|
| `id` | `BIGSERIAL PK` |
| `token` | `TEXT UNIQUE NOT NULL` |
| `total_idr` | `INTEGER NOT NULL` |
| `item_count` | `INTEGER NOT NULL` |
| `status` | `TEXT NOT NULL DEFAULT 'paid'` |
| `session_id` | `TEXT` |
| `created_at` | `TIMESTAMPTZ DEFAULT now()` |

**`order_items`** — line items with the price snapshotted at purchase time (`order_id`, `asin`,
`title`, `qty`, `unit_price_idr`).

### Recommendations and reviews

**`item_recommendations`** — offline item-to-item output.

| Column | Type | Notes |
|---|---|---|
| `asin` | `TEXT PK` | Source product |
| `recs` | `JSONB` | Ordered ASIN neighbours |

**`reviews`** — `id`, `asin`, `rating`, `summary`, `comment`, `author`, `verified`, `review_date`,
`created_at`; indexed on `asin` and `rating`.

### Knowledge base

**`store_knowledge`**

| Column | Type |
|---|---|
| `id` | `SERIAL PK` |
| `category` | `TEXT NOT NULL` |
| `title` | `TEXT NOT NULL` |
| `content` | `TEXT NOT NULL` |
| `embedding` | `vector(384)` |

**`store_knowledge_questions`**

| Column | Type | Notes |
|---|---|---|
| `id` | `SERIAL PK` |
| `chunk_id` | `INTEGER FK → store_knowledge(id)` | `ON DELETE CASCADE` |
| `question` | `TEXT NOT NULL` | Hypothetical shopper question |
| `lang` | `TEXT NOT NULL DEFAULT 'id'` | `id` or `en` |
| `embedding` | `vector(384)` | Question vector |

Both tables carry HNSW cosine indexes; questions are additionally indexed by `chunk_id`.

### Copilot telemetry

**`copilot_turns`** — one row per turn.

| Column | Type | Notes |
|---|---|---|
| `id` | `BIGSERIAL PK` | |
| `thread_id`, `session_id` | `TEXT` | Attribution |
| `created_at` | `TIMESTAMPTZ DEFAULT now()` | |
| `guardrail` | `TEXT` | `greeting`, `off_topic`, `injection`, or `null` |
| `outcome` | `TEXT NOT NULL` | `answered`, `guardrail`, `unanswered`, `cart`, `error` |
| `tool_names` | `TEXT[]` | Tools invoked |
| `latency_ms` | `REAL` | End-to-end turn latency |
| `input_tokens`, `output_tokens` | `INTEGER` | Usage |
| `model` | `TEXT` | Provider/model id |
| `cost_usd` | `NUMERIC` | Estimated from configured token prices |
| `unanswered_question` | `TEXT` | Populated by `flag_unanswered` |

Indexes: `copilot_turns_created_idx`, `copilot_turns_thread_idx`.

**`copilot_feedback`** — `id`, `thread_id`, `session_id`, `rating` (`-1` or `1`), `created_at`.

### LangGraph checkpoints

Created at runtime by `PostgresSaver.setup()`, not by Alembic: `checkpoints`, `checkpoint_blobs`,
`checkpoint_writes`, plus the sidecar `copilot_thread_activity` used by the TTL cleanup job.

---

## Operations

### Caching

| Layer | TTL | Scope |
|---|---|---|
| BM25 index | Process lifetime | Rebuilt lazily; reset by `POST /search/reindex` |
| Category facets | 5 minutes + HTTP cache | `GET /categories` |
| Policy retrieval | 10 minutes / 256 entries | `lookup_store_policy` |
| Storefront catalog | Next.js ISR / edge cache | Product listing |

### Structured JSON logging & W3C distributed tracing

Every log line is a single JSON object with the same nine correlation fields, so a request can be
followed from the edge through the API and into the database without grep archaeology.

| Field | Meaning |
|---|---|
| `timestamp` | RFC 3339 / ISO 8601 UTC with milliseconds and a `Z` suffix, e.g. `2026-10-30T15:00:00.123Z` |
| `level` | Lower-cased Python level: `debug`, `info`, `warning`, `error`, `critical` |
| `service` | Fixed service identity: `toko-marcell-api` |
| `version` | Deployed API version: `1.0.0` |
| `environment` | `production` / `development` from `ENVIRONMENT` / `DEBUG` |
| `message` | The rendered log message |
| `trace_id` | 32-hex W3C trace ID, shared by every span in the trace |
| `span_id` | 16-hex W3C span ID for the current request |
| `request_id` | Incoming `X-Request-ID` or a generated `req_<hex8>` |

```json
{"timestamp":"2026-10-30T15:00:00.123Z","level":"info","service":"toko-marcell-api","version":"1.0.0","environment":"production","message":"request.completed","trace_id":"4bf92f3577b34da6a3ce929d0e0e4736","span_id":"00f067aa0ba902b7","request_id":"req_8ac34d30","method":"GET","path":"/admin/kpi","status_code":200,"duration_ms":42.17}
```

**W3C TraceContext propagation.** When a caller sends
`traceparent: 00-<32hex>-<16hex>-01`, the API adopts that trace and span ID and echoes the request
ID on the response as `X-Request-ID`. Missing or malformed headers are replaced with a fresh random
32-hex trace ID and 16-hex span ID, so logs remain correlatable even when the caller is not
instrumented.

**Context without plumbing.** Request IDs and trace IDs live in `contextvars`
(`api/logging_context.py`) and are read by `JSONLogFormatter` at format time. Business logic,
services and database helpers never receive a logger or request object as an argument — they just
log normally and the context is attached automatically.

**Zero-agent shipping.** `setup_logging()` (`api/logging_config.py`) streams newline-delimited JSON
to `stdout`, which is exactly what Render, Datadog, CloudWatch, Loki and similar collectors already
ingest. There is no sidecar, file tailer or heavyweight daemon to deploy.

### Rate limits

- Copilot: `COPILOT_RATE_LIMIT_PER_MIN` and `COPILOT_RATE_LIMIT_PER_DAY` per client IP.
- Writes (`/events`, `/checkout/confirm`, `/sizing/recommend`, `/copilot/feedback`): bounded per-IP
  write limiter with `429` + `Retry-After`.

### Admin protection

`/admin/kpi` accepts a Bearer JWT issued by `/auth/login`; the legacy `ADMIN_TOKEN` is compared in
constant time (`hmac.compare_digest`) as a fallback. `/search/reindex` still uses `ADMIN_TOKEN`
directly and returns `503` when it is unset rather than falling open. Access JWTs expire after 15
minutes; refresh tokens rotate on every use and are stored only as SHA-256 digests.

### Cold starts

Render's free tier sleeps after inactivity. The storefront probes `/health` and renders a wake-up
state for 30–50 seconds. The BM25 index and embedding model are loaded lazily, so `/health` and
catalog browsing never wait for model initialization.

---

## Testing

```bash
# Full backend suite (requires the seeded database and a running API on :8001)
pytest api/tests/

# Unit-only subsets (no database)
pytest api/tests/test_search_unit.py api/tests/test_sizing_unit.py \
       api/tests/test_guardrail_unit.py api/tests/test_copilot_tools_unit.py

# Lint
ruff check api/ pipelines/ scripts/
```

Integration tests target `http://localhost:8001` and assume the Docker Compose stack is running.
CI restores PostgreSQL from the seed dump, starts Uvicorn, runs Ruff and the full suite with
`COPILOT_FAKE_LLM=1`, then executes the 54-scenario copilot benchmark. Tests that require a real LLM
key are marked `llm` and skip automatically when no key is configured.
