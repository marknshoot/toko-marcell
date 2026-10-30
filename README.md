# Toko Marcell — AI Engineering for Bilingual E-Commerce

[![CI](https://img.shields.io/badge/CI-GitHub%20Actions-2088FF?style=for-the-badge&logo=githubactions&logoColor=white)](./.github/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL%2016-pgvector%20HNSW-336791?style=for-the-badge&logo=postgresql&logoColor=white)](https://github.com/pgvector/pgvector)
[![FastAPI](https://img.shields.io/badge/FastAPI-API-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![LangGraph](https://img.shields.io/badge/LangGraph-Agent%20Runtime-1C3C3C?style=for-the-badge&logo=langchain&logoColor=white)](https://langchain.com)
[![Next.js](https://img.shields.io/badge/Next.js-Storefront-000000?style=for-the-badge&logo=next.js&logoColor=white)](https://nextjs.org)

Toko Marcell is a production-deployed Indonesian fashion storefront built around six measurable AI
engineering systems: **bilingual Information Retrieval (Search)**, **collaborative-filtering
recommendations**, **deterministic sizing**, **retrieval-augmented store knowledge**, **hardened agent
orchestration**, and **operations telemetry**. Every system is instrumented and evaluated; the
application suites and the 54-scenario copilot benchmark are gated in continuous integration
(CI) — not demonstrated with a
notebook.

The catalog contains **4,670 curated products**, **46,700 reviews**, and **3.36 million interaction
records**. The shopper-facing experience is served by Next.js on Vercel; the FastAPI backend,
PostgreSQL 16 + pgvector, and LangGraph agent runtime run in Docker locally and on Render.

| Destination | Link |
|---|---|
| **Live storefront** | [https://toko-marcell.vercel.app](https://toko-marcell.vercel.app) |
| **Executive Key Performance Indicator (KPI) dashboard** | [https://toko-marcell.vercel.app/admin/kpi](https://toko-marcell.vercel.app/admin/kpi) — admin token: `dev-admin-token` |
| **API documentation (OpenAPI)** | [https://toko-marcell-api.onrender.com/docs](https://toko-marcell-api.onrender.com/docs) |
| **Health check** | [https://toko-marcell-api.onrender.com/health](https://toko-marcell-api.onrender.com/health) |

> The backend runs on Render's free tier and sleeps after inactivity. The first request can take
> 30–50 seconds; the storefront shows an explicit wake-up state instead of failing silently.

---

## Contents

1. [The Business Problem & Commercial Value](#1-the-business-problem--commercial-value)
2. [The Six Core Technical Systems](#2-the-six-core-technical-systems)
3. [Empirical Benchmarks & Verification](#3-empirical-benchmarks--verification)
4. [Quickstart & Local Development](#4-quickstart--local-development)
5. [Repository Map](#5-repository-map)

---

## 1. The Business Problem & Commercial Value

### 1.1 Why Toko Marcell exists

Indonesian e-commerce catalog discovery is broken for the way people actually type. Shoppers search
in **code-switched queries** — Indonesian (Bahasa Indonesia) mixed with English, brand names, and
casual abbreviations:

```
"sepatu running pria"        "jaket waterproof buat hujan"
"celana jeans levis 501"     "baju kerja wanita hitam"
```

Traditional keyword search (**BM25**) treats these as unrelated tokens. It cannot connect
`sepatu` to `shoes`, `baju` to `shirt`, or a shopper's intent to a semantically similar product.
The result is a zero-result page, a bounce, and lost revenue — even when the catalog contains the
exact item the shopper wanted.

**The commercial thesis:** making the existing catalog discoverable is the cheapest revenue lever a
retailer has. No new inventory, no new ad spend — just retrieval that understands the shopper.

### 1.2 Why the AI Sales Copilot is needed

Online fashion loses **25–30% of orders to returns**, and size uncertainty is a leading cause.
Shoppers who are unsure about fit abandon the product page instead of asking for help, because
there is no salesperson online.

The **AI Sales Copilot** is a 24/7 in-store shopkeeper. It:

- **Discovers products** from a natural-language request in Indonesian, English, or both.
- **Recommends multi-item outfits** that respect an enforced budget invariant — the total is
  computed and validated in code, never trusted to the language model.
- **Answers store policy** questions (shipping, returns, payment, sizing rules) with grounded,
  retrievable evidence.
- **Determines body sizes** from height, weight, foot length, waist, or head circumference using
  deterministic, category-aware size charts.
- **Admits uncertainty** through an explicit `flag_unanswered` path, which feeds a live backlog of
  questions the catalog and knowledge base cannot yet answer.

The business outcomes are conversion, average order value, and deflection of repetitive support
questions — measured in the KPI dashboard, not asserted in a pitch deck.

### 1.3 Why the Recommendation System is needed

A fashion catalog is a cross-sell business. Shoppers rarely arrive knowing the full outfit, and
"customers also bought" is the difference between a one-item basket and a three-item basket.

Toko Marcell uses **Item-to-Item Collaborative Filtering** learned from 3.36 million interaction
records, plus **department-level popularity** as a robust cold-start baseline. Recommendations appear
on the product page, in the cart, and in a personalised homepage rail. The result is larger baskets,
more organic catalog discovery, and a surface that keeps working for anonymous first-time visitors.

### 1.4 Unit Economics Discipline

The fastest way to destroy a retail margin is unmeasured large language model (LLM) inference. A
copilot that costs more per order than the order's gross margin is a science project, not a product.

Toko Marcell treats inference as a first-class Service Level Objective (SLO):

| Guardrail | Target | Achieved |
|---|---:|---:|
| **Cost per assisted order** | **≤ $0.005** | **$0.0016** |
| Copilot p95 turn latency | < 5.0 s | Live in `/admin/kpi` |

An **assisted order** is an order whose session had at least one copilot turn. Cost is estimated
from configured per-million-token prices and recorded per turn in `copilot_turns.cost_usd`. The
dashboard reports cost per turn, per conversation, per resolved conversation, and per assisted order
side by side with conversion — so a cheap-but-useless copilot cannot hide.

---

## 2. The Six Core Technical Systems

```mermaid
flowchart LR
    S[Shopper] --> FE[Next.js storefront]
    FE --> API[FastAPI]
    API --> IR[Hybrid search<br/>BM25 + MiniLM + RRF]
    API --> REC[Item-to-item CF<br/>+ department popularity]
    API --> SIZE[Deterministic sizing<br/>260 categories]
    API --> AGENT[LangGraph copilot<br/>guardrails + 7 tools]
    AGENT --> RAG[Store knowledge RAG<br/>34 chunks + 120 questions]
    IR --> PG[(PostgreSQL 16<br/>pgvector HNSW)]
    REC --> PG
    SIZE --> PG
    RAG --> PG
    AGENT --> PG
    API --> KPI[Next.js /admin/kpi]
```

### System 1 — Bilingual Hybrid Search & Information Retrieval

**Business value.** Recovers revenue from code-switched and intent-driven queries that keyword search
misses. Indonesian queries are the hardest and most valuable segment.

**Engineering design.**

- **Lexical baseline:** a hand-written, in-process BM25 index (`k1=1.5`, `b=0.75`) with field
  weighting (title 3.0, brand 2.0, category 1.5, features 1.0, description 0.5), plural folding, and
  deterministic Indonesian → English query rewriting after a zero-result search.
- **Dense retrieval:** 384-dimensional multilingual embeddings from
  `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, stored in PostgreSQL and queried
  through a **pgvector HNSW** cosine index.
- **Fusion:** Reciprocal Rank Fusion (**RRF, `k=60`**) merges the lexical and dense rankings without
  calibrating incompatible score distributions.
- **Serving modes:** `hybrid` (default), `dense`, and `bm25`. Search results are deduplicated by
  product title before pagination, so the page count reflects what the shopper can actually see.
- **Search health telemetry:** `GET /search/insights` exposes top queries, zero-result queries, the
  zero-result rate, and session-level search-to-product-detail-page conversion.

**Measured result:** Indonesian normalized Discounted Cumulative Gain at 10 (nDCG@10) rises from
**0.334 (BM25)** to **0.765 (hybrid)** — a **+129%** gain — while overall nDCG@10 rises from 0.794 to
0.894 and HitRate@10 reaches 100%.

### System 2 — Collaborative Filtering Recommendation Engine

**Business value.** Increases basket size and cross-sell without manual merchandising, and gives new
or anonymous shoppers a useful starting point.

**Engineering design.**

- An offline item-to-item model computes co-occurrence recommendations from the interaction matrix
  and writes the top neighbours to the `item_recommendations` table (`asin`, `recs` JSONB).
- **Department popularity** (`rating_count`, `avg_rating`) is the cold-start and fallback baseline.
- At serving time, `GET /recs/item/{asin}` returns the item-to-item list; if it is short or absent,
  the endpoint falls back to same-category products, then department popularity.
- Session recommendations union the last-viewed items' neighbours and exclude products the shopper
  has already seen.
- Evaluation uses **sequential leave-one-out ranking**: hold out each user's final interaction, rank
  the catalog, and measure HitRate / MRR / nDCG.

**Measured result:** item-to-item CF achieves **HR@10 = 7.96%** versus **0.18%** for the popularity
baseline — a **+4,322%** relative improvement on the offline protocol.

### System 3 — Deterministic Category-Aware Sizing Engine

**Business value.** Attacks the returns problem at the point of add-to-cart. A shopper gets an honest
size recommendation in seconds, without waiting for a human or trusting a hallucinated LLM answer.

**Engineering design.**

- **260 catalog categories** are mapped to standardised sizing kinds: `tops`, `bottoms`, `footwear`,
  `belt`, `headwear`, plus `dress`, `one_size`, `dimensions`, and an explicit `unsupported` class.
- **Footwear** uses the **ISO 9407 Mondopoint** foot-length standard with gendered US/UK/EU
  conversion tables. **Tops, bottoms, belts, and headwear** use authored, gendered size charts.
- The engine is **zero-LLM** and deterministic: `POST /sizing/recommend` takes body measurements and
  returns `applicable`, `size_kind`, `recommendation`, `source`, `confidence`, `required_inputs`, and a
  disclaimer. Missing measurements return `applicable=false` with the exact inputs required — never a
  guessed size.
- The same core powers the copilot's `recommend_size` tool and the product-page sizing widget, so
  there is one source of truth for fit advice.
- `GET /sizing/{asin}` tells the frontend which measurements a product actually needs.

**Why deterministic:** size advice is a liability surface. A rule engine that says "I need your foot
length" is more trustworthy than a language model that invents a size.

### System 4 — Store Knowledge RAG (Retrieval-Augmented Generation) Pipeline

**Business value.** Deflects repetitive shipping, returns, payment, and sizing questions while keeping
answers grounded in authored policy. The `flag_unanswered` path turns gaps into a product backlog
instead of hallucinations.

**Engineering design.**

- Store policies and sizing guidance are split into **34 chunks** and embedded into 384-dimensional
  vectors.
- Each chunk is paired with **120 hypothetical shopper questions** across Indonesian and English;
  question vectors point back to their parent chunk.
- Retrieval fuses three signals with RRF (`k=60`): **direct chunk vectors**, **hypothetical-question
  vectors**, and **IDF-weighted bilingual lexical matching**.
- A bounded TTL cache keeps policy retrieval cheap on repeated questions.
- Evaluation uses **RAGAS** (`ragas.evaluate`) with ID-based and non-LLM context metrics over 12
  bilingual policy queries at `k=3`.

**Measured result:** **1.0000 Context Recall** and **0.9167 Context Precision** — the hybrid strategy
recovers every ground-truth chunk while keeping the context window small.

### System 5 — Hardened Agent Orchestration

**Business value.** A copilot that can browse, size, recommend, and add to cart is only useful if it
is safe, bounded, and cheap. This system is the difference between a demo and a deployable agent.

**Engineering design.**

- **Runtime:** LangChain v1 `create_agent` on a **LangGraph** state machine, with conversation memory
  checkpointed in PostgreSQL (`PostgresSaver`) keyed by a browser-generated `thread_id`.
- **Three-class guardrail layer:** deterministic regex plus LLM classification route every message into
  `greeting` (fast-path canned reply), `off_topic` (polite deflection), `injection` (prompt-injection
  and jailbreak defense), or `on_topic`. Guardrail replies never consume a full agent turn.
- **Seven deterministic tools:** `search_catalog`, `get_product_details`, `recommend_size`,
  `build_outfit`, `lookup_store_policy`, `add_to_cart`, and `flag_unanswered`.
- **Mathematical invariants live in code, not prompts:** `build_outfit` sums item prices and rejects
  any combination where `total > budget`; `add_to_cart` re-reads the server-side price; `flag_unanswered`
  records the unresolved question for the operations backlog.
- **Provider resilience:** OpenRouter is preferred, Gemini is the fallback, and rate limits are
  enforced per client IP. `COPILOT_FAKE_LLM=1` provides a deterministic fake tool-calling model so CI
  never depends on a live provider.

### System 6 — Production Operations Telemetry & KPI Dashboard

**Business value.** Leadership can see whether the AI surfaces are making money, losing money, or
annoying customers — in one screen, without asking an engineer to run a query.

**Engineering design.**

- A Next.js executive dashboard at **`/admin/kpi`**, gated by `X-Admin-Token`.
- Live panels for:
  - **Latency:** copilot p50/p95 against the **< 5.0 s p95 SLO**.
  - **Unit economics:** cost per turn, conversation, resolved conversation, and assisted order.
  - **Conversion:** funnel rates and copilot-versus-non-copilot cohorts.
  - **Search health:** zero-result rate and top zero-result queries.
  - **Copilot quality:** unanswered rate, deflection rate, and thumbs up/down.
  - **Unanswered backlog:** the actual customer questions the system could not answer.
- Backed by `GET /admin/kpi`, which returns explicit `synthetic` and `caveat` metadata rather than
  presenting demo data as audited revenue.

---

## 3. Empirical Benchmarks & Verification

All headline numbers are traceable to committed artifacts in [`pipelines/results/`](./pipelines/results)
and are reproducible with the scripts in [`pipelines/`](./pipelines). The source ledger — what each
number means, its provenance tier, and how to re-run it — is
[`pipelines/BENCHMARKS.md`](./pipelines/BENCHMARKS.md).

| System | Metric | Baseline | Result | Evidence |
|---|---|---:|---:|---|
| Bilingual search | Overall nDCG@10 | 0.794 (BM25) | **0.894** (hybrid, multilingual) | [`search_eval_multilingual.json`](./pipelines/results/search_eval_multilingual.json) |
| Bilingual search | Indonesian nDCG@10 | 0.334 (BM25) | **0.765** (**+129%**) | [`search_eval_multilingual.json`](./pipelines/results/search_eval_multilingual.json) |
| Bilingual search | HitRate@10 (HR@10) | 84.4% (BM25) | **100.0%** | [`search_eval_multilingual.json`](./pipelines/results/search_eval_multilingual.json) |
| Store knowledge RAG | RAGAS Context Recall | 0.667 (direct chunks) | **1.0000** | [`rag_eval.json`](./pipelines/results/rag_eval.json) |
| Store knowledge RAG | RAGAS Context Precision | 0.542 (direct chunks) | **0.9167** | [`rag_eval.json`](./pipelines/results/rag_eval.json) |
| Recommendations | HR@10 (sequential leave-one-out) | 0.18% (popularity) | **7.96%** (item CF) | [`recs_eval.json`](./pipelines/results/recs_eval.json) |
| Copilot behaviour | 54-scenario CI suite | — | **54/54 pass, 0 budget violations** | [`copilot_eval.json`](./pipelines/results/copilot_eval.json) |
| Unit economics | Cost per assisted order | $0.005 SLO | **$0.0016** | `GET /admin/kpi` |
| Operations | Copilot p95 latency | < 5.0 s SLO | Live in `/admin/kpi` | `GET /admin/kpi` |

The copilot CI suite verifies four jobs end to end:

- **Guardrails** — 18/18 cases across greeting, off-topic, injection, and on-topic routing.
- **Sizing** — 18/18 cases, including unsupported categories returning `applicable=false`.
- **Budget outfits** — 8/8 cases with **zero** `total > budget` invariant violations.
- **Policy RAG** — 10/10 cases with recall@3 ≥ 0.9.

### Reproduce the benchmarks

```bash
# Information retrieval: BM25 vs dense vs hybrid
python pipelines/eval_search.py --api-url http://localhost:8001 \
  --modes bm25,dense,hybrid --out pipelines/results/search_eval.json

# Store knowledge retrieval with RAGAS
python pipelines/eval_rag.py --out pipelines/results/rag_eval.json

# Item-to-item recommendations
python pipelines/eval_recs.py --out pipelines/results/recs_eval.json

# 54-scenario copilot behaviour suite
python pipelines/eval_copilot.py --out pipelines/results/copilot_eval.json

# End-to-end application tests (with the Docker stack running)
pytest api/tests/
cd shop && npm test -- --run
```

---

## 4. Quickstart & Local Development

The entire stack is containerised: Next.js storefront, FastAPI backend, and PostgreSQL 16 with
pgvector. The database is auto-seeded on first boot with the catalog, reviews, embeddings,
recommendations, and store knowledge — no downloads and no manual pipeline step.

### Prerequisites

- Docker with the Compose plugin.
- Optional: a free [OpenRouter API key](https://openrouter.ai/keys) to enable the copilot. Everything
  except the copilot works without one.

### Run

```bash
git clone https://github.com/marknshoot/toko-marcell.git
cd toko-marcell

# Optional: enable the copilot
cp .env.example .env
# edit .env → OPENROUTER_API_KEY=sk-or-...

docker compose up -d --build
```

| Service | URL |
|---|---|
| Storefront | [http://localhost:3000](http://localhost:3000) |
| API + interactive docs | [http://localhost:8001/docs](http://localhost:8001/docs) |
| Executive KPI dashboard | [http://localhost:3000/admin/kpi](http://localhost:3000/admin/kpi) (`dev-admin-token`) |
| Health check | [http://localhost:8001/health](http://localhost:8001/health) |

```bash
docker compose logs -f    # stream logs
docker compose down       # stop the stack
```

### What works without an LLM key

Catalog browsing, department filters, **hybrid text search**, recommendations, reviews, the sizing
widget, and the demo checkout all run against the seeded database. Without a key, `/copilot/chat`
returns a clean `503` instead of crashing, and the copilot panel explains that the assistant is
temporarily unavailable.

### Backend development without Docker

```bash
cd api
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

export DATABASE_URL=postgresql://toko:toko@localhost:5432/toko
export ADMIN_TOKEN=dev-admin-token
uvicorn main:app --reload --port 8000
```

The API runs `alembic upgrade head` and seeds the knowledge base on startup. Migrations are
hand-written SQL, idempotent, and safe to re-run.

### Configuration

Copy [`.env.example`](./.env.example) and set only what you need. The most important variables:

| Variable | Purpose | Default |
|---|---|---|
| `OPENROUTER_API_KEY` | Preferred copilot provider; without it the copilot is disabled | empty |
| `GEMINI_API_KEY` | Fallback copilot provider | empty |
| `DATABASE_URL` | PostgreSQL + pgvector connection string | local compose DSN |
| `ADMIN_TOKEN` | Protects `/search/reindex` and `/admin/kpi` | empty (endpoints return `503`) |
| `TEXT_EMBED_MODEL` | 384-d multilingual embedding model | `paraphrase-multilingual-MiniLM-L12-v2` |
| `COPILOT_RATE_LIMIT_PER_MIN` / `_PER_DAY` | Per-IP copilot rate limits | `10` / `100` |
| `ENABLE_RERANKER` | Optional cross-encoder stage-2 reranking | `false` |

The full reference is in [`api/README.md`](./api/README.md#environment-variables).

---

## 5. Repository Map

```
toko-marcell/
├── api/                    FastAPI service, LangGraph agent, Alembic migrations, tests
│   ├── routers/            HTTP surface: search, catalog, sizing, recs, copilot, admin, checkout
│   ├── knowledge/          Authored store policies, size charts, category → size-kind map
│   ├── migrations/         Versioned schema (0001 → 0005)
│   └── tests/              Unit + integration suites
├── shop/                   Next.js storefront, copilot UI, sizing widget, /admin/kpi
├── pipelines/              Offline catalog build, embeddings, recommendation training, evaluations
│   └── results/            Committed benchmark artifacts
├── data/seed/              PostgreSQL seed dump (catalog, reviews, interactions, knowledge)
├── docs/                   Business case, metrics reference, SLOs, case study
├── scripts/                Traffic simulator and operational helpers
├── docker-compose.yml      Storefront + API + PostgreSQL
└── render.yaml             Render deployment definition
```

---

## Documentation

| Document | Audience | Contents |
|---|---|---|
| [`api/README.md`](./api/README.md) | Backend engineers | Architecture, endpoint contracts, environment variables, database schema |
| [`pipelines/BENCHMARKS.md`](./pipelines/BENCHMARKS.md) | AI engineers | Metric provenance and reproduction commands |
| [`docs/METRICS.md`](./docs/METRICS.md) | Product & analytics | Every metric, its definition, and its caveats |
| [`docs/SLOS.md`](./docs/SLOS.md) | Operations | Latency, availability, cost, and fallback targets |
| [`docs/BUSINESS_CASE.md`](./docs/BUSINESS_CASE.md) | Hiring managers | Personas, hypotheses, KPI tree, non-goals |
| [`docs/CASE_STUDY.md`](./docs/CASE_STUDY.md) | Stakeholders | Simulated end-to-end business outcomes with limitations |

---

## License

Released under the MIT License. See [`LICENSE`](./LICENSE).

The product data is derived from the public Amazon Reviews 2023 dataset for portfolio and research
use. Product images remain the property of their respective owners.
