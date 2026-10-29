# Business case — Toko Marcell

**Audience:** hiring manager or product owner. **Question this document answers:** why does the
engineering exist, which shopper and store-owner problems does it target, how would success be
measured, and what can this repository *not* prove?

> **Evidence rule.** Every number here is traceable to a committed artifact in
> [`pipelines/results/`](../pipelines/results/) or [`pipelines/BENCHMARKS.md`](../pipelines/BENCHMARKS.md).
> Offline retrieval/evaluation numbers are labelled **offline**. Online funnel numbers are either
> exposed by `GET /events/summary` or marked **planned (phase N)**. The shop uses a **simulated
> checkout** on demo traffic and has **no inventory, margin or revenue data**; this document makes no
> revenue, profit or ROI claim.

Companion documents: [KPI dictionary](./METRICS.md) · [benchmark source ledger](../pipelines/BENCHMARKS.md)
· [engineering README](../README.md).

---

## 1. Personas

Both personas are **composites** built from the product's target market, not real customers.

### Persona A — the Indonesian online-fashion shopper ("Rina")

- 24, mobile-first, shops between classes and commutes. Budget-conscious; compares prices in IDR.
- Searches in **mixed Indonesian and English**: *"jaket anti air"*, *"linen shirt oversized"*,
  *"sepatu lari empuk"*. She does not translate her intent into catalog vocabulary.
- Needs two things before she pays: **will it fit?** and **what happens if I return it?**
- Success: finds a relevant product in one or two searches, gets a size/policy answer she trusts,
  and can build an outfit inside her budget without opening WhatsApp.
- Failure modes: zero results, English-only semantic mismatch, a size guess, an unanswered policy
  question, or a chat assistant that invents an answer. Each one ends the session.

### Persona B — the store owner / operator ("Admin Toko Marcell")

- Runs a small fashion store with a catalog in the thousands, plus Instagram/WhatsApp and a web
  storefront. No data team, no analyst, no time to read SQL.
- Wants four answers: **which searches return nothing**, **where sessions drop**, **does the AI
  assistant pay for itself**, and **what can it not answer**.
- Cares about server-confirmed orders, AOV and cost per assisted order — not nDCG in isolation.
- Failure modes: vanity metrics, revenue claims from synthetic events, an assistant that gives S/M/L
  advice for shoes, and a token bill with no attribution.

---

## 2. Five problems → feature → KPI

The table is the business contract for the build: every feature names the lever it moves and the KPI
that would show it. "Evidence today" is deliberately split into **offline** (committed evaluation)
and **online** (instrumented, planned, or not yet measurable).

| # | Problem | Business lever | Feature that addresses it | KPI it moves | Evidence today |
|---|---|---|---|---|---|
| 1 | Mixed Indonesian/English or casual queries return nothing or the wrong products. BM25 scores Indonesian nDCG@10 at **0.334**. | Conversion | Tri-modal bilingual retrieval: hand-written BM25 + multilingual MiniLM dense vectors + CLIP visual vectors, fused with RRF (`k=60`), optional cross-encoder rerank | **zero-result rate ↓**, **search-to-PDP ↑** (offline leading indicators: nDCG@10, HR@10) | **Offline:** overall nDCG@10 **0.794 → 0.894**, Indonesian nDCG@10 **0.334 → 0.765**, HR@10 **100%** ([`search_eval_multilingual.json`](../pipelines/results/search_eval_multilingual.json)). Search metrics are live in `/events/summary`; no committed live-traffic result is claimed. |
| 2 | Size uncertainty blocks the add-to-cart and creates returns. The store's chart is a rule-of-thumb, not a brand-official chart. | Returns, abandoned PDPs | Deterministic category-aware `recommend_size` (authored chart + documented brand cut rules) plus policy/size-guide RAG | **copilot assisted-add rate ↑**, **unanswered rate ↓** | **Offline retrieval:** RAGAS Context Recall **1.0000**, Context Precision **0.8750** on 12 bilingual policy/size-guide queries ([`rag_eval.json`](../pipelines/results/rag_eval.json)). Sizing is rule-based; there is **no fit-accuracy benchmark**. Assisted-add and copilot-quality metrics are live (phase 9); deflection is phase 12. |
| 3 | Repetitive shipping, returns and payment questions consume the owner's time. | Support cost | `lookup_store_policy`: hypothetical-question embeddings + direct chunk embeddings + IDF-weighted bilingual lexical matching, fused with RRF | **deflection ↑**, **unanswered rate ↓** | Same RAGAS artifact for retrieval quality. Unanswered logging is live (phase 10); deflection is planned (phase 12). There is no human handoff, so "deflection" means resolved inside the copilot versus an honest "can't answer" — not a human-agent queue. |
| 4 | One-item baskets: the shopper buys the product she searched for and leaves. | AOV | Session-aware item-to-item collaborative filtering, plus `build_outfit` with the budget total enforced in code (`total ≤ budget`) | **AOV ↑**, **copilot assisted-add rate ↑** (offline leading indicator: HR@10) | **Offline:** sequential leave-one-out HR@10 **7.96% vs 0.18%** for popularity — **+4,322% relative** on 5,000 sampled active users ([`recs_eval.json`](../pipelines/results/recs_eval.json)). AOV from server-confirmed orders is live in `/events/summary` (phase 9); the budget invariant is tested in phase 14. |
| 5 | Ungrounded AI answers create trust risk; LLM and abuse traffic create cost exposure. | Margin and trust | Deterministic guardrail short-circuit, bounded rate limits, a 7-tool whitelist, no client-supplied prices, and server-validated add-to-cart actions | **cost per conversation ↓**, **cost per assisted order ↓** | Guardrail and tool behaviour are unit-tested in CI, but **no cost figure is claimed today**: tokens are logged now (phase 9) and unit economics land in phase 13. |

**KPI → source map.** The [KPI dictionary](./METRICS.md) owns the formulas; this is the table or
endpoint each feature's KPI is computed from:

| KPI | Source table / endpoint | Status |
|---|---|---|
| zero-result rate, search-to-PDP | `events` → `GET /events/summary` | Live (search-to-PDP is a session proxy) |
| AOV | `orders` → `GET /events/summary.revenue` (server-confirmed totals; `purchase_mock` is not authoritative) | Live (phase 9) |
| copilot assisted-add rate | `events` (`copilot_add_to_cart` / `add_to_cart`) | Live (descriptive share, not lift) |
| deflection | `copilot_turns` → `GET /admin/kpi` | Planned (phase 12) |
| unanswered rate | `copilot_turns.outcome` + logged `unanswered_question` → `GET /events/summary.copilot_quality` (`flag_unanswered`) | Live (phase 10) |
| thumbs-up rate | `copilot_feedback` → `GET /events/summary.copilot_quality` (`POST /copilot/feedback`) | Live (phase 9) |
| cost per conversation, cost per assisted order | `copilot_turns` tokens × configured prices; `orders` join | Planned (phase 13) |
| latency p50 / p95 | `copilot_turns.latency_ms` → `GET /events/summary.copilot_quality` (dashboard phase 12) | Live (phase 9) |
| offline leading indicators (nDCG@10, HR@10, RAGAS) | `pipelines/results/*.json` | Offline only; not business KPIs |

**How to read the evidence.** Offline nDCG/RAGAS/HR numbers are proxies measured on small,
author-labelled or sequential-holdout sets. They justify shipping a feature; they do not prove
revenue. The live funnel endpoint reports its own caveats; AOV is now computed from server-confirmed
orders, but those orders are still simulation rows. See the [KPI dictionary](./METRICS.md) for every
formula and caveat.

---

## 3. Hypotheses

These are the falsifiable claims behind the feature list. None has a live A/B result; the "test"
column states what evidence exists and what would count against the hypothesis.

| ID | Hypothesis | Prediction / falsifier | Test and status |
|---|---|---|---|
| **H1 — Find** | Bilingual hybrid retrieval increases qualified product discovery for Indonesian shoppers. | Indonesian nDCG@10 and HR@10 rise versus BM25; zero-result rate falls; search-to-PDP rises. Falsified if offline Indonesian nDCG does not improve, or if online zero-result rate does not fall after the phase-11 fallback (live; still needs traffic). | **Supported offline:** ID nDCG@10 0.334 → 0.765; overall 0.794 → 0.894. Online validation needs real traffic; no A/B. |
| **H2 — Decide** | Grounded policy and sizing answers turn hesitation into carts. | Unanswered rate falls, assisted-add rate rises, thumbs-up rate rises. Falsified if the assistant answers more but assisted adds do not move, or if thumbs-down rises. | **Partially supported:** retrieval quality is measured (RAGAS 1.0000 recall / 0.8750 precision); assisted-add, thumbs, guardrail and latency telemetry are live (phase 9); honest unanswered outcomes are live (phase 10). Deflection remains phase 12. |
| **H3 — Basket** | Recommendations and outfit building raise basket size without breaking the shopper's budget. | AOV and items per order rise; 100% of outfit totals stay ≤ budget. Falsified if AOV does not move, or if any outfit exceeds budget. | **Supported offline for ranking:** HR@10 7.96% vs 0.18% popularity. AOV is now live from server-confirmed orders (phase 9); the budget invariant is enforced in code and tested in phase 14. |
| **H4 — Trust and cost** | Guardrails and deterministic tools cut unsafe or irrelevant LLM work without hiding real questions. | Cost per conversation stays bounded; off-topic/injection turns are blocked; genuinely unanswerable turns are logged honestly rather than answered with a guess. Falsified if guardrails block legitimate sizing/policy questions at a high rate, or if unanswered turns are silently answered. | **Partially measurable:** guardrail-rate, latency and outcome telemetry are live (phase 9); explicit `unanswered` outcomes and the logged question are live (phase 10); cost remains phase 13. |
| **H5 — Measurement** | Server-confirmed orders plus per-turn copilot telemetry can support defensible AOV, cohort and cost metrics. | The join keys exist (`orders.session_id`, `copilot_turns.session_id`), AOV is computable from rows the client cannot forge, and cohort denominators are non-empty. Falsified if orders lack session attribution or per-turn logs cannot be joined to sessions, making cohort/AOV analysis impossible. | **Supported (phase 9):** `/events/summary` now returns order-based revenue/AOV, copilot vs non-copilot cohorts and turn quality. `purchase_mock` remains client-assertable and is not used for revenue. |

---

## 4. KPI tree

The north star is intentionally **two metrics reported together**, not one blended score: conversion
and AOV can move in opposite directions (discounting raises conversion and lowers AOV), so blending
them would hide the tradeoff. The guardrail is reported beside them.

```
North star (assisted sessions)
├── assisted-session conversion = orders in assisted sessions / assisted sessions
├── assisted-session AOV        = order revenue in assisted sessions / assisted orders
└── Guardrail: cost per assisted order

├── Find (search)
│   ├── zero-result rate                     [live]
│   ├── search-to-PDP                        [live, session proxy]
│   └── offline nDCG@10 / HR@10              [offline leading indicator]
│
├── Decide (copilot / RAG)
│   ├── copilot assisted-add rate            [live, descriptive]
│   ├── unanswered rate                      [live: phase 10]
│   ├── deflection                           [planned: phase 12]
│   └── thumbs-up rate                       [live: phase 9]
│
├── Basket (recommendations / outfit)
│   ├── AOV                                  [live: phase 9]
│   ├── items per order                      [computable now; not exposed]
│   └── offline recommendation HR@10         [offline leading indicator]
│
└── Trust and cost
    ├── guardrail short-circuit rate         [live: phase 9]
    ├── latency p50 / p95                    [live: phase 9; dashboard phase 12]
    ├── cost per conversation                [planned: phase 13]
    └── cost per assisted order              [planned: phase 13]
```

**Assisted session (operational definition).** A session with at least one `search` or copilot event
(`copilot_message`, `copilot_product_click`, `copilot_add_to_cart`) in the window. Recommendation-rail
impressions and clicks are **not instrumented**, so the north star currently covers search and
copilot sessions only. Orders carry `session_id`, which is what makes the phase-9 join possible.

**Why cohort comparison is not a lift claim.** Sessions that choose the copilot are more engaged than
sessions that do not. Comparing them is useful for direction, but it is **correlational**: selection
bias, seasonality and product mix are uncontrolled. There is no live A/B in this project because
traffic is too small to power one. Any future experiment needs a pre-registered primary metric, a
holdout, and confidence intervals; until then the honest output is "assisted sessions converted at X%,
non-assisted at Y%, with selection bias" — never "the copilot lifted conversion by X−Y points".

Metric definitions, formulas, sources and caveats: **[METRICS.md](./METRICS.md)**.

---

## 5. Non-goals

Explicitly out of scope for this repository:

- **Real payments, fulfilment, shipping, returns or refunds.** Checkout is a simulation; no money
  moves and `orders.status = 'paid'` is a demo state.
- **Inventory, COGS, margin or profitability optimisation.** There is no stock, cost or markdown
  data, so no profit-per-order claim is possible.
- **Human handoff / live-agent escalation.** There is nobody behind the chat. The honest behaviour is
  "I can't answer that" plus a logged unanswered turn.
- **User accounts, PII, cross-device identity or personalised profiles.** Sessions are anonymous and
  browser-scoped.
- **A live A/B testing platform or causal attribution.** Cohort comparisons stay descriptive.
- **Brand-official size charts or a per-product fit ML model.** Sizing is authored, deterministic and
  advisory and category-aware, not brand-official.
- **Marketplace / multi-tenant / multi-currency support.** One store, one demo USD→IDR rate.
- **A production SLA on free-tier hosting.** Cold starts are documented, not hidden.

---

## 6. Honest limitations

These are the limitations a reviewer should hold against every claim above:

- **Simulated checkout.** The storefront, QRIS page, couriers and order confirmation are a demo.
  No payment gateway, no fulfilment, no refunds, no chargebacks. Orders are server-confirmed rows,
  but they are still simulation rows.
- **No inventory or margin data.** AOV is a basket-value proxy, not profit. Out-of-stock, sourcing
  cost, discounts, shipping cost and returns cannot be modelled.
- **Small traffic and no live A/B.** Offline sets are small (32 search queries, 12 RAG queries, 5,000
  sampled recommendation users) and mostly author-labelled. Online cohorts are underpowered for
  statistical claims.
- **Brand size rules are demo data, and the chart is a store estimate.** The guide in
  [`api/knowledge/size_charts.md`](../api/knowledge/size_charts.md) is authored, not an official
  brand chart. Since phase 10b `recommend_size` is category-aware: shoes use a standard foot-length
  table, belts use waist, hats use head circumference, luggage is dimensions, and lingerie/kids/
  swimwear/costumes/unknown categories are honestly unsupported instead of defaulting to tops. The
  six brand offsets remain `source=demo`; no fit-accuracy benchmark exists.
- **Cohort comparison is correlational.** Copilot users self-select. There is no holdout, so
  "assisted" metrics are not lift and must not be presented as ROI.
- **`purchase_mock` is client-assertable.** Checkout confirm writes it server-side today, but the
  public `POST /events` endpoint accepts `purchase_mock` from any client and historical rows may be
  browser-asserted. Revenue-grade metrics must use `orders`, not events.
- **Cost figures are planned, not measured.** Tokens are not yet logged and provider prices are
  configurable placeholders. A free-tier model can make marginal cost look like zero while hiding
  quota and capacity constraints.
- **Offline metrics are proxies.** nDCG@10, HR@10 and RAGAS scores measure retrieval on small sets;
  they do not measure revenue, trust or long-term retention.
- **Synthetic demo data.** The committed seed contains demo funnel rows (357 events, 106 orders per
  [`BENCHMARKS.md`](../pipelines/BENCHMARKS.md)). Any dashboard built from it must carry a
  `synthetic` label; no revenue figure may be derived from it.
- **Fixed demo FX and list prices.** `price_idr` uses a documented USD→IDR demo rate (16,000) and
  source list prices, not a pricing strategy. No discounts, tax or shipping are modelled.
- **No human handoff.** Unanswered means the assistant refused honestly; it does not route to a human.
- **Anonymous, session-scoped identity.** Cross-device journeys and repeat-customer value are
  invisible. A session ending is not a customer ending.

---

## 7. What would make this a real business experiment

The build is structured so the next step is measurement, not more features:

1. **Replace simulation with real commerce** — payment gateway, fulfilment and returns, so `orders`
   become revenue and AOV becomes a financial metric.
2. **Add inventory and margin feeds** — so the guardrail can become profit per assisted order rather
   than revenue per assisted order.
3. **Run a pre-registered holdout** — randomise eligible sessions to copilot/search/rec surfaces,
   define one primary metric and guardrails in advance, report confidence intervals and segment by
   new vs returning shopper.
4. **Expand the offline sets** — more bilingual queries, human relevance labels, and a sizing
   evaluation with real fit/return outcomes.
5. **Instrument recommendation surfaces** — impressions and clicks, so the north star actually covers
   the "rec" part of AI/search/rec.
6. **Audit groundedness** — a sampled human review of copilot answers, not just thumbs, because
   thumbs are self-selected and sparse.
7. **Use real provider invoices** — replace configured price-per-1k-tokens with billed cost, including
   guardrail calls, retries and infrastructure.

Until then, this repository is an engineering portfolio with an honest measurement design — not a
revenue case study.
