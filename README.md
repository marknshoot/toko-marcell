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
records**. The shopper-facing experience is served by Next.js on Vercel; the FastAPI and LangGraph
agent runtime are Dockerised locally and deployed to Render, against PostgreSQL 16 + pgvector on Neon
Serverless.

| Destination | Link |
|---|---|
| **Live storefront** | [https://toko-marcell.vercel.app](https://toko-marcell.vercel.app) |
| **Executive Key Performance Indicator (KPI) dashboard** | [https://toko-marcell.vercel.app/admin/kpi](https://toko-marcell.vercel.app/admin/kpi) — demo login: `admin` / `marcell-admin-2026` |
| **API documentation (OpenAPI)** | [https://toko-marcell-api.onrender.com/docs](https://toko-marcell-api.onrender.com/docs) |
| **Health check** | [https://toko-marcell-api.onrender.com/health](https://toko-marcell-api.onrender.com/health) |
| **Hugging Face embedding model** | [`Marcell-Kristianto/paraphrase-multilingual-MiniLM-L12-v2-id-en-onnx`](https://huggingface.co/Marcell-Kristianto/paraphrase-multilingual-MiniLM-L12-v2-id-en-onnx) — 124 MB pruned multilingual ONNX |

> **Both halves of the backend sleep.** The FastAPI service runs on Render's free tier, which spins
> down after 15 minutes of inactivity, and PostgreSQL 16 + pgvector runs on Neon Serverless, whose
> compute auto-suspends after 5 minutes. A first request can therefore take 30–50 seconds, so the
> storefront shows an explicit **dual wake-up state** — Render API *and* Neon database — that only
> clears once `/health` reports both awake, instead of failing silently.

---

## Contents

1. [The Business Problem & Commercial Value](#1-the-business-problem--commercial-value)
   - 1.1 [Why Toko Marcell exists](#11-why-toko-marcell-exists)
   - 1.2 [Why the AI Sales Copilot is needed](#12-why-the-ai-sales-copilot-is-needed)
   - 1.3 [Why the Recommendation System is needed](#13-why-the-recommendation-system-is-needed)
   - 1.4 [Unit Economics Discipline](#14-unit-economics-discipline)
   - 1.5 [Design Constraints & Free-Tier Architectural Realities](#15-design-constraints--free-tier-architectural-realities)
2. [The Six Core Technical Systems](#2-the-six-core-technical-systems)
3. [Empirical Benchmarks & Verification](#3-empirical-benchmarks--verification)
4. [Data Architecture & Schema](#4-data-architecture--schema)
5. [Quickstart & Local Development](#5-quickstart--local-development)
6. [Repository Map](#6-repository-map)

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

### 1.5 Design Constraints & Free-Tier Architectural Realities

Toko Marcell runs a full retrieval stack, an agent runtime, and a managed database on free tiers, with
no paid inference in the request path. That is a design input, not a footnote: five realities shaped
the architecture instead of being hidden behind a disclaimer.

| # | Constraint | Hard limit | Architectural response |
|---|---|---|---|
| 1 | No public store-policy corpus | Indonesian fashion SOPs are not published anywhere | 34 authored policy chunks + 120 bilingual evaluation questions, gated in CI |
| 2 | Free-tier LLM APIs | RPM/TPM caps, 0-token burst rejections, no vision, provider outages | Fallback model chain, 3-call loop cap, offline fake model, `Literal`-typed tool schemas |
| 3 | Render free compute | 512 MB RAM, spin-down after 15 minutes | 124 MB pruned ONNX embedder, under 240 MB API footprint, explicit wake-up overlay |
| 4 | Neon free Postgres | Auto-suspend after 5 minutes, 1–3 s wake handshake, capped connections | `psycopg_pool` bounded to 1–5 connections, dual `/health` `SELECT 1;` probe |
| 5 | Amazon Fashion dataset | No inventory counts, USD-only prices, no live traffic | Synthesized IDR pricing, CF + popularity fallback, hybrid BM25 + pgvector |

#### 1. No public e-commerce store knowledge base — synthetic domain authoring

**Why it was needed.** Indonesian fashion retailers do not publish their raw internal operational
policies. Shipping tiers, return windows, hygiene exclusions, and payment rules live in internal SOPs
and WhatsApp templates, so the public web holds nothing to retrieve — and evaluating RAG on someone
else's FAQ would measure world knowledge instead of retrieval quality.

**Solution.** Authored **34 realistic Markdown policy chunks** covering shipping tiers (Rp 15.000
Jabodetabek, Rp 25.000 outside Java, free above Rp 300.000), the 7-day sizing and return guarantee,
hygiene exclusions on worn or washed goods, payment channels including VA/QRIS and the COD
availability rule, producer warranties, and category sizing rules — then paired them with **120
bilingual hypothetical shopper questions** (Indonesian, English, and code-switched) so RAG recall is
measurable in CI without live shoppers. The suite holds **1.0000 context recall** and
**0.9167 context precision** at `k=3`.

#### 2. LLM free-tier API limits & cost guardrails

**Constraints.** Free provider tiers enforce strict rate limits (requests per minute and tokens per
minute, plus occasional 0-token burst caps that reject a request before it produces anything), offer
no multimodal vision support for outfit-photo inputs, and are subject to provider outages.

**Solutions.**

- **Multi-tier fallback chain** (`LLM_FALLBACK_MODELS`): the configured model is tried first and the
  chain is deduplicated, so a `429`, a quota cap, or a dead endpoint degrades to the next tier instead
  of failing the shopper's turn.
- **Bounded tool loops**: `ModelCallLimitMiddleware(run_limit=3, exit_behavior="end")` caps model
  calls per turn, which keeps a small model's tool loop finite and the p95 latency budget provable.
- **Deterministic `COPILOT_FAKE_LLM=1`**: a fake tool-calling model lets the 54-scenario copilot suite
  and the application tests run offline in CI — no key, no quota, no flaky provider.
- **MCP-style hardened Pydantic schemas**: tool arguments are validated against typed fields and
  `Literal` enums rather than free-form strings, so a small model such as `ministral-8b-latest` cannot
  hallucinate an invalid department or filter and burn a retry; malformed arguments fail validation
  before they reach SQL.

#### 3. Render free-tier compute — 512 MB RAM and cold starts

**Constraints.** The free instance has a **512 MB** memory ceiling and spins down after
**15 minutes** of inactivity. A PyTorch/Transformers embedding stack easily exceeds 1.5 GB and is
killed by the OOM killer mid-request; the next request after a spin-down pays a 30–50 second cold
start.

**Solutions.**

- **Quantized, pruned ONNX embedder**: `paraphrase-multilingual-MiniLM-L12-v2` is pruned to **124 MB**
  and served through **ONNX Runtime + FastEmbed**
  ([`Marcell-Kristianto/paraphrase-multilingual-MiniLM-L12-v2-id-en-onnx`](https://huggingface.co/Marcell-Kristianto/paraphrase-multilingual-MiniLM-L12-v2-id-en-onnx))
  instead of PyTorch, with a single ONNX thread, `enable_cpu_mem_arena=False`, and the cross-encoder
  reranker disabled on this host.
- **A measured memory budget**: the API baseline stays under **240 MB** (~80 MB with dense search
  disabled, ~380 MB for the full hybrid dense + lexical stack), so hybrid retrieval runs inside the
  ceiling instead of being cut for safety.
- **Explicit `ServerWakeup` overlay**: a cold start is surfaced, never silent — the storefront shows
  the waking state, polls `/health`, and reveals the catalog once the API and database answer.

#### 4. Neon Serverless PostgreSQL — auto-suspend and connection pooling

**Constraints.** Neon's free tier auto-suspends compute after **5 minutes** of inactivity and then
charges a **1–3 second** wake handshake on the next query. It also limits available connection slots,
so a per-request `connect()` or an oversized pool is simply denied.

**Solutions.**

- **A bounded connection pool**: `psycopg_pool.ConnectionPool` with `DB_POOL_MIN=1` and
  `DB_POOL_MAX=5`, opened once in the FastAPI lifespan and closed on shutdown. Outside the server
  (pipelines, standalone tests) `get_conn()` falls back to a one-shot `psycopg.connect()`, so no
  component depends on the pool existing.
- **A dual-backend health probe**: `GET /health` executes `SELECT 1;` through that same pool and
  reports the two platforms separately:
  `{"status": "ok", "services": {"api": "ok", "database": "ok"}}` when both answer, and a `503`
  `{"status": "waking", "services": {"api": "ok", "database": "waking"}}` payload while Neon is
  suspended. The storefront polls that contract and only dismisses the waking state once **both**
  Render and Neon are awake, so a live API can never mask a suspended database.

#### 5. Amazon Fashion dataset — cold start and synthetic pricing

**Constraints.** The public Amazon Reviews 2018 (Clothing, Shoes & Jewelry) corpus has no real-time
inventory counts and quotes prices in USD, while an Indonesian storefront must show stock and IDR
totals. Because the dataset is historical, a first-time visitor also has no interaction history to
recommend from.

**Solutions.**

- **Synthesized IDR pricing**: prices are converted at a pinned USD→IDR rate and rounded to
  shelf-ready hundreds (retail-psychological rounding), with `price_idr` as the server-authoritative
  column and `price_usd` kept only as provenance. Checkout re-reads the server price, so a tampered
  client cannot change the total.
- **Cold-start recommendations**: item-to-item collaborative filtering over the interaction matrix,
  with **departmental popularity** as the documented fallback for items and sessions without enough
  co-occurrence signal — a new visitor still sees a useful shelf instead of an empty rail.
- **Code-switched retrieval**: hybrid BM25 + dense pgvector fused with RRF, so `celana jeans levis
  501` and `running shoes pria` both resolve against the catalog the storefront is actually showing.

**Net trade-off.** The stack is slower on a cold start and capped at one embedding thread; in
exchange it is free, reproducible, and cheap enough to benchmark on every commit — the same limits that
constrain it are what make the CI gates affordable.

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
- **Dense retrieval:** 384-dimensional multilingual embeddings from a **vocabulary-pruned,
  100% numerically equivalent** copy of `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
  (`Marcell-Kristianto/paraphrase-multilingual-MiniLM-L12-v2-id-en-onnx`), stored in PostgreSQL and
  queried through a **pgvector HNSW** cosine index. Pruning non-Latin vocabulary cuts the ONNX file
  from 225 MB to 124 MB and runtime RSS from ~630 MB to ~380 MB without changing any embedding.
- **Fusion:** Reciprocal Rank Fusion (**RRF, `k=60`**) merges the lexical and dense rankings without
  calibrating incompatible score distributions.
- **Serving modes:** `hybrid` (default), `dense`, and `bm25`. Search results are deduplicated by
  product title before pagination, so the page count reflects what the shopper can actually see.
- **Memory-constrained deployments:** the pruned model runs at **~380 MB RSS** with one ONNX thread
  and `enable_cpu_mem_arena=False`, so `render.yaml` enables `ENABLE_DENSE_SEARCH=true` and Render's
  512 MB free tier serves the full hybrid dense + lexical stack. `ENABLE_DENSE_SEARCH=false` remains
  available for tighter hosts and degrades to BM25 + Indonesian query rewriting in ~80 MB;
  `mode=dense` requests fall back to BM25 instead of returning zero results.
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

How the pipeline works end to end:

```mermaid
flowchart TB
    subgraph OFFLINE["Offline ingestion"]
        direction TB
        SRC["api/knowledge/*.md<br/>store_policies.md + size_charts.md"] --> CHUNK["Chunking<br/>34 policy + sizing chunks"]
        CHUNK --> HYP["Hypothetical questions<br/>120 Indonesian + English shopper questions"]
        CHUNK --> EMB["FastEmbed MiniLM<br/>384-d multilingual ONNX"]
        HYP --> EMB
        EMB --> K[("store_knowledge<br/>chunk text + embedding")]
        EMB --> KQ[("store_knowledge_questions<br/>question + chunk_id + embedding")]
        EMB --> CACHE[("knowledge_seed_cache.json<br/>precomputed vectors")]
    end

    subgraph ONLINE["Online retrieval — lookup_store_policy"]
        direction TB
        Q["Shopper question<br/>Indonesian, English, or code-switched"] --> TTL{"10-minute TTL cache<br/>256 entries"}
        TTL -->|hit| ANSWER
        TTL -->|miss| QEMB["Embed query<br/>384-d MiniLM"]
        QEMB --> S1["Signal 1 — direct chunk vector<br/>pgvector cosine over every chunk"]
        QEMB --> S2["Signal 2 — hypothetical question vector<br/>top 5x limit, deduplicated to parent chunk"]
        Q --> S3["Signal 3 — bilingual BM25 / IDF lexical<br/>over chunk title + content"]
        K -.-> S1
        KQ -.-> S2
        K -.-> S3
        S1 --> RRF["Reciprocal Rank Fusion<br/>k = 60"]
        S2 --> RRF
        S3 --> RRF
        RRF --> TOP3["Top-3 grounded policy chunks"]
        TOP3 --> STORE["Store result in the TTL cache"]
        STORE --> ANSWER["Copilot answer<br/>with grounded policy citations"]
    end
```

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

How one turn flows through the hardened runtime:

```mermaid
flowchart TB
    MSG["Shopper message<br/>thread_id + session_id"] --> SAN["Sanitization<br/>strip control characters, trim, cap length"]
    SAN --> TRIAGE{"Pre-agent guardrail triage<br/>guardrail.classify()"}
    TRIAGE -->|greeting| GREET["Canned greeting reply<br/>0 agent model calls"]
    TRIAGE -->|off_topic| OFF["Polite scope deflection<br/>0 agent model calls"]
    TRIAGE -->|injection| INJ["Prompt-injection refusal<br/>0 agent model calls"]
    TRIAGE -->|on_topic| ASSEMBLE["Dynamic context assembly"]

    GREET --> FASTLOG[("Fast log path → copilot_turns<br/>guardrail label, outcome = guardrail")]
    OFF --> FASTLOG
    INJ --> FASTLOG

    ASSEMBLE --> CONTEXT["Authoritative context block<br/>pinned page product re-read from the DB<br/>shown-products registry<br/>recent browsing events"]

    subgraph AGENT["LangGraph agent execution loop — create_agent"]
        direction TB
        CONTEXT --> MODEL["Model call<br/>OpenRouter primary, Gemini failover"]
        MODEL --> CAP{"ModelCallLimitMiddleware<br/>cap = 3 model calls"}
        CAP -->|tool calls| TOOLS["7 deterministic tools<br/>search_catalog, get_product_details<br/>recommend_size, build_outfit<br/>lookup_store_policy, add_to_cart<br/>flag_unanswered"]
        TOOLS --> INV["Code invariants enforced<br/>outfit total summed against budget<br/>product price re-read server-side<br/>size refusal instead of a guess"]
        INV --> MODEL
        CAP -->|end| REPLY["Grounded reply<br/>product cards + ui_actions"]
    end

    REPLY --> CKPT[("PostgresSaver checkpoint<br/>thread state keyed by thread_id")]
    REPLY --> METRICS["OpenTelemetry-aligned turn metrics<br/>guardrail, outcome, tools, latency<br/>tokens, model, cost_usd"]
    METRICS --> TURNS[("copilot_turns")]
    FASTLOG --> TURNS
```

Guardrail labels never enter the agent loop, so a greeting, deflection, or refusal costs zero agent
model calls. With `GUARDRAIL_LLM_MODE=ambiguous`, a decisive regex label also skips the small
classifier call, making the entire triage path LLM-free.

### System 6 — Production Operations Telemetry & KPI Dashboard

**Business value.** Leadership can see whether the AI surfaces are making money, losing money, or
annoying customers — in one screen, without asking an engineer to run a query.

**Engineering design.**

- A Next.js executive dashboard at **`/admin/kpi`**, gated by Argon2id admin login (15-minute access
  JWT + HttpOnly rotating refresh token). The demo login is `admin` / `marcell-admin-2026`; legacy
  `X-Admin-Token` scripts keep working.
- Live panels for:
  - **Latency:** copilot p50/p95 against the **< 5.0 s p95 SLO**.
  - **Unit economics:** cost per turn, conversation, resolved conversation, and assisted order.
  - **Conversion:** funnel rates and copilot-versus-non-copilot cohorts.
  - **Search health:** zero-result rate and top zero-result queries.
  - **Copilot quality:** unanswered rate, deflection rate, and thumbs up/down.
  - **Unanswered backlog:** the actual customer questions the system could not answer.
- **OpenTelemetry-aligned structured JSON logging** for every request: nine standard fields
  (timestamp, level, service, version, environment, message, trace/span/request IDs), W3C
  `traceparent` propagation or automatic ID generation, and newline-delimited JSON streamed to
  `stdout` for Render, Datadog, CloudWatch and similar collectors with no sidecar agent.
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

## 4. Data Architecture & Schema

The schema is owned by Alembic revisions `0001` → `0006` and is visualised once, below, so the
catalog, order, telemetry, retrieval and authentication planes can be read together. The column-level
dictionary, per-table notes and the full index list live in
[`api/README.md#database-schema`](./api/README.md#database-schema).

```mermaid
erDiagram
    %% ── Catalog ──────────────────────────────────────────────
    products {
        INTEGER id PK "internal product id"
        TEXT asin UK "Amazon-style product key"
        TEXT title "display title, search dedupe key"
        TEXT brand
        NUMERIC price_usd "source price"
        INTEGER price_idr "server-authoritative IDR price"
        TEXT department "facet"
        TEXT category "facet"
        JSONB category_path
        TEXT description
        JSONB features
        TEXT image_url
        NUMERIC avg_rating
        INTEGER rating_count
        JSONB also_buy
        JSONB also_view
        VECTOR embedding "optional English MiniLM, 384-d, HNSW"
        VECTOR embedding_ml "default multilingual column, 384-d, HNSW"
    }
    reviews {
        SERIAL id PK
        TEXT asin "logical FK to products.asin, not enforced"
        NUMERIC rating
        TEXT summary
        TEXT comment
        TEXT author
        BOOLEAN verified
        TEXT review_date
        TIMESTAMPTZ created_at
    }
    item_recommendations {
        TEXT asin PK "logical FK to products.asin"
        JSONB recs "ordered ASIN neighbours"
    }

    %% ── Orders ───────────────────────────────────────────────
    orders {
        BIGSERIAL id PK
        TEXT token UK "secret receipt token"
        INTEGER total_idr "server-computed total"
        INTEGER item_count
        TEXT status "paid"
        TEXT session_id "anonymous session correlation"
        TIMESTAMPTZ created_at
    }
    order_items {
        BIGSERIAL id PK
        BIGINT order_id FK "ON DELETE CASCADE to orders.id"
        TEXT asin "logical FK to products.asin"
        TEXT title "snapshot at purchase"
        INTEGER qty
        INTEGER unit_price_idr "snapshot at purchase"
    }

    %% ── Telemetry ────────────────────────────────────────────
    events {
        BIGSERIAL id PK
        TEXT session_id
        TEXT event_type "view_product, search, add_to_cart, checkout_start, purchase_mock, copilot"
        TEXT asin "logical FK to products.asin"
        TEXT query
        INTEGER results_count
        INTEGER qty
        INTEGER price_idr
        TIMESTAMPTZ created_at
    }
    copilot_turns {
        BIGSERIAL id PK
        TEXT thread_id "LangGraph thread, logical key"
        TEXT session_id
        TIMESTAMPTZ created_at
        TEXT guardrail "greeting, off_topic, injection, or null"
        TEXT outcome "answered, guardrail, unanswered, cart, error"
        TEXT_ARRAY tool_names "text[] of tools invoked"
        REAL latency_ms
        INTEGER input_tokens
        INTEGER output_tokens
        TEXT model
        NUMERIC cost_usd
        TEXT unanswered_question "populated by flag_unanswered"
    }
    copilot_feedback {
        BIGSERIAL id PK
        TEXT thread_id "logical key to a copilot thread"
        TEXT session_id
        SMALLINT rating "-1 or +1"
        TIMESTAMPTZ created_at
    }

    %% ── RAG knowledge ────────────────────────────────────────
    store_knowledge {
        SERIAL id PK
        TEXT category
        TEXT title
        TEXT content
        VECTOR embedding "384-d multilingual, HNSW cosine"
    }
    store_knowledge_questions {
        SERIAL id PK
        INTEGER chunk_id FK "ON DELETE CASCADE to store_knowledge.id"
        TEXT question "hypothetical ID/EN shopper question"
        TEXT lang "id or en"
        VECTOR embedding "384-d, HNSW cosine"
    }

    %% ── Admin auth ───────────────────────────────────────────
    admin_users {
        SERIAL id PK
        VARCHAR username UK "login name"
        VARCHAR password_hash "Argon2id digest"
        VARCHAR role "admin"
        TIMESTAMPTZ created_at
    }
    admin_refresh_tokens {
        SERIAL id PK
        INTEGER user_id FK "ON DELETE CASCADE to admin_users.id"
        VARCHAR token_hash UK "SHA-256 of the raw refresh token"
        TIMESTAMPTZ expires_at
        BOOLEAN revoked
        TIMESTAMPTZ created_at
    }

    products ||--o{ reviews : "asin"
    products ||--o{ item_recommendations : "asin"
    products ||--o{ events : "asin"
    orders ||--o{ order_items : "order_id"
    orders ||--o{ events : "session_id"
    store_knowledge ||--o{ store_knowledge_questions : "chunk_id"
    admin_users ||--o{ admin_refresh_tokens : "user_id"
    copilot_turns }o--o{ copilot_feedback : "thread_id"
```

Legend: `PK` primary key, `UK` unique key, `FK` enforced foreign key. Relationships drawn without an
enforced constraint (`reviews.asin`, `item_recommendations.asin`, `events.asin`, `events.session_id`,
`copilot_feedback.thread_id`) are correlated logical keys, not database constraints. `VECTOR` is a
pgvector `vector(384)`; `TEXT_ARRAY` is PostgreSQL `text[]`.

The complete schema is **12 tables**: the **10 core domain tables** (`products`, `reviews`,
`item_recommendations`, `orders`, `order_items`, `events`, `copilot_turns`, `copilot_feedback`,
`store_knowledge`, `store_knowledge_questions`) plus the two authentication tables added by revision
`0006` (`admin_users`, `admin_refresh_tokens`).

The LangGraph checkpointer tables (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`) and the
`copilot_thread_activity` TTL sidecar are created at runtime by `PostgresSaver.setup()`, not by
Alembic, so they are intentionally outside the migration-owned ERD.

### 4.1 Domains and ownership

| Domain | Tables | Purpose | Introduced by |
|---|---|---|---|
| **Catalog** | `products`, `reviews`, `item_recommendations` | Products, multilingual embeddings, review text, offline item-to-item neighbours | `0001`, `0002` (drops `image_embedding` in `0005`) |
| **Orders** | `orders`, `order_items` | Server-priced orders with prices snapshotted at purchase | `0001` |
| **Telemetry** | `events`, `copilot_turns`, `copilot_feedback` | Funnel events, per-turn unit economics, thumbs feedback | `0001`, `0003`, `0004` |
| **RAG** | `store_knowledge`, `store_knowledge_questions` | 34 policy chunks and 120 hypothetical questions with 384-d vectors | `0001` |
| **Admin auth** | `admin_users`, `admin_refresh_tokens` | Argon2id credentials and SHA-256 refresh-token rotation with reuse detection | `0006` |

### 4.2 Indexes

| Table | Index | Definition |
|---|---|---|
| `products` | `products_department_idx` | btree (`department`) |
| `products` | `products_category_idx` | btree (`category`) |
| `products` | `products_embedding_idx` | HNSW cosine (`embedding`) |
| `products` | `products_embedding_ml_idx` | HNSW cosine (`embedding_ml`) |
| `reviews` | `reviews_asin_idx`, `reviews_rating_idx` | btree (`asin`), btree (`rating`) |
| `item_recommendations` | `item_recs_asin_idx` | btree (`asin`) |
| `events` | `events_session_idx`, `events_type_created_idx` | btree (`session_id`), btree (`event_type`, `created_at DESC`) |
| `orders` | `orders_created_idx` | btree (`created_at DESC`) |
| `order_items` | `order_items_order_idx` | btree (`order_id`) |
| `store_knowledge` | `store_knowledge_category_idx`, `store_knowledge_embedding_idx` | btree (`category`), HNSW cosine (`embedding`) |
| `store_knowledge_questions` | `store_knowledge_q_chunk_idx`, `store_knowledge_q_embedding_idx` | btree (`chunk_id`), HNSW cosine (`embedding`) |
| `copilot_turns` | `copilot_turns_created_idx`, `copilot_turns_thread_idx` | btree (`created_at DESC`), btree (`thread_id`) |
| `copilot_feedback` | `copilot_feedback_created_idx` | btree (`created_at DESC`) |
| `admin_refresh_tokens` | `admin_refresh_tokens_token_hash_idx`, `admin_refresh_tokens_user_idx` | btree (`token_hash`), btree (`user_id`) |

Every retrieval path is index-backed: `/search` reads `products_embedding_ml_idx`,
`lookup_store_policy` reads both HNSW knowledge indexes, the funnel aggregates `events_type_created_idx`,
and the KPI dashboard reads `copilot_turns_created_idx`.

---

## 5. Quickstart & Local Development

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
| Executive KPI dashboard | [http://localhost:3000/admin/kpi](http://localhost:3000/admin/kpi) (`admin` / `marcell-admin-2026`) |
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
| `ADMIN_TOKEN` | Legacy static token for `/search/reindex` and `/admin/kpi` scripts | empty |
| `JWT_SECRET_KEY` | Signs 15-minute admin access JWTs | dev placeholder |
| `ACCESS_TOKEN_EXPIRE_MINUTES` / `REFRESH_TOKEN_EXPIRE_DAYS` | Dual-JWT session lifetimes | `15` / `7` |
| `TEXT_EMBED_MODEL` | 384-d pruned multilingual embedding model (124 MB ONNX, ~380 MB RSS) | `Marcell-Kristianto/paraphrase-multilingual-MiniLM-L12-v2-id-en-onnx` |
| `COPILOT_RATE_LIMIT_PER_MIN` / `_PER_DAY` | Per-IP copilot rate limits | `10` / `100` |
| `ENABLE_RERANKER` | Optional cross-encoder stage-2 reranking | `false` |
| `ENABLE_DENSE_SEARCH` | Dense/embedding retrieval; enabled on Render with the pruned model | `true` |

The full reference is in [`api/README.md`](./api/README.md#environment-variables).

---

## 6. Repository Map

```
toko-marcell/
├── api/                    FastAPI service, LangGraph agent, Alembic migrations, tests
│   ├── routers/            HTTP surface: search, catalog, sizing, recs, copilot, admin, checkout
│   ├── knowledge/          Authored store policies, size charts, category → size-kind map
│   ├── migrations/         Versioned schema (0001 → 0006)
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
