# Toko Marcell — Business Value & Experiment Case Study

> **SYNTHETIC / SIMULATION CASE STUDY — All business outcomes are demonstrated via deterministic
> seeded simulation; no real revenue or customer data is claimed.**

**Audience:** hiring manager, product owner, or engineer reviewing the business value behind the
build. **Question this document answers:** why does ungrounded e-commerce AI fail, what did Toko
Marcell build instead, how were the four shopper jobs modelled, what did the seeded simulation show,
and what would it take to turn this into a real experiment?

**Run behind the numbers:** `scripts/simulate_traffic.py`, seed **42**, **300** sessions, the
4,051-product Men/Women catalog in local Postgres. The same seed and catalog reproduce the counts,
revenue and cohort rates below. The simulator is the artifact; no generated revenue is real.

**Companion documents:** [business case](./BUSINESS_CASE.md) · [KPI dictionary](./METRICS.md) ·
[SLO policy](./SLOS.md) · [benchmark ledger](../pipelines/BENCHMARKS.md) · [root README](../README.md).

---

## 1. Executive summary

Most e-commerce copilot demos fail in the same four places:

1. **They are ungrounded.** The model answers from parametric memory, so shipping, returns and size
   claims can be plausible but wrong.
2. **They guess when the evidence is missing.** A shoe question becomes S/M/L, or a return question
   becomes a confident policy that does not exist.
3. **They are expensive without attribution.** Every turn burns tokens, but nobody can say which
   conversations, carts or orders the spend produced.
4. **They confuse correlation with lift.** Sessions that choose the assistant are more engaged, so a
   raw cohort difference is presented as causal ROI.

Toko Marcell takes the opposite architecture. The LLM is an orchestrator, not the source of truth:
deterministic sizing rules, a budget-enforcing outfit builder, and RAGAS-evaluated store-policy
retrieval do the domain work. A deterministic guardrail and a seven-tool whitelist bound what the
model can do; genuinely unanswerable questions are logged through `flag_unanswered` instead of being
invented. Every turn is attributed to a session, a tool trace, a latency and a configured token-cost
estimate.

This case study turns that engineering into a business narrative with a **seeded simulator**, not
real customers. The simulation models four journeys — search-only baseline, sizing assist, policy
assist and outfit assist — over the real seeded catalog. Its purpose is to show how the product
*would* be measured, which operational levers each job moves, and where the honest limits sit.

**One-line result, explicitly simulated:** in the seed-42 run, copilot-assisted sessions converted at
**30.3% vs 7.4%** for the search-only baseline, with **AOV Rp764,190 vs Rp378,270** and estimated
token cost of **$0.001593 per assisted order**. These are **simulation outputs, not causal lift and
not revenue**.

---

## 2. Why ungrounded AI fails — and what replaces it

| Failure mode | Ungrounded default | Toko Marcell response | Evidence |
|---|---|---|---|
| **Size advice** | The model guesses S/M/L from the title. Shoes, lingerie and luggage get the same answer. | `recommend_size` dispatches by `size_kind`: tops/bottoms/dress use authored TB/BB charts; footwear, waist/belt and headwear use cited standards; unsupported categories return `applicable=false` + `required_inputs`. Missing measurements are requested, never invented. | Every seed `(department, category)` pair is mapped in [`size_kind_map.json`](../api/knowledge/size_kind_map.json); the phase-14 benchmark asserts 18 exact sizing contracts, including unsupported lingerie/luggage/kids. |
| **Policy answers** | The model writes a confident shipping or return policy. | `lookup_store_policy` retrieves authored bilingual chunks with hypothetical-question embeddings + direct chunks + IDF lexical matching, fused with RRF. | RAGAS Context Recall **1.0000**, Context Precision **0.8750** on 12 ID+EN queries ([`rag_eval.json`](../pipelines/results/rag_eval.json)). |
| **Outfit budget** | The model recommends a nice combination that costs 1.4× the budget. | `build_outfit` selects catalog products and enforces `total_idr <= budget_idr` in code. | Phase-14 benchmark: **8/8** outfit scenarios, **0** budget violations. |
| **Unsafe or off-topic turns** | The model answers politics, code or a jailbreak because it can. | A deterministic guardrail short-circuits greetings/off-topic/injection; an optional LLM classifier handles ambiguity; seven tools are the only action surface. | Phase-14 golden set: **18/18** guardrail cases, including 4 valid shopping queries that must pass through. |
| **Cost without attribution** | A monthly provider bill and no per-conversation answer. | `copilot_turns` records thread, session, tools, outcome, latency, tokens, model and `cost_usd`; `unit_economics` exposes cost per turn/conversation/resolved conversation/assisted order. | Phase 13 estimate from configured per-million prices, not a provider invoice. |
| **Fake certainty** | “I couldn’t find that” never gets said. | `flag_unanswered` stores the shopper question (300-char cap) and logs `outcome='unanswered'`; there is no human handoff, so the honest fallback is visible. | Phase-10 telemetry; final-turn deflection in `/admin/kpi`. |

This split is the core product argument: **use deterministic engines where correctness is checkable,
use the LLM for language and orchestration, and make every claim traceable to a tool result.**

---

## 3. What was built

| Layer | Implementation | Business role |
|---|---|---|
| **Find** | BM25 + multilingual MiniLM + CLIP → RRF; deterministic ID→EN retry on zero results; `/search/insights` | Reduce zero-result dead ends and expose missed demand |
| **Decide** | Category-aware deterministic sizing; bilingual policy RAG; `flag_unanswered` | Remove fit/policy hesitation without guessing |
| **Basket** | Session-aware item-to-item CF; `build_outfit` with a hard budget invariant | Raise AOV with multi-item, budget-safe baskets |
| **Trust** | Guardrail, tool whitelist, server-validated cart, rate limits, no client prices | Bound unsafe output and spend |
| **Measure** | `events`, `orders`, `copilot_turns`, `copilot_feedback`; `/events/summary`, `/admin/kpi`, phase-14 benchmark | Attribute conversion, AOV, deflection, latency and cost |

The simulation covers the last three shopper journeys plus the search-only baseline. It is not a
load test, a payment test, or a model-quality evaluation.

---

## 4. Hypotheses and simulation design

### 4.1 Hypotheses

| ID | Hypothesis | How this case study treats it | Simulated / offline result | Status |
|---|---|---|---|---|
| **H1 — Bilingual retrieval** | Mixed Indonesian/English retrieval increases qualified discovery versus BM25. | **Not varied by the simulator.** The baseline path emits ID+EN search events, but it does not re-run BM25 vs hybrid. H1 is measured by the committed offline IR harness. | Offline nDCG@10 **0.794 → 0.894** overall; Indonesian **0.334 → 0.765**; HR@10 **100%** ([`search_eval_multilingual.json`](../pipelines/results/search_eval_multilingual.json)). | **Supported offline; not tested online.** |
| **H2 — Category-aware sizing** | Grounded size help turns PDP hesitation into carts. | Sizing-assist cohort: PDP → sizing chat → cart/checkout. Compared with search-only baseline. | Add-to-cart **64.0% vs 32.6%**; conversion **36.0% vs 7.4%**. | **Simulation-supported. Not causal.** |
| **H3 — Policy deflection** | Shipping/returns/QRIS answers resolve questions inside the copilot, without escalation. | Policy cohort: PDP → policy chat → answer, with a small optional purchase path. | **45/45** policy conversations resolved by `lookup_store_policy` (**100%** simulated deflection); policy conversion only **2.2%**, because the job is support, not basket-building. | **Simulation-supported for resolution, not cost savings.** |
| **H4 — Budget outfits & AOV** | A budget-enforcing outfit builder raises basket size without breaking the cap. | Outfit cohort: outfit query → multi-item cart → checkout, with budget chosen from Rp750k–Rp2M. | **22/22** outfit orders multi-item; **0** budget violations; outfit AOV **Rp952,073 vs Rp378,270** baseline. | **Simulation-supported; no style-relevance claim.** |

### 4.2 Cohort design

The simulator assigns exact cohort counts, then shuffles them across sequential session IDs:

| Cohort | Share | Simulated sessions | Journey |
|---|---:|---:|---|
| Baseline / search-only | 45% | 135 | `search → view_product → add_to_cart? → checkout_start? → order?` |
| Copilot: sizing | 25% | 75 | `view_product → copilot_message/recommend_size → add_to_cart? → checkout? → order?` |
| Copilot: policy | 15% | 45 | `view_product → copilot_message/lookup_store_policy → optional cart` |
| Copilot: outfit | 15% | 45 | `copilot_message/build_outfit → product clicks → multi-item cart → checkout? → order?` |
| **Total** | **100%** | **300** | |

The probabilities are **authored assumptions**, not fitted to real traffic. They make the simulated
mechanism inspectable; they do not estimate a production conversion rate.

### 4.3 Deterministic mechanics

- **Seeded RNG:** `random.Random(42)` drives cohort shuffle, journey choices, product selection,
  timestamps, latency, tokens and feedback.
- **Catalog:** 4,051 Men/Women products read from Postgres, sorted by ASIN; an embedded 14-product
  fallback keeps `--dry-run` usable offline.
- **Outfit budgets:** Rp750k, Rp1.0M, Rp1.25M, Rp1.5M or Rp2.0M. Items are chosen from department-
  specific top/bottom/shoe slots, and the final total is checked against the cap.
- **Orders:** written as real `orders` rows with `sim_ord_*` tokens, `order_items`, `status='paid'`,
  `session_id` and explicit timestamps. A `purchase_mock` event is emitted for funnel consistency.
- **Copilot telemetry:** one `sim_thr_*` thread per assisted session; turns carry outcome, tools,
  latency, input/output tokens, `cost_usd` and a `simulated-model` label.
- **Feedback:** a deterministic subset of threads gets a thumbs up/down row.
- **Reset:** `--reset-sim-only` deletes only `sim_sess_*` / `sim_ord_*` / `sim_thr_*` rows before
  inserting. Re-running without it fails rather than doubling the dataset.
- **Validation:** every event/order/turn references a known sim session; every order total equals its
  line items; every outfit order has ≥2 items and stays within its budget; every feedback thread
  exists. Violations abort the write.

### 4.4 What the simulation deliberately does not do

- It does **not** call the LLM, search engine, sizing engine or RAG pipeline. It writes telemetry that
  represents those journeys.
- It does **not** model inventory, stock-outs, cancellations, refunds, margin, shipping cost or tax.
- It does **not** run an A/B test. Cohorts are fixed by authored probabilities, not randomized live
  assignment.
- It does **not** prove that the copilot causes conversion. It demonstrates the measurement design
  and the direction the product is built to move.
- It writes directly to Postgres, bypassing the API's rate limits and checkout validation; that is
  appropriate for analytics seeding, not for a security or load test.

---

## 5. Measured outcomes (simulated)

**Run:** seed 42 · 300 sessions · 4,051 products · local Postgres · simulated timestamps within the
last 14 days. The word “measured” below means **measured inside the simulation**, not observed from
customers.

### 5.1 Baseline vs copilot-assisted

| Metric | Baseline / search-only | Copilot-assisted | Difference |
|---|---:|---:|---:|
| Sessions | 135 | 165 | +30 sessions |
| Orders | 10 | 50 | +40 orders |
| **Conversion rate** | **7.4%** | **30.3%** | **+22.9 pp (4.1×)** |
| Add-to-cart session rate | 32.6% | 51.5% | +18.9 pp |
| **AOV (IDR)** | **378,270** | **764,190** | **+385,920 (+102.0%)** |
| **Revenue per session (IDR)** | **28,020** | **231,573** | **+203,553 (+726.5%)** |
| **Cost per assisted order (USD, est.)** | n/a | **$0.001593** | simulated token estimate |

The conversion and AOV differences are **simulation outputs under authored probabilities**, not
causal lift. The only defensible reading is: *if the modelled mechanisms behave as specified, this is
the direction and magnitude the instrumentation is designed to detect.*

### 5.2 Cohort detail

| Cohort | Sessions | Orders | Conversion | Add-to-cart | Revenue (IDR) | AOV (IDR) |
|---|---:|---:|---:|---:|---:|---:|
| Baseline / search-only | 135 | 10 | 7.4% | 32.6% | 3,782,700 | 378,270 |
| Copilot: sizing | 75 | 27 | 36.0% | 64.0% | 16,144,700 | 597,952 |
| Copilot: policy | 45 | 1 | 2.2% | 8.9% | 1,119,200 | 1,119,200 |
| Copilot: outfit | 45 | 22 | 48.9% | 73.3% | 20,945,600 | 952,073 |
| **Copilot-assisted total** | **165** | **50** | **30.3%** | **51.5%** | **38,209,500** | **764,190** |
| **All sessions** | **300** | **60** | **20.0%** | — | **41,992,200** | **699,870** |

**What each cohort demonstrates**

- **Sizing:** the add-to-cart rate rises from **32.6% to 64.0%**, matching the hypothesis that
  category-aware advice removes a pre-cart blocker. It does not prove the advice is *correct*; the
  phase-10b/14 contracts prove chart conformance, not fit.
- **Policy:** all **45/45** policy conversations are resolved by the policy tool, but only one policy
  shopper buys. Support value is **deflection**, not conversion. There is no human handoff, so this
  is self-service resolution, not a human-agent queue metric.
- **Outfit:** every one of the **22** outfit orders contains **3 items** (the simulator's outfit path
  is multi-item by construction), with **0** budget violations. Outfit AOV is **Rp952,073**, 2.5× the
  baseline. Style relevance is not measured.
- **Baseline:** 10 of 135 search-only sessions convert (7.4%), with one-item orders. This is the
  comparator, not a control group.

### 5.3 Copilot quality and trust signals

| Signal | Simulated value | What it means |
|---|---:|---|
| Copilot turns / threads | 198 / 165 | Some outfit conversations take a second cart-action turn |
| Resolved conversations | 165 / 165 | Every assisted thread contains an `answered` or `cart` turn |
| Thumbs up / down | 66 / 13 (**83.5%** up) | Sparse, self-selected simulated feedback — not an answer-quality audit |
| Simulated latency p50 / p95 | 1,278 ms / 2,480 ms | Generated telemetry, not an SLO-attainment claim |
| Outfit budget violations | 0 | Hard invariant enforced by the simulator and phase-14 benchmark |
| Policy deflection | 100% (45/45) | All modelled policy questions resolved in-copilot |

---

## 6. Unit economics and reliability

### 6.1 Token cost vs simulated incremental cart value

| Unit-economics metric | Simulated value |
|---|---:|
| Copilot turns | 198 |
| Input / output tokens | 371,080 / 39,988 |
| Total estimated token cost | **$0.079655** |
| Cost per turn | **$0.000402** |
| Cost per conversation | **$0.000483** |
| Cost per resolved conversation | **$0.000483** |
| **Cost per assisted order** | **$0.001593** |
| Assisted orders | 50 |
| Simulated assisted revenue | **Rp38,209,500** |
| Simulated AOV delta vs baseline | **+Rp385,920** |
| Simulated revenue/session delta | **+Rp203,553** |

The cost estimate uses the configured planning prices (`$0.15` input / `$0.60` output per million
tokens), not a provider invoice. **No FX conversion is applied** between the USD token estimate and
the IDR basket values, and no ROI is claimed. A cheap token bill does not prove a profitable product;
it only shows the spend is small relative to the modelled basket under these assumptions.

### 6.2 Guardrail short-circuiting

The phase-14 benchmark runs the deterministic regex guardrail on 18 labelled cases and gets
**18/18** correct, including four valid shopping queries that must pass through. In production,
`GUARDRAIL_LLM_MODE=ambiguous` runs that regex layer first and skips the LLM classifier on decisive
greeting/off-topic/injection labels; the default `always` keeps contextual classification for every
non-injection message.

**What that saves:** one classifier call on decisive labels. **What this simulation claims:** nothing
about that saving — the simulator does not model guardrail tokens, retries or provider prices beyond
the configured per-turn estimate. Guardrail cost remains outside the agent token count, so the
per-turn cost is a lower bound when the guardrail runs.

### 6.3 Policy RAG cache

`lookup_store_policy` caches normalized `(query, limit)` results for **10 minutes**, bounded at **256
entries**, and clears after a knowledge re-seed. Repeated shipping/returns/QRIS questions therefore
skip embedding and pgvector retrieval.

**What that saves:** embedding and retrieval work on a cache hit. **What this simulation claims:**
nothing about cache-hit rate or latency reduction — each simulated policy turn gets one query and the
simulator does not credit cache hits. The conservative reading is that real repeat-question traffic
should be cheaper and faster than the modelled policy path, but that must be measured in production.

### 6.4 Reliability evidence around the simulation

- `scripts/simulate_traffic.py` validates referential integrity, order arithmetic, multi-item outfit
  orders and the budget cap before writing; violations abort the transaction.
- `pipelines/eval_copilot.py` gates guardrail accuracy **≥95%**, sizing accuracy **100%**, outfit
  budget violations **0**, and policy recall@3 **≥90%** in CI (current: 18/18, 18/18, 0, 10/10).
- The simulator's dry-run, invariants and unit tests run in CI; its Postgres write path does not. It is
  a deterministic analysis tool, not a production load test.
- API availability, p95 latency and SLO attainment are **not** established by this simulation. See
  [`SLOS.md`](./SLOS.md) for the policy and its measured-vs-policy labels.

---

## 7. Limitations and production roadmap

### 7.1 Limitations a reviewer should hold against every number

- **Observational selection bias, not a true A/B.** The cohorts are simulated and fixed by authored
  probabilities. In a real store, copilot users self-select; the only honest comparison would be a
  pre-registered randomized holdout with confidence intervals.
- **No real WhatsApp escalation.** There is no human handoff in the product. “Deflection” means the
  copilot resolved the question versus logged an honest inability to answer. It does not mean a
  support agent was avoided.
- **No server-side inventory reservation.** Orders do not decrement stock, cannot fail on
  out-of-stock, and do not model cancellations or returns.
- **No webhook order verification.** Checkout is simulated; the simulator writes paid orders
  directly. There is no payment gateway, signature verification, idempotency key or fulfilment
  webhook.
- **No margin, COGS, shipping cost, tax or refunds.** AOV is basket value, not profit.
- **Authored probabilities.** The conversion probabilities, basket choices and token counts are
  assumptions chosen to make the mechanism inspectable, not estimates fitted to traffic.
- **Token cost is an estimate.** Configured per-million prices, missing usage stays `NULL`, and
  guardrail calls are outside the recorded agent usage.
- **No model-quality or style-relevance audit.** The simulator writes tool names; it does not call
  the LLM. The phase-14 suite checks contracts, not prose quality or outfit taste.
- **Small, synthetic sample.** 300 sessions is enough to demonstrate plumbing, not to estimate a
  production effect size.

### 7.2 Production roadmap

1. **Run a pre-registered holdout.** Randomize eligible sessions to copilot vs no-copilot, define one
   primary metric (assisted conversion or revenue/session) and guardrails in advance, and report
   confidence intervals. Never call an observational difference “lift.”
2. **Connect real commerce.** Payment gateway, webhook-verified orders, idempotency, fulfilment and
   returns. Replace simulated `orders.status='paid'` with provider-confirmed states.
3. **Reserve inventory server-side.** Atomic stock decrement at cart/checkout, explicit sold-out
   handling, and a cancellation/refund model.
4. **Add real WhatsApp escalation.** Keep the honest in-copilot fallback, but route an explicitly
   consented “talk to a human” path to a queue with SLA and handoff context.
5. **Calibrate the simulator.** Replace authored probabilities with observed funnel rates by cohort,
   product category and traffic source; keep the seed and validation invariants for reproducibility.
6. **Instrument the missing surfaces.** Recommendation impressions/clicks, search mode, image-search
   latency, sizing latency, TTFT and cache-hit rate, so SLO attainment is computed from data rather
   than policy.
7. **Bill-grade cost accounting.** Ingest provider invoices and include guardrail calls, retries,
   embeddings, cache misses and infrastructure; keep the configured estimate as a planning input.
8. **Audit groundedness and fit.** Sample copilot answers for human correctness, and join sizing
   advice to real return/exchange outcomes before claiming fit improvement.

---

## 8. Reproduce

```bash
# 1. Dry-run with the embedded fallback (works without Postgres):
python scripts/simulate_traffic.py --dry-run --sessions 100 --seed 42

# 2. Generate/refresh 300 synthetic sessions in a seeded local database:
DATABASE_URL=postgresql://toko:toko@localhost:5432/toko \
  python scripts/simulate_traffic.py --reset-sim-only --sessions 300 --seed 42

# 3. Inspect the instrumentation that now sees the simulation:
curl -s 'http://127.0.0.1:8001/events/summary?since_days=30' | python -m json.tool
curl -s -H 'X-Admin-Token: ci-test-admin-token' \
  'http://127.0.0.1:8001/admin/kpi?since_days=30' | python -m json.tool
```

`--reset-sim-only` removes only `sim_sess_*`, `sim_ord_*` and `sim_thr_*` rows. Running without it
fails when prior simulation rows exist, so metrics are never silently double-counted. It does **not**
remove the committed demo seed rows; `/events/summary` and `/admin/kpi` will include both unless you
start from a fresh seed. The case-study table above uses the simulator's isolated summary, not the
blended dashboard. Every summary carries the synthetic label; see [`METRICS.md`](./METRICS.md) for the
simulator's status and caveats.

---

## 9. Bottom line

The defensible claim is not “the copilot produced Rp42M.” It is:

> **Toko Marcell has a working, instrumented business experiment design.** It pairs deterministic
> domain engines with an LLM orchestrator, records the unit economics of every turn, enforces the
> outfit budget in code, and labels simulation as simulation. Under a deterministic seed-42 scenario,
> sizing and outfit assistance move cart and AOV metrics in the intended direction, while policy
> assistance resolves questions without pretending every conversation should end in a purchase.

Turning that into a business result requires real payments, inventory, a holdout and provider-grade
cost data. The repository is built so those are the next measurements — not more untested AI claims.
