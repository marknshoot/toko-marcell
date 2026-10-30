# Service-level objectives & unit-economics budgets

This document defines the **targets** for Toko Marcell's latency, availability and copilot spend.
It is a policy document, not a claim that every target is currently met: the committed telemetry
covers copilot turn latency and cost, while text/image search, sizing latency and copilot
time-to-first-token still need production instrumentation before an attainment percentage can be
quoted.

Companion metric definitions and caveats live in [`METRICS.md`](./METRICS.md).

## 1. Scope and definitions

| Term | Meaning here |
|---|---|
| **Window** | Rolling 30 days for availability and error budgets; rolling 7/30 days for latency and cost reporting. |
| **Successful request** | A request that returns its documented HTTP 2xx response inside the latency threshold. |
| **Failed request** | A 5xx response, timeout, connection failure, or copilot provider failure after fallback. Client 4xx/422 validation errors are excluded from availability because they are correctly rejected input. |
| **Server-side latency** | Time inside the API from request start to response completion (TTFT for streaming copilot). Browser network time, CDN time and cold-start model downloads are separate. |
| **Percentile** | `p50` = median; `p95` = 95th percentile over successful requests in the window. Report the sample size; p95 is unstable at small n. |

If a target is breached, the error budget is consumed even when the response eventually succeeds
outside the threshold.

## 2. Latency SLOs

| Surface | Measurement point | p50 target | p95 target | Measured today? | Notes |
|---|---|---:|---:|---|---|
| `GET /search` (text) | API handler, warm BM25/vector index, database reachable | **< 35 ms** | **< 80 ms** | **Policy only** — no timing table | Excludes first-request model loading and cold Render/Neon wake-up. Report by mode (`bm25`, `dense`, `hybrid`) because each does a different amount of work. |
| `POST /sizing/recommend` | API handler, deterministic chart lookup | **< 5 ms** | **< 15 ms** | **Policy only** — no timing table | No LLM and no external service; PostgreSQL product lookup is the only dependency. |
| `POST /copilot/chat` TTFT | First streamed token on `/copilot/chat/stream` (or first token-equivalent response for non-streaming) | **< 1.2 s** | **< 3.0 s** | **Policy only** — total-turn p50/p95 is live, TTFT is not | Includes guardrail classification, tool calls and the first model token. Total turn completion is a separate, larger budget. |

**Current instrumentation:** `/events/summary` and `/admin/kpi` expose copilot **total turn**
`p50/p95` latency (`copilot_turns.latency_ms`). TTFT, `/search` and
`/sizing/recommend` are not yet stored in a timing table, so **no latency-SLO attainment percentage
is claimed** until those timings are emitted. Do not quote a target as a measured result.

### Latency alert thresholds

| Signal | Warning | Page / rollback review |
|---|---|---|
| Text search p95 | > 80 ms for 15 min | > 150 ms for 15 min |
| Image search p95 | > 250 ms for 15 min | > 500 ms for 15 min |
| Sizing p95 | > 15 ms for 15 min | > 40 ms for 15 min |
| Copilot TTFT p95 | > 3.0 s for 15 min | > 6.0 s for 15 min |
| Copilot total-turn p95 | > 8 s for 15 min | > 15 s for 15 min |

These thresholds are proposed alerting policy; the repository does not include a pager or hosted
alert backend.

## 3. Availability & error budgets

| Service | Monthly availability SLO | Allowed failure budget (30 days) | Measured today? | What counts |
|---|---:|---:|---|---|
| Core REST (`/health`, `/products`, `/search`, `/sizing`, `/events`, `/checkout`, `/orders`) | **99.5%** | **0.5% ≈ 3 h 36 min** | **Policy only** — no hosted SLO calculation | 5xx, timeouts, database unavailability. Planned deploys should stay inside the budget, not reset it. |
| AI copilot (`/copilot/chat`, `/copilot/chat/stream`) | **99.0%** | **1.0% ≈ 7 h 12 min** | **Policy only** — outcomes logged, no hosted calculation | 5xx/502/503 after all provider fallbacks, provider timeout, or an unavailable LLM key. A guardrail-only canned reply is a **success**, because it is an intentional deterministic response. |

**Error-budget policy:**

- **50% consumed:** annotate the dashboard and review the failing dependency; no feature freeze.
- **75% consumed:** freeze non-essential AI/copilot changes until the burn rate returns below the
  budget trajectory; prioritise the failing path.
- **100% consumed:** freeze copilot releases, roll back the last latency/provider change if
  attributable, and publish the incident window and denominator.

A public-traffic availability number is not currently computed. The API rate limiter is permissive by
IP and there is no bot filtering, so any future public number must state its denominator.

## 4. Unit economics & cost budgets

### 4.1 Configured prices

The estimate uses **USD per one million tokens**, configured in `api/config.py`:

| Setting | Default | Meaning |
|---|---:|---|
| `LLM_INPUT_COST_PER_MILLION` | **$0.15** | Input/prompt tokens per 1M |
| `LLM_OUTPUT_COST_PER_MILLION` | **$0.60** | Output/completion tokens per 1M |

Per-turn estimate:

```
cost_usd = input_tokens  × LLM_INPUT_COST_PER_MILLION  / 1,000,000
         + output_tokens × LLM_OUTPUT_COST_PER_MILLION / 1,000,000
```

Example: 2,000 input + 500 output tokens at the defaults costs
`2,000 × 0.15 / 1M + 500 × 0.60 / 1M = $0.0006`.

`copilot_turns.cost_usd` is populated when the provider reports either token count. If neither count
is reported, the cost stays `NULL`; a missing cost is not treated as free. The configured price is a
**planning estimate**, not an invoice: provider discounts, free tiers, retries, guardrail calls and
currency conversion can differ. The pre-agent guardrail's LLM tokens are **not** in the recorded
agent usage, so the estimate is a lower bound when the guardrail runs.

### 4.2 Aggregates exposed by `GET /events/summary` and `GET /admin/kpi`

Both endpoints return a `unit_economics` block:

| Field | Formula | Denominator |
|---|---|---|
| `total_cost_usd` | `SUM(copilot_turns.cost_usd)` in the window | turns with reported cost; `null` if none |
| `avg_cost_per_turn_usd` | `total_cost_usd / COUNT(*)` copilot turns | all turns, including zero-token guardrail turns |
| `cost_per_conversation_usd` | `total_cost_usd / COUNT(DISTINCT thread_id)` | conversations in the window |
| `cost_per_resolved_conversation_usd` | `total_cost_usd / COUNT(DISTINCT thread_id WHERE outcome IN ('answered','cart'))` | resolved conversations |
| `cost_per_assisted_order_usd` | `total_cost_usd / COUNT(paid orders whose session has ≥1 copilot turn)` | assisted orders |

All divisions return `null` when the denominator is zero or total cost is unknown. `cost_usd` is
model spend only; it excludes embeddings, vector search, hosting, payment fees and human time.

### 4.3 Cost budgets

| Guardrail | Budget | Measured today? | Interpretation |
|---|---:|---|---|
| Cost per conversation | **< $0.005** | **Estimate live** — `unit_economics`, not attainment | Mean estimated model spend for one `thread_id` conversation. |
| Cost per assisted order | **< $0.02** | **Estimate live** — `unit_economics`, not attainment | Estimated model spend associated with paid orders in copilot sessions. This is a cost guardrail, not ROI. |

These are **budgets**, not measured historical results. Report numerator (spend), denominator
(conversations or assisted orders) and sample size beside every ratio. A low value can simply mean
few assisted orders or missing token usage; a high value can be one long conversation.

**Cost alerts:**

| Signal | Warning | Page / budget freeze |
|---|---|---|
| Cost per conversation (7-day) | > $0.005 | > $0.010 |
| Cost per assisted order (7-day, n ≥ 20 orders) | > $0.02 | > $0.04 |
| Daily total model spend | > 80% of the configured daily cap | > 100% of the cap |
| Turns with token usage missing | > 10% of turns in 24 h | > 25% |

The daily cap is an operational setting, not hard-coded in this repository; do not invent a dollar
figure without recording the cap and its window.

## 5. Provider fallback: OpenRouter → Gemini

The copilot builds a provider chain in `api/copilot_llm.py`:

```
OpenRouter (primary, with LLM_FALLBACK_MODELS routing)
  → optional generic OpenAI-compatible endpoint
  → Google Gemini
```

LangChain's `.with_fallbacks(...)` rolls over when the primary raises an exception, including
typical 429/5xx/connection failures. The application-level rules are:

| Condition | Action |
|---|---|
| OpenRouter 429 rate limit | Roll over to the next configured provider. Alert if fallback rate stays > 10% for 15 min. |
| OpenRouter 5xx / connection error / timeout | Roll over. Alert on any sustained 5xx rate > 5% for 5 min. |
| OpenRouter latency p95 > 8 s for 10 min | Treat as degraded; roll over if the fallback is healthy. |
| Gemini also fails | Return a friendly `502/503`; count the request against the copilot 99.0% budget. |
| 4xx validation, guardrail hit, or user input error | **Do not** roll over or retry; the primary did not fail. |
| Provider returns an unusable/malformed response | Log, retry/fallback where the client supports it, otherwise fail the turn and count it bad. |

**Rollover alerts:** primary error rate > 5% (5 min), fallback share > 10% of turns (15 min),
all-provider unavailability for > 1 minute (page), and a cost-budget breach from a fallback model
that is materially more expensive than the configured planning price. The fallback provider's token
prices must be reconciled before its spend is treated as equivalent to the primary's estimate.

## 6. Guardrail token-saving mode

`GUARDRAIL_LLM_MODE` controls the pre-agent classifier:

| Mode | Behaviour | Trade-off |
|---|---|---|
| `always` (default) | Every non-injection message calls the LLM guardrail, preserving contextual judgement for greetings, off-topic and subtle injections. Explicit injection regex still short-circuits. | Highest guardrail recall, highest token/latency cost. |
| `ambiguous` | Deterministic regex runs first; a decisive `greeting`, `off_topic` or `injection` label returns immediately. Only regex-ambiguous messages call the LLM. | Lower cost/latency; relies on the authored regex for decisive cases and can miss a subtle attack the regex labels decisively but incorrectly. |

Switching to `ambiguous` is a cost/latency optimisation, not a security upgrade. Measure guardrail
false positives/negatives on the 18-case phase-14 guardrail golden set plus a broader audit before
changing the default.

## 7. Policy RAG cache

`lookup_store_policy` caches normalized `(query, limit)` results in-process for **10 minutes**, bounded
to **256 entries**. A hit skips both embedding and pgvector. `clear_policy_cache()` is called after a
knowledge re-seed and is available to tests. The cache is per API process, so multi-instance
deployments can briefly serve different knowledge versions after a seed; restart or fan out the
clear operation when consistency matters.

## 8. What is deliberately not claimed

- No latency-SLO attainment percentage until search/sizing/TTFT timings are persisted.
- No real revenue, ROI or provider invoice. Orders and checkout are simulations.
- No human-agent deflection or support-cost saving; there is no human queue.
- No claim that fallback-provider pricing equals primary-provider pricing.
- No claim that a low cost per assisted order is incremental profit; cohort assignment is
  observational and self-selected.
