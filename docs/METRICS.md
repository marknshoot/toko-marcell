# KPI dictionary — Toko Marcell

Every business metric this repository exposes **or plans to expose**, with its definition, formula,
source and caveats. Companion to the [business case](./BUSINESS_CASE.md); engineering details live in
the [README](../README.md) and the benchmark provenance ledger ([`BENCHMARKS.md`](../pipelines/BENCHMARKS.md)).

> **Source-of-truth rule.** Money metrics come from server-confirmed `orders` / `order_items`, never
> from browser-posted events. Checkout confirm writes `purchase_mock` server-side today, but the public
> `POST /events` endpoint also accepts `purchase_mock` from any client, and historical rows may be
> browser-asserted. Treat `purchase_mock` as a directional funnel signal, not revenue.

## Status legend

| Status | Meaning |
|---|---|
| **Live** | Computed today by a committed endpoint or query. Values can be quoted with the caveats below. |
| **Live (proxy)** | Computed today, but the metric is a proxy for the business quantity, not the quantity itself. |
| **Planned (phase N)** | Not exposed yet. The formula and source are the design contract; do not quote a value until the phase ships. |

## Conventions

- **Window.** `/events/summary` uses `since_days` (default 30, allowed 1–365) against `created_at`.
  All metrics below are window-bound unless stated otherwise.
- **Rates are `null`, never `0.0`, when the denominator is empty.** Do not read a missing denominator
  as zero conversion.
- **Rates are rounded to 4 decimals** by the API. Raw SQL should report numerator and denominator.
- **Session.** An anonymous `session_id` generated in the browser (`sessionStorage` +
  `crypto.randomUUID()`). No accounts, no PII, no cross-device identity. A new tab is a new session.
- **Event vs session.** State clearly which one a metric counts. Event-level metrics are weighted by
  activity; session-level metrics are weighted by visits.
- **No bot filtering.** The API rate-limits by IP, but events are not otherwise authenticated. Any
  public-traffic number is directional and spoofable.

---

## Metric index

| Metric | Status | Primary source |
|---|---|---|
| [Conversion rates](#conversion-rates) | Live | `events` → `GET /events/summary` |
| [AOV](#aov) | Live (phase 9) | `orders` → `GET /events/summary.revenue` |
| [Zero-result rate](#zero-result-rate) | Live | `events` (`search`) → `GET /events/summary` |
| [Search-to-PDP](#search-to-pdp) | Live (proxy) | `events` → `GET /events/summary`; by mode planned (phase 11) |
| [Copilot assisted-add rate](#copilot-assisted-add-rate) | Live (proxy) | `events` → `GET /events/summary` |
| [Deflection](#deflection) | Planned (phase 12) | `copilot_turns` (outcomes live in phase 9), `/admin/kpi` (phase 12) |
| [Unanswered rate](#unanswered-rate) | Live field (phase 9); meaningful after phase 10 | `copilot_turns.outcome` → `GET /events/summary.copilot_quality` |
| [Thumbs-up rate](#thumbs-up-rate) | Live (phase 9) | `copilot_feedback` → `GET /events/summary.copilot_quality` |
| [Cost per conversation](#cost-per-conversation) | Planned (phase 13) | `copilot_turns` tokens × configured prices |
| [Cost per assisted order](#cost-per-assisted-order) | Planned (phase 13) | `copilot_turns` + `orders` |
| [Latency p50 and p95](#latency-p50-and-p95) | Live (phase 9); dashboard surface phase 12 | `copilot_turns.latency_ms` → `GET /events/summary.copilot_quality` |

---

## Conversion rates

**Status:** Live. Returned by `GET /events/summary` in the `rates` object.

**Definition:** Session-level shares of distinct sessions that reached a step. These are not
user-level or order-level conversion rates, and they are not a strict first-touch funnel.

| API field | Formula |
|---|---|
| `view_to_cart` | `COUNT(DISTINCT session_id WHERE event_type='add_to_cart') / COUNT(DISTINCT session_id WHERE event_type='view_product')` |
| `cart_to_checkout` | `COUNT(DISTINCT session_id WHERE event_type='checkout_start') / COUNT(DISTINCT session_id WHERE event_type='add_to_cart')` |
| `checkout_to_purchase` | `COUNT(DISTINCT session_id WHERE event_type='purchase_mock') / COUNT(DISTINCT session_id WHERE event_type='checkout_start')` |
| `view_to_purchase` | `COUNT(DISTINCT session_id WHERE event_type='purchase_mock') / COUNT(DISTINCT session_id WHERE event_type='view_product')` |

**Source:** `events` table; implementation in
[`api/routers/events.py`](../api/routers/events.py) (`sessions_by_step`, `rate()`); event schema in
[`api/schemas.py`](../api/schemas.py); client emitters in
[`shop/src/lib/api.js`](../shop/src/lib/api.js) and
[`shop/src/components/Catalog.js`](../shop/src/components/Catalog.js).

**Caveats:**

- **Not a sequence.** Each rate uses distinct sessions per step, not ordered paths. A session that
  viewed a product after searching, or reloaded the PDP, still counts once.
- **Event loss biases downward.** The frontend posts fire-and-forget (`keepalive: true`, errors
  swallowed), so lost events are invisible.
- **`purchase_mock` is client-assertable.** Checkout confirm writes it today, but `POST /events`
  accepts it from any client. Revenue, AOV and cohorts are now exposed from `orders` by
  `GET /events/summary` (phase 9); the event conversion rates remain directional proxies.
- **Session boundary bias.** A cart created in one tab and purchased in another is two sessions;
  a session that starts before the window and purchases inside it is missing its earlier step.
- **No bot or test-traffic filtering.** Rate limits bound abuse but do not remove it.
- **Null denominators.** An empty events table produces `null`, not `0.0`.

---

## AOV

**Status:** Live (phase 9). Returned by `GET /events/summary` in the `revenue` block, alongside
`order_count` and `revenue_idr`.

**Definition:** Average value of server-confirmed orders created in the window, in IDR.

**Formula:**

```sql
SELECT SUM(total_idr) / NULLIF(COUNT(*), 0) AS aov
FROM orders
WHERE created_at > now() - make_interval(days => :since_days)
  AND status = 'paid';
```

**Source:** `orders.total_idr`, `orders.created_at`, `orders.status`; line-level detail in
`order_items`; exposed by [`api/routers/events.py`](../api/routers/events.py) as
`revenue.aov_idr`, `revenue.revenue_idr` and `revenue.order_count`. Checkout computes the total
server-side from `products.price_idr`; the client sends only `{asin, qty}` and cannot set a price
(`ConfirmIn` forbids extra fields).

**Caveats:**

- **Simulated checkout.** No payment gateway, fulfilment, refunds or chargebacks. `status='paid'` is a
  demo state, not settled money.
- **No inventory, COGS, margin, discount, tax or shipping data.** AOV is basket value, not profit.
- **Do not compute AOV from `events.price_idr`.** That field is client-supplied; `purchase_mock` is
  client-assertable. The orders table is the only acceptable source.
- **List prices at a fixed demo FX.** `price_idr` uses the documented 16,000 IDR/USD demo rate, not a
  pricing strategy.
- **Outliers and small n.** One large order can move AOV materially; report count and median beside
  the mean.
- **No cancellation/refund model.** If one is added later, AOV must exclude reversed orders.

---

## Zero-result rate

**Status:** Live. Returned by `GET /events/summary` as `search.zero_result_rate`.

**Definition:** Share of search events whose result count was zero.

**Formula:**

```
zero_result_rate = COUNT(search events WHERE results_count = 0)
                 / COUNT(search events)
```

**Source:** `events` (`event_type='search'`, `results_count`), written by the catalog after a search
response in [`shop/src/components/Catalog.js`](../shop/src/components/Catalog.js); aggregated in
[`api/routers/events.py`](../api/routers/events.py). Phase 11 adds top zero-result queries and a
deterministic ID→EN rewrite fallback via `GET /search/insights`.

**Caveats:**

- **Event-level, not session-level.** A popular query repeated by many sessions weighs more.
- **One event per distinct query.** The catalog logs once per query string per mounted view; page
  changes, facet changes and retries may not emit a new event. Image search does not emit a `search`
  event at all.
- **`results_count` is the API total at event time**, after duplicate-title suppression and any
  department/category facet. Historic rows may predate server-side changes, so compare like with like.
- **Zero results is not always failure.** It can mean a genuine catalog gap, a typo, an unsupported
  SKU, or a stopword-only query; pair the rate with the top-query list (phase 11).
- **Spoofable.** Any client can post a `search` event with an arbitrary `results_count`.

---

## Search-to-PDP

**Status:** Live (proxy). Returned by `GET /events/summary` as `rates.search_to_pdp`. A by-mode
breakdown is planned (phase 11, `GET /search/insights`).

**Definition:** Share of search sessions that also viewed a product in the same session.

**Formula:**

```
search_to_pdp = |sessions(event_type='search') ∩ sessions(event_type='view_product')|
              / |sessions(event_type='search')|
```

**Source:** `events` (distinct `session_id` intersection), implemented in
[`api/routers/events.py`](../api/routers/events.py); served by `GET /events/summary`. Planned
phase-11 variant: search-to-PDP by search mode from `GET /search/insights`.

**Caveats:**

- **Session-level proxy, not click-through.** It does not prove a search result was clicked; the
  product view could have happened before the search, or from a recommendation rail in the same
  session.
- **No query-to-product attribution.** The current event schema does not link a click to a query or a
  rank position. A true search CTR needs a `product_click` event carrying query/rank; it remains a
  roadmap item, not part of the current phase plan.
- **Event loss and duplicate views.** Fire-and-forget events can be lost; repeated views collapse to
  one session.
- **The phase-11 by-mode view is still session-level**, not a per-result CTR.
- **Null when there are no search sessions.**

---

## Copilot assisted-add rate

**Status:** Live (proxy). Returned by `GET /events/summary` as
`copilot.assisted_add_to_cart_rate`.

**Definition:** Share of all add-to-cart events that were attributed to the copilot.

**Formula:**

```
copilot_assisted_add_rate = COUNT(event_type='copilot_add_to_cart')
                          / COUNT(event_type='add_to_cart')
```

**Source:** `events` (`copilot_add_to_cart`, `add_to_cart`) written by
[`shop/src/components/CopilotChat.js`](../shop/src/components/CopilotChat.js) and the cart provider;
aggregated in [`api/routers/events.py`](../api/routers/events.py). Phase 9 adds
`copilot_turns`/`copilot_feedback` and cohort comparisons; this event rate remains descriptive.

**Caveats:**

- **The numerator is a subset of the denominator by construction.** A copilot-driven add fires both
  the generic `add_to_cart` event (from the cart) and `copilot_add_to_cart`. The result is an
  attribution share, not incremental lift.
- **Event counts, not sessions or orders.** One engaged user can add many times.
- **Selection bias.** Shoppers who open the copilot are more engaged than those who do not; there is
  no control group. Never call the rate "lift".
- **No order linkage in the rate itself.** Phase 9 joins `session_id` to `orders` for the
  `GET /events/summary.cohorts` blocks; the `copilot_add_to_cart` rate remains an event-count share.
- **Spoofable.** Any client can post either event type.
- **Null when there are no add-to-cart events.**

---

## Deflection

**Status:** Planned (phase 12). Outcomes are logged in `copilot_turns` now (phase 9); honest
"can't answer" is added by `flag_unanswered` in phase 10. Surfaced by `GET /admin/kpi` in phase 12.

**Definition:** Share of copilot conversations resolved inside the copilot with a grounded answer,
rather than ending in an explicit "I can't answer that" turn.

> This is **not** classic support deflection (avoiding a human-agent contact), because Toko Marcell
> has no human handoff or support queue. It is a self-service resolution rate.

**Formula (conversation-level, final turn in the window):**

```
eligible  = threads whose final turn has outcome IN ('answered', 'unanswered')
deflected = eligible threads whose final outcome = 'answered'
deflection = COUNT(deflected) / COUNT(eligible)
```

**Source:** `copilot_turns` (`thread_id`, `session_id`, `outcome`, `created_at` — phase 9);
`flag_unanswered` sets `outcome='unanswered'` (phase 10); aggregated by `/admin/kpi` (phase 12).
The committed schema uses the column names above.

**Caveats:**

- **No human queue exists.** Do not translate deflection into "support tickets avoided" or "support
  cost saved" without a support-cost baseline.
- **"Answered" means tool-grounded, not correct or satisfying.** Pair it with the thumbs rate and a
  human audit sample (phase 14 golden set).
- **Final-turn heuristic.** A conversation can contain both unanswered and answered turns; only the
  last one decides. Thread rotation creates a new conversation.
- **Guardrail-blocked-only threads are excluded.** Off-topic/injection blocks are a separate signal.
- **Conversation-level vs turn-level.** At conversation level, deflection is the complement of the
  unanswered rate by construction (`deflection = 1 − unanswered` on the same denominator). Do not
  present the two as independent signals.

---

## Unanswered rate

**Status:** Live field (phase 9), meaningful after phase 10. `GET /events/summary` returns
`copilot_quality.unanswered_rate` = unanswered turns / all logged turns. `flag_unanswered` lands in
phase 10, so the value is expected to remain 0 until then.

**Definition:** Share of logged copilot turns whose `outcome` is `unanswered`.

**Formula:**

```
unanswered_rate = COUNT(outcome = 'unanswered') / COUNT(*)
```

**Source:** `copilot_turns.outcome` (phase 9); `flag_unanswered` sets `outcome='unanswered'`
(phase 10); exposed by `GET /events/summary.copilot_quality` (phase 9); dashboard in phase 12.

**Caveats:**

- **Measures self-declared inability, not all errors.** A confidently wrong answer is not counted;
  groundedness needs an audit sample or the phase-14 golden set.
- **The phase-9 denominator includes guardrail blocks and provider errors.** That is a true share of
  all turns, but it conflates off-topic/abuse with knowledge gaps. A future knowledge-gap variant
  should filter to `outcome IN ('answered', 'unanswered')`; state the denominator explicitly.
- **Depends on the model actually calling `flag_unanswered`.** Prompt/tool compliance is part of the
  metric's validity.
- **Turn-level weights chatty conversations.** Report a conversation-level companion (share of
  threads with any unanswered turn) beside it.
- **No human handoff.** An unanswered turn ends the interaction; it does not escalate.

---

## Thumbs-up rate

**Status:** Live (phase 9). `POST /copilot/feedback` writes a `copilot_feedback` row; the aggregate is
in `GET /events/summary.copilot_quality`.

**Definition:** Share of explicit copilot feedback votes that are positive.

**Formula:**

```
thumbs_up_rate = COUNT(rating=1) / COUNT(rating IN (-1, 1))
```

**Source:** `copilot_feedback` table (`thread_id`, optional `session_id`, `rating` ∈ {-1, 1});
`POST /copilot/feedback`; thumbs UI in `shop/src/components/CopilotChat.js`; exposed in
`copilot_quality.thumbs_up`, `.thumbs_down`, `.thumbs_up_rate`.

**Caveats:**

- **Low response rate and self-selection.** A small, motivated minority votes; the rate is not
  representative of all conversations.
- **No neutral option.** A thumbs-down can mean a wrong answer, a correct-but-unhelpful answer, tone,
  latency, or a UI mistake.
- **Per-turn vs per-conversation.** The phase-9 endpoint stores a raw vote row per click; it is
  conversation-level feedback in practice. The UI prevents re-clicking the same rating but does not
  deduplicate votes across reloads, so repeated votes inflate the raw share.
- **No turn_id.** Feedback is linked to `thread_id`, not to one specific assistant message; a
  multi-turn conversation's vote cannot be attributed to a single answer.
- **Thread ids are not validated against turns.** The endpoint accepts any well-formed `thread_id`
  (rate-limited, but not authenticated), so the raw vote count is spoofable.
- **Can be gamed or accidentally clicked**, and a thumbs-up does not prove factual correctness.
- **Not comparable across releases** without controlling prompt, model and UI changes.

---

## Cost per conversation

**Status:** Planned (phase 13). Token counts are logged per turn in phase 9; configurable
price-per-1k-token and the cost calculation land in phase 13.

**Definition:** Estimated mean LLM spend for one copilot conversation in the window, in USD.

**Formula:**

```
turn_cost = input_tokens / 1000 * price_per_1k_input
          + output_tokens / 1000 * price_per_1k_output

cost_per_conversation = SUM(turn_cost) / COUNT(DISTINCT thread_id)
```

**Source:** `copilot_turns` (`thread_id`, `input_tokens`, `output_tokens`, `model`, `created_at` —
phase 9); configured input/output prices (phase 13); `GET /admin/kpi` (phase 12/13). `tokens_in` /
`tokens_out` were the old draft names; the committed columns are `input_tokens` / `output_tokens`.
`cost_usd` is reserved and stays `NULL` until phase 13 writes the price table.

**Caveats:**

- **Estimate, not invoice.** Prices are configured per 1k tokens; provider billing, discounts,
  rounding and currency conversion can differ. A free-tier model can make the estimate $0 while
  quota and capacity limits still bind.
- **Include every LLM call attributable to the turn** (guardrail, agent, retries) when tokens are
  logged; excluding them understates cost.
- **Conversation boundary is `thread_id`.** A client-generated thread reset creates a new
  conversation and hides the prior spend.
- **No infrastructure, embedding, vector-search, hosting or human cost.** This is model spend only.
- **Outliers and small n.** Report the count and median; a single long conversation can dominate the
  mean.
- **Price changes over time.** Recompute historical estimates with the price table in force at the
  time, and record the version used.

---

## Cost per assisted order

**Status:** Planned (phase 13). This is the north-star **guardrail** metric, not a revenue metric.

**Definition:** Estimated copilot model spend attributable to sessions that produced a
server-confirmed order, divided by the number of those assisted orders.

**Formula:**

```
assisted_orders = orders whose session_id has >= 1 copilot_turn
copilot_cost    = SUM(turn_cost for turns in those sessions)
cost_per_assisted_order = copilot_cost / COUNT(assisted_orders)
```

If a session contains multiple orders, either split the session cost across them or state that the
full cost is allocated to each; do not silently double count.

**Source:** `copilot_turns.session_id` (phase 9) joined to `orders.session_id`; per-turn cost from
phase 13; `GET /admin/kpi` (phase 12/13). Orders remain the only revenue-grade source.

**Caveats:**

- **Correlational attribution.** The order may have happened without the copilot. There is no
  holdout, so this is cost *associated with* assisted orders, not incremental cost or ROI.
- **Includes abandoned conversations** in converting sessions; excludes copilot conversations that
  influenced a purchase in a later session or on another device.
- **Simulated orders.** No real margin, refunds or fulfilment; do not present this as a real
  acquisition cost.
- **Model spend only.** Excludes infrastructure, embeddings, search, hosting, payment fees and
  marketing.
- **Extremely unstable at small order counts.** Report numerator (spend), denominator (orders) and a
  confidence interval; never a bare ratio.
- **Report beside conversion and AOV, never alone.** A low cost per assisted order can simply mean
  few assisted orders; a high one can be a few expensive conversations.

---

## Latency p50 and p95

**Status:** Live (phase 9). `copilot_turns.latency_ms` is written per turn; `GET /events/summary`
returns `copilot_quality.avg_latency_ms`, `p50_latency_ms` and `p95_latency_ms`. The phase-12
dashboard reuses the same aggregation.

**Definition:** Mean, median and 95th-percentile server-side wall-clock duration of a copilot turn.

**Formula:**

```sql
SELECT AVG(latency_ms) AS avg_latency_ms,
       percentile_cont(0.5)  WITHIN GROUP (ORDER BY latency_ms) AS p50_latency_ms,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_latency_ms
FROM copilot_turns
WHERE created_at > now() - make_interval(days => :since_days);
```

**Source:** `copilot_turns.latency_ms` (phase 9); `GET /events/summary.copilot_quality` (phase 9);
`GET /admin/kpi` (phase 12); SLO targets and alert thresholds are documented in phase 13.

**Caveats:**

- **Server-side only.** Excludes browser/network time, but includes LLM wait, tool queries and
  guardrail calls. Render free-tier cold starts (30–50 s) are a separate platform behaviour and are
  not in this number.
- **Guardrail-only turns are much faster** and pull pooled percentiles down. Report by route and
  guardrail label, not only pooled.
- **p95 is unstable with small n.** Report the sample size; a single slow turn can move it.
- **Streaming time-to-first-token is different** from total turn latency; this metric is the total.
- **Timeouts/errors may be absent** from the table unless the logging path records them.

---

## Supporting telemetry

These are useful for trust and diagnosis, but they are not north-star or revenue metrics.

| Metric | Status | Definition / formula | Source | Caveat |
|---|---|---|---|---|
| Guardrail short-circuit rate | Live (phase 9) | `turns with outcome='guardrail' / all copilot turns` | `copilot_turns.outcome` → `copilot_quality.guardrail_rate` | Counts every deterministic label, including greetings; it is not only unsafe blocks. False positives need the phase-14 golden set. |
| Items per order | Computable now, not exposed | `SUM(orders.item_count) / COUNT(orders)` or `SUM(order_items.qty) / COUNT(DISTINCT order_id)` | `orders.item_count`, `order_items.qty` | Same simulation caveats as AOV; no cancellation model. |
| Offline retrieval quality | Live (offline) | nDCG@10, MRR@10, HR@10, P@10 over a fixed query set | [`search_eval_multilingual.json`](../pipelines/results/search_eval_multilingual.json), [`search_eval.json`](../pipelines/results/search_eval.json) | 32 queries, authored relevance labels; a proxy, not online behaviour. |
| Offline policy/size-guide RAG | Live (offline) | RAGAS Context Precision / Recall at k=3 | [`rag_eval.json`](../pipelines/results/rag_eval.json) | 12 bilingual queries; measures retrieval, not answer correctness or fit accuracy. |
| Offline recommendation ranking | Live (offline) | Sequential leave-one-out HR@10 / HR@5 / MRR@10 / nDCG@10 | [`recs_eval.json`](../pipelines/results/recs_eval.json) | 5,000 sampled active users, seed 42; offline holdout, not online CTR. |

---

## Phase map — when each metric becomes quotable

| Phase | What becomes available |
|---|---|
| 8 | Dictionary and business framing only; no new metric is computed. |
| 9 (shipped) | AOV/revenue from `orders`; `copilot_turns` logging (latency, tools, guardrail, outcome, tokens); `copilot_feedback` + thumbs UI; copilot-user vs non-user cohorts (descriptive); `copilot_quality` aggregate (unanswered/guardrail/latency/thumbs). |
| 10 | `outcome='unanswered'` logging through `flag_unanswered`; copilot refocused on sizing, policy and outfit jobs. |
| 11 | `GET /search/insights`: top zero-result queries and search-to-PDP by mode; ID→EN rewrite fallback. |
| 12 | `GET /admin/kpi`: funnel, cohorts, deflection, unanswered, search health, cost, p50/p95; `synthetic` badge. |
| 13 | Cost per turn, cost per conversation, cost per resolved conversation and cost per assisted order; SLOs and cache. |
| 14 | Golden-set evaluation in CI: sizing exact asserts, outfit budget invariant, guardrail labels, policy RAGAS. |
| 15 | Seeded traffic simulator and case study; every simulated metric must carry a `synthetic` label. |

---

## Do not compute / do not claim

- **Revenue or ROI from `purchase_mock`.** Use `orders`; even then checkout is simulated.
- **AOV from `events.price_idr`.** The client supplies it.
- **Profit, contribution margin or support-cost savings.** No COGS, margin or support baseline exists.
- **Lift or causality from cohort comparison.** There is no holdout; state the selection bias.
- **A cost figure before phase 13.** Tokens and prices are not wired yet.
- **A single blended north-star score.** Conversion and AOV are reported separately.
- **"Deflection" as human-agent deflection.** There is no human handoff.
- **Any number from the simulator as real traffic.** Label it `synthetic`, always.
