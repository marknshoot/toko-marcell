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
| `OPENROUTER_API_KEY` | for copilot | — | **Preferred provider.** When set, the copilot uses OpenRouter; without any LLM key `/copilot/chat` returns `503`, everything else works |
| `LLM_MODEL` | | `openrouter/free` | Any OpenRouter model id. `openrouter/free` auto-routes across the free pool and handles tool-calling + the guardrail well (verified end-to-end). To pin one: `inclusionai/ling-3.0-flash-sante:free`, or `nvidia/nemotron-3-super-120b-a12b:free` (may emit tool-call JSON as the reply) |
| `LLM_BASE_URL` | | `https://openrouter.ai/api/v1` | OpenAI-compatible endpoint |
| `LLM_FALLBACK_MODELS` | | *(empty)* | Optional comma-separated fallbacks passed via OpenRouter `models`; tried when the primary returns 429/503. Unnecessary with `openrouter/free` |
| `LLM_FALLBACK_BASE_URL` / `LLM_FALLBACK_API_KEY` / `LLM_FALLBACK_MODEL` | | — | Optional **generic OpenAI-compatible** provider inserted into the copilot's `.with_fallbacks` chain between OpenRouter and Gemini |
| `COPILOT_FAKE_LLM` | | *(unset)* | `1` swaps in a deterministic in-process fake tool-calling model (no network, no key) so CI/tests never hit a real provider |
| `GEMINI_API_KEY` | fallback | — | Google AI Studio key; the final link in the provider chain, used only when the others are absent |
| `GEMINI_MODEL` | | `gemini-3.5-flash-lite` (code) / `gemini-2.5-flash` (compose) | Any Gemini chat model (fallback path) |
| `TEXT_EMBED_MODEL` | | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | Multilingual (ID/EN) 384-d dense text embedding model (or `sentence-transformers/all-MiniLM-L6-v2` for English-only low-RAM mode) |
| `TEXT_EMBED_COLUMN` | | `embedding_ml` | Which 384-d column `/search` and Copilot `search_catalog` query (`embedding_ml` default, or `embedding`) |
| `CORS_ORIGINS` | | `http://localhost:3000,https://toko-marcell.vercel.app` | Comma-separated allowlist; `*` opens it up |
| `ADMIN_TOKEN` | for reindex | — | Secret token for `POST /search/reindex`; endpoint returns `503` when unset. Docker Compose defaults to `dev-admin-token`; set a strong value in production |
| `TRUST_PROXY` | | `false` | Set `true` on Render (behind their LB) so `X-Forwarded-For` is trusted for rate-limiting |
| `COPILOT_RATE_LIMIT_PER_MIN` | | `10` | Max copilot requests per client IP per minute |
| `COPILOT_RATE_LIMIT_PER_DAY` | | `100` | Max copilot requests per client IP per day |
| `ENABLE_TRIMODAL` | | `false` (set `true` in local Compose) | Enable `mode=trimodal`; it loads both MiniLM and the CLIP text ONNX (`Qdrant/clip-ViT-B-32-text`), so it needs ≥1 GB RAM |
| `ENABLE_RERANKER` | | `false` (set `true` in local Compose) | Cross-encoder reranking for `/search?rerank=true` and the copilot's `search_catalog` |
| `FASTEMBED_CACHE_DIR` | | `/tmp/fastembed_cache` | Cache for MiniLM and CLIP `fastembed` ONNX models |
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
| `POST` | `/copilot/chat` | Agentic copilot turn (v2: deterministic guardrail → `create_agent` loop → grounded reply); text-only |
| `POST` | `/copilot/chat/stream` | Stream an agentic copilot turn via Server-Sent Events (`token` + `done` events) |
| `GET` | `/copilot/tools` | The 6 deterministic tool names the v2 copilot can call |
| `POST` | `/copilot/feedback` | Record a thumbs up/down vote for a copilot thread → `201` · rate-limited like other writes |
| `GET` | `/copilot/threads/{thread_id}` | Replay a conversation thread's display messages (may be empty) |
| `DELETE` | `/copilot/threads/{thread_id}` | Reset a conversation (delete all persisted checkpointer state) → `204` |
| `POST` | `/events` | Record a funnel event → `201` · `422` on unknown type |
| `GET` | `/events/summary` | Funnel + order revenue/AOV + copilot cohorts + copilot quality, with explicit caveats |
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

## Module map

```text
api/
├── main.py                   # thin app factory: lifespan, CORS, schema migration, include routers
├── migrate.py                # programmatic `alembic upgrade head` (run on startup)
├── alembic.ini · migrations/ # Alembic env + hand-written SQL revisions
├── config.py                 # pydantic-settings Settings, one cached get_settings()
├── db.py                     # psycopg_pool ConnectionPool, get_conn() context manager / Depends
├── schemas.py                # shared Pydantic request/response models
├── routers/
│   ├── health.py             # GET /health
│   ├── catalog.py            # GET /categories, /products, /products/{id}
│   ├── search.py             # GET /search, POST /search/image, POST /search/reindex
│   ├── recs.py               # GET /recs/popular, /recs/item/{asin}, /recs/session
│   ├── reviews.py            # GET /products/{id}/reviews
│   ├── events.py             # POST /events, GET /events/summary (funnel, revenue, cohorts, quality)
│   ├── checkout.py           # POST /checkout/confirm, GET /orders/{token}
│   └── copilot.py            # POST /copilot/chat, /chat/stream, /feedback; GET /tools, /threads/{id}
├── search.py                 # BM25 index, embeddings, RRF, CLIP encoders (ONNX)
├── reranker.py               # Stage-2 ONNX cross-encoder (ms-marco-MiniLM-L-6-v2)
├── guardrail.py              # deterministic pre-agent guardrail (greeting/off-topic/injection, zero LLM)
├── copilot_agent.py          # LangChain v1 create_agent orchestrator (run_turn) + middleware
├── copilot_metrics.py        # best-effort copilot_turns telemetry + outcome/token extraction
├── copilot_tools.py          # the 6 deterministic @tool functions bound to the agent
├── copilot_llm.py            # provider fallback chain (OpenRouter → generic → Gemini) + fake LLM
├── copilot_memory.py         # LangGraph PostgresSaver checkpointer + thread activity / TTL cleanup
├── sizing.py                 # deterministic recommend_size logic (TB/BB chart + brand cut rules)
├── rate_limiter.py           # sliding-window per-IP rate limiter for /copilot/chat
├── knowledge_seed.py         # markdown → embedded store_knowledge chunks + hypothetical questions
├── knowledge/                # authored RAG source: size_charts.md, store_policies.md, size_chart.json
└── tests/                    # pytest suite (unit + live API integration)
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

- 384-d `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` sentence embeddings → `products.embedding_ml` (HNSW cosine, bilingual ID/EN default; `products.embedding` with `all-MiniLM-L6-v2` also available).
- 512-d pre-trained CLIP embeddings (`Qdrant/clip-ViT-B-32-vision` / `Qdrant/clip-ViT-B-32-text` via `fastembed` ONNX) → `products.image_embedding` (HNSW cosine).
- `/search?mode=` switches ranker: `bm25`, `vector`, `hybrid` (default: BM25+vector), `trimodal`
  (BM25+dense+CLIP-text). All fused lists go through **Reciprocal Rank Fusion** with `k=60`.
  `trimodal` loads both the text MiniLM model and the CLIP text encoder, so it is gated behind
  `ENABLE_TRIMODAL` (`false` on 512 MB deployments, `true` in local Compose).

$$
\text{RRF}(d) = \sum_{m \in M} \frac{1}{60 + r_m(d)}
$$

RRF is chosen over score normalization because BM25 scores are unbounded positives while cosine is
bounded; RRF needs only ranks, so heterogeneous rankers combine with no calibration.

### Image search (`POST /search/image`)

1. Validate MIME (`image/*`) and size (50 B–10 MB).
2. Offload synchronous image decoding, `fastembed` ONNX inference (`Qdrant/clip-ViT-B-32-vision`), and
   PostgreSQL query execution to a worker thread (`asyncio.to_thread`) so the FastAPI event loop never blocks.
3. Cosine search against `image_embedding`, ordered by HNSW, returning `visualSimilarity` per item and
   `timings: { embed_ms, db_ms }`. To embed or refresh catalog image vectors:
   `python3 pipelines/embed_images.py --overwrite` — see [`../pipelines/README.md`](../pipelines/README.md).

### Stage-2 cross-encoder reranking (optional, copilot only)

Used only by the copilot's `search_catalog` and only when `ENABLE_RERANKER=true`; `/search` returns
the RRF order. `reranker.py` re-scores the retrieved candidates with `Xenova/ms-marco-MiniLM-L-6-v2` through ONNX
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

`GET /products/{id}/reviews` returns an aggregate (count, average, 1–5 star
breakdown) plus up to `limit` authentic reviews. These are real Amazon reviews extracted for the
catalog (`../pipelines/extract_reviews.py`, ≤10 per ASIN), stored in the `reviews` table — the seed
contains **46,700** of them. They power the product-page review UI; the copilot has no review
tool (its sizing guidance comes from the authored brand cut rules, not from review mining).

---

## The AI copilot (v2)

The copilot is an **agentic RAG loop** built on **LangChain v1 `create_agent`** (langchain 1.4.3,
langgraph 1.2.12), not a chat completion with the catalog pasted in. It replaced the earlier
two-pass planner/synthesizer design.

Provider chain (`copilot_llm.py`, wired with `.with_fallbacks`): **OpenRouter** (primary, whenever
`OPENROUTER_API_KEY` is set) → an optional **generic OpenAI-compatible** endpoint
(`LLM_FALLBACK_BASE_URL` / `LLM_FALLBACK_API_KEY` / `LLM_FALLBACK_MODEL`) → **Google Gemini**.
`LLM_MODEL` defaults to `openrouter/free`, which auto-routes across OpenRouter's free pool; the
active provider chain is printed once at startup. With no provider configured, `/copilot/chat`
returns `503`. Setting `COPILOT_FAKE_LLM=1` swaps in a deterministic in-process fake tool-calling
model so CI and tests never call a real LLM.

**Text-only — no image in chat.** The v2 chat does **not** accept images (there is no `image_url`
field and no image tool). Photo search still exists as a first-class storefront feature via
`POST /search/image`; it is simply not part of the conversation.

### Execution model

```text
Guardrail (deterministic, 0 LLM calls)  ─ greeting / off-topic / injection → canned reply
      │ (anything else)
      ▼
create_agent loop  ─ LLM ⇄ tools (max 3 model calls/turn) → grounded reply + cards
```

1. **Pre-agent guardrail (`guardrail.py`).** Before the tool-calling agent runs, `classify(message, history)`
   evaluates the user message alongside up to **3 user + 3 assistant history turns** using a fast
   LLM classifier (with deterministic regex fast-path for pure greetings/injections and full offline
   fallback when `COPILOT_FAKE_LLM=1`). Greetings, off-topic requests (code, SQL, math, politics,
   medical/legal, creative) and prompt-injection attempts receive a canned "Admin Toko Marcell" reply
   tagged with `guardrail` (`greeting` | `off_topic` | `injection`).
2. **`create_agent` loop (`copilot_agent.py`).** The LLM plans and calls tools, iterating until it
   has an answer. A `ModelCallLimitMiddleware` caps it at **3 model calls per turn** so a free-tier
   quota can't be drained by a loop. User messages are sanitized against spoofed context headers, and
   the injected context block is wrapped in `<store_context>` tags with explicit untrusted-data prompt rules.
3. **Grounded answer.** The reply may only use tool evidence, in the "Admin Toko Marcell" voice
   (Indonesian or English, mirroring the shopper), with structured product cards.

### The 6 bound tools (`copilot_tools.py`)

| Tool | Backed by | Purpose |
|---|---|---|
| `search_catalog` | BM25 + multilingual MiniLM (`embedding_ml`, pgvector) fused with RRF, optional reranker, department/category/budget filters | bilingual product discovery |
| `get_product_details` | `products` by registry ref (`nomor 2`, `produk ini`), ASIN/id, or title fragment | fabric, specs, exact IDR price |
| `recommend_size` | `sizing.py` (TB/BB, 2-layer: store baseline chart → brand cut rule) | advisory sizing, framed as guidance never a certainty |
| `build_outfit` | catalog search for top + bottom + shoes | the total is **summed and verified ≤ budget in code**, not by the LLM |
| `lookup_store_policy` | `store_knowledge` + `store_knowledge_questions` + IDF-weighted lexical RRF | bilingual sizing (TB/BB), shipping, returns, QRIS demo |
| `add_to_cart` | re-reads `products.price_idr` server-side | returns a `ui_action` the browser executes |

`GET /copilot/tools` returns exactly these 6 names. There is deliberately **no order-status tool**:
checkout is a demo simulation with no fulfillment, shipping or tracking to report on.

### Middleware

- **`ContextInjectionMiddleware`** — injects the page / pinned product (re-read from the DB), a
  numbered `[Produk dalam percakapan ini] [1]…` shown-products registry so follow-ups like "yang
  kedua" resolve to a real ASIN, and recent browsing context. Because the per-request context is
  new each turn, `run_turn` / `stream_turn` re-seed the registry from the previous turns'
  `ToolMessage` results in the checkpointer (only products shown *this* turn become response cards).
- **`TrimHistoryMiddleware`** — keeps the prompt inside a 2,000-token budget (char/4 estimate).
- **`ModelCallLimitMiddleware`** — hard cap of 3 model calls per turn.

### Memory

Conversation state lives in a **LangGraph `PostgresSaver`** keyed by a browser-generated UUID
`thread_id` (stored in `localStorage` under `tm_copilot_thread`), with a sidecar
`copilot_thread_activity` table and a 7-day TTL cleanup (on startup and daily). History lives in the
checkpointer, not in the request — a turn sends a single `message`. `GET /copilot/threads/{thread_id}`
replays the display messages; `DELETE /copilot/threads/{thread_id}` resets the conversation (`204`).

### API contract (v2)

`POST /copilot/chat` (`CopilotChatV2In`, `extra="forbid"`):

```json
{
  "session_id": "sess-abc12345",         // optional
  "thread_id": "uuid-from-localStorage", // required (1..64 chars)
  "message": "Ada jaket buat hujan?",    // required (1..2000 chars)
  "context": { "page_asin": "B0001YRQHQ", "referenced_asins": ["B00XXXX"] }  // optional, ≤3 refs
}
```

Response (`CopilotChatV2Out`, or streamed via `POST /copilot/chat/stream` as SSE `token` + `done` events): `reply`, `thread_id`, `products` (≤6, each with a stable `ref` number),
`ui_actions` (`add_to_cart`), `tool_calls`, `guardrail` (`greeting` | `off_topic` | `injection` |
`null`) and `took_ms`. Errors: `422` (malformed/oversized), `429` (rate limit), `503` (no LLM key),
`502` (upstream failure).

`POST /copilot/feedback` (`CopilotFeedbackIn`, `extra="forbid"`):

```json
{ "thread_id": "uuid-from-localStorage", "session_id": "sess-abc12345", "rating": 1 }
```

`rating` is `1` (thumbs up) or `-1` (thumbs down); `session_id` is optional. The endpoint is
rate-limited like `/events` and writes one `copilot_feedback` row. `GET /events/summary` aggregates it
in `copilot_quality`.

### Grounding guarantees

- Every product in a reply comes from a tool result; the prompt forbids invented brands, ASINs,
  stock or discounts, and the tool results are the only catalog data in context.
- Prices are always IDR, always from `products.price_idr`; `add_to_cart` re-reads the price server-side.
- Sizing is answered by the deterministic `recommend_size` tool and framed as guidance, applying the
  authored brand cut rules in `knowledge/size_chart.json` (the structured source of truth, labelled
  *panduan umum toko*) and explained in `knowledge/size_charts.md`. Sizing and fit come **only** from
  that knowledge base — no per-product or review-derived signal.
- Outfit budgets are summed and verified in code, not by the LLM.
- Off-topic, coding, SQL, academic, medical/legal and prompt-injection requests are stopped by the
  deterministic guardrail with **zero LLM calls**.

**Knowledge ingestion & RAGAS evaluation.** `knowledge_seed.py` splits `knowledge/*.md` by section header,
embeds each chunk with `TEXT_EMBED_MODEL` (`paraphrase-multilingual-MiniLM-L12-v2`) into `store_knowledge`
(**23 chunks**, HNSW index), and generates 3–5 bilingual (ID+EN) hypothetical questions per chunk into
`store_knowledge_questions` (**82** questions). At query time, `lookup_store_policy` fuses
hypothetical-question vectors, direct-chunk vectors, and IDF-weighted lexical keywords via RRF (`k=60`),
achieving **1.0000 Context Recall** and **0.9167 Context Precision** in RAGAS evaluation
(`../pipelines/eval_rag.py`).

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
| `copilot_message` | a copilot chat turn | — |
| `copilot_product_click` | a product card in a copilot reply is opened | `asin` |
| `copilot_add_to_cart` | add-to-cart triggered from the copilot | `asin`, `qty` |

```bash
curl -s -X POST localhost:8001/events -H 'Content-Type: application/json' \
  -d '{"event_type":"view_product","session_id":"sess-abc12345","asin":"B000YXC2LI"}'
curl -s localhost:8001/events/summary | python3 -m json.tool
```

The frontend posts events **fire-and-forget** (`keepalive: true`, errors swallowed): losing a data
point is cheaper than breaking a purchase.

`GET /events/summary` returns the original funnel keys (`by_type`, `sessions_by_step`, `rates`,
`search`, `copilot`, `caveats`) plus the phase-9 measurement blocks:

| Block | Contents | Source |
|---|---|---|
| `revenue` | `order_count`, `revenue_idr`, `aov_idr` (`null` without a denominator) | `orders` only — never browser events |
| `cohorts` | `copilot` vs `non_copilot`: `sessions`, `sessions_with_add_to_cart`, `add_to_cart_rate`, `purchase_sessions`, `purchase_rate`; `purchase_source`; `caveat` | `events` + `orders` (joined on `session_id`) |
| `copilot_quality` | `turns`, `unanswered_rate`, `guardrail_rate`, `avg_latency_ms`, `p50_latency_ms`, `p95_latency_ms`, `thumbs_up`, `thumbs_down`, `thumbs_up_rate` | `copilot_turns` + `copilot_feedback` |

The cohort caveat says plainly that the comparison is **correlational and self-selected** — there is
no holdout. `copilot_quality.turns` counts all logged turns, so `unanswered_rate` and
`guardrail_rate` share that denominator. `guardrail_rate` counts every deterministic guardrail
label, including greetings, not only unsafe blocks. `copilot` also keeps `messages`, `product_clicks`,
`add_to_cart`,
`total_add_to_cart` and `assisted_add_to_cart_rate` (= `copilot_add_to_cart` / all `add_to_cart`,
`null` when there is no denominator); a copilot-driven add fires **both** events, so it is an
attributed share, not an independent funnel.

`orders` is the only revenue source. `purchase_mock` is written by the confirm handler today, but the
public `/events` endpoint still accepts it from any client and historical rows may be browser-asserted;
it remains a directional funnel signal. Rates are `null`, never `0.0`, when a denominator is missing.

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
- `orders` is the authoritative checkout record; `purchase_mock` is a funnel event only. The public
  events endpoint still accepts a client-posted `purchase_mock`, so revenue/AOV never read it.
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

- **AI token safeguard** — `/copilot/chat`: text-only, ≤2,000 chars/message (`extra="forbid"` rejects
  anything malformed or oversized with a `422`), plus per-IP rate limiting.
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

The schema is versioned with **Alembic** (`alembic.ini`, `migrations/versions/`). Migrations are
hand-written SQL via `op.execute` — there are no ORM models; SQLAlchemy is only Alembic's connection
layer and all queries stay raw psycopg. On startup `init_db()` runs `alembic upgrade head`
(`migrate.py`), so `docker compose up` and Render need no extra step.

| Revision | Adds |
|---|---|
| `0001` baseline | every app table + indexes (incl. HNSW); drops the legacy v1 demo table (no `asin`) |
| `0002` | `products.embedding_ml vector(384)` + HNSW index (opt-in multilingual) |

Each revision is idempotent (`IF NOT EXISTS`), so databases created before Alembic (the seed dump,
Neon) are adopted by a plain `upgrade head` — no `alembic stamp` needed. The LangGraph checkpoint
tables and their `copilot_thread_activity` sidecar are still created at runtime by
`copilot_memory.py`.

```bash
cd api
alembic upgrade head            # or: python migrate.py
alembic current
alembic revision -m "add foo"   # then write upgrade()/downgrade() SQL by hand
```

```sql
products (
  id INTEGER PRIMARY KEY, asin TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL, brand TEXT,
  price_usd NUMERIC(10,2) NOT NULL, price_idr INTEGER NOT NULL,
  department TEXT, category TEXT, category_path JSONB,
  description TEXT, features JSONB, image_url TEXT,
  avg_rating NUMERIC(3,2), rating_count INTEGER NOT NULL DEFAULT 0,
  also_buy JSONB, also_view JSONB,
  embedding vector(384),            -- MiniLM (default), HNSW cosine
  embedding_ml vector(384),         -- paraphrase-multilingual-MiniLM-L12-v2 (opt-in A/B), HNSW cosine
  image_embedding vector(512)       -- champion CLIP, HNSW cosine
)
events           (id BIGSERIAL, session_id, event_type, asin, query, results_count, qty, price_idr, created_at)
copilot_turns    (id BIGSERIAL, thread_id, session_id, created_at, guardrail, outcome, tool_names, latency_ms,
                  input_tokens, output_tokens, model, cost_usd)
copilot_feedback (id BIGSERIAL, thread_id, session_id, rating CHECK (-1,1), created_at)
orders           (id BIGSERIAL, token UNIQUE, total_idr, item_count, status, session_id, created_at)
order_items      (id BIGSERIAL, order_id → orders, asin, title, qty, unit_price_idr)
item_recommendations (asin PRIMARY KEY, recs JSONB)
reviews          (id SERIAL, asin, rating, summary, comment, author, verified, review_date, created_at)
store_knowledge  (id SERIAL, category, title, content, embedding vector(384))        -- 23 chunks
store_knowledge_questions (id SERIAL, knowledge_id → store_knowledge, question, embedding vector(384))  -- 82 hypothetical questions
copilot_thread_activity (thread_id, last_touch)   -- sidecar for the 7-day TTL cleanup
```

Indexes: `products(department)`, `products(category)`, HNSW on `products.embedding`,
`products.embedding_ml` and `products.image_embedding`, HNSW on `store_knowledge.embedding` and
`store_knowledge_questions.embedding`, plus events/orders/reviews indexes.

The LangGraph **checkpointer tables** (used by the copilot's `PostgresSaver`) are **created at
runtime** by the saver, not shipped in the seed — they are ephemeral conversation state and are
excluded from `data/seed/init.sql.gz`. The Phase 4 additions (`embedding_ml`,
`store_knowledge_questions`) ship in the seed and are applied idempotently by
`../pipelines/migrations/2026_10_phase4.sql`.

`pgvector` is enabled defensively: if the extension is unavailable the API still boots, just without
an `embedding` column (search is disabled rather than crashing).

---

## Testing

```bash
# Pure unit tests — no database needed (also part of what CI runs)
python3 -m pytest tests/test_search_unit.py

# Full API suite — needs a running Postgres (docker compose up) and the champion vision ONNX for
# image tests. The copilot tests run against the deterministic fake LLM (COPILOT_FAKE_LLM=1), so no
# real LLM key is required.
COPILOT_FAKE_LLM=1 python3 -m pytest tests/
```

The API suite is **173 passed + 4 skipped**. The skips are environment-gated (image/trimodal
encoders, a rate-limit integration test, and an LLM-key case); the pipeline suite is **6**
(pipeline-math only, no DB). The suites,
grouped by concern:

| Suite | Covers |
|---|---|
| `test_search_unit.py` | stemmer, tokenizer, BM25 scoring, dedupe, facet filtering |
| `test_search_api.py` | modes, semantic/SKU queries, plural equivalence, filters, reindex |
| `test_catalog_api.py` | health, facets, pagination boundaries, detail 404/422 |
| `test_checkout_and_events_api.py` | valid/invalid events (incl. copilot types), **price-tampering rejection**, duplicate-ASIN aggregation, order lookup |
| `test_copilot_api.py` | tool registry (6 names), guardrails, thread memory, the v2 shopping/ops use cases (fake LLM) |
| `test_recs_api.py` | popular, department filter, item CF + fallback, session cold vs warm |
| `test_reviews_api.py` | review aggregates, limits, 404s |
| `test_search_image_api.py` | valid image, department filter, bad MIME, tiny file |
| `test_agent_tools_real.py` | real-tool grounding against the live DB |

CI (`.github/workflows/ci.yml`) now runs **Ruff** plus the **full API suite** against a Postgres
service restored from the seed with `COPILOT_FAKE_LLM=1`, alongside the frontend lint/test/build.

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
- Migration `0003` adds copilot telemetry; revisions are applied on startup, still with a manual
  `alembic upgrade head` available for operators.
- Reindex is manual after a reseed.
- The copilot is bound to 6 tools (search, details, size, outfit, policy, add-to-cart);
  there is intentionally **no order-status tool** (checkout is a simulation), and reviews
  are surfaced on the product page rather than in chat.
- Render free tier cold-starts (see the cold-start overlay in the storefront).

---

### Related docs

- [`../README.md`](../README.md) — project overview, research study, architecture
- [`../pipelines/README.md`](../pipelines/README.md) — dataset provenance and offline pipelines
- [`../shop/README.md`](../shop/README.md) — storefront internals
