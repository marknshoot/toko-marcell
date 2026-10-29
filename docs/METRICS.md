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
- **Dashboard parity.** `GET /admin/kpi` uses the same window and returns the same underlying
  `events`, `orders`, `copilot_turns` and `copilot_feedback` aggregates as `/events/summary` and
  `/search/insights`, reshaped for the operations page. Its `metadata.synthetic=true` label is
  mandatory: these are seeded/simulated demo rows, not real customer revenue.
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
| [Conversion rates](#conversion-rates) | Live | `events` → `GET /events/summary`, `GET /admin/kpi` |
| [AOV](#aov) | Live (phase 9) | `orders` → `GET /events/summary`, `GET /admin/kpi.revenue` |
| [Zero-result rate](#zero-result-rate) | Live | `events` (`search`) → `GET /events/summary`, `GET /search/insights`, `GET /admin/kpi` |
| [Search-to-PDP](#search-to-pdp) | Live (proxy) | `events` → `GET /events/summary`, `GET /search/insights`, `GET /admin/kpi` |
| [Copilot assisted-add rate](#copilot-assisted-add-rate) | Live (proxy) | `events` → `GET /events/summary` |
| [Deflection](#deflection) | Live (phase 12) | `copilot_turns` final turn → `GET /admin/kpi.copilot_quality` |
| [Unanswered rate](#unanswered-rate) | Live (phase 10) | `copilot_turns.outcome` + `unanswered_question` → `GET /events/summary`, `GET /admin/kpi` |
| [Thumbs-up rate](#thumbs-up-rate) | Live (phase 9) | `copilot_feedback` → `GET /events/summary`, `GET /admin/kpi` |
| [Cost per conversation](#cost-per-conversation) | Live (phase 13) | `copilot_turns.cost_usd` → `unit_economics` |
| [Cost per assisted order](#cost-per-assisted-order) | Live (phase 13) | `copilot_turns` + `orders` → `unit_economics` |
| [Latency p50 and p95](#latency-p50-and-p95) | Live (phase 9; dashboard phase 12) | `copilot_turns.latency_ms` → `GET /events/summary`, `GET /admin/kpi` |
| [SLOs](#service-level-objectives) | Live (policy, phase 13) | [`SLOS.md`](./SLOS.md); search/image/sizing/TTFT timing not yet persisted |
| [Copilot business scenario benchmark](#copilot-business-scenario-benchmark) | Live (CI, phase 14) | [`pipelines/results/copilot_eval.json`](../pipelines/results/copilot_eval.json) |

---

## Conversion rates

**Status:** Live. Returned by `GET /events/summary` in the `rates` object and by
`GET /admin/kpi` in the `funnel.rates` object.

**Definition:** Session-level shares of distinct sessions that reached a step. These are not
user-level or order-level conversion rates, and they are not a strict first-touch funnel.

| API field | Formula |
|---|---|
| `view_to_cart` | `COUNT(DISTINCT session_id WHERE event_type='add_to_cart') / COUNT(DISTINCT session_id WHERE event_type='view_product')` |
| `cart_to_checkout` | `COUNT(DISTINCT session_id WHERE event_type='checkout_start') / COUNT(DISTINCT session_id WHERE event_type='add_to_cart')` |
| `checkout_to_purchase` | `COUNT(DISTINCT session_id WHERE event_type='purchase_mock') / COUNT(DISTINCT session_id WHERE event_type='checkout_start')` |
| `view_to_purchase` | `COUNT(DISTINCT session_id WHERE event_type='purchase_mock') / COUNT(DISTINCT session_id WHERE event_type='view_product')` |

The phase-12 dashboard exposes a deliberately different, order-linked funnel:
`/admin/kpi.funnel.order_sessions` counts distinct `session_id` values on **paid `orders`**, and
`checkout_to_order` / `overall_conversion` use that count. The step counts are independent distinct
sessions, not a sequential cohort, so a session can enter mid-funnel and a raw ratio can exceed 1.
Read it as operational telemetry, not a monotonic conversion funnel.

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

**Status:** Live (phase 9). Returned by `GET /events/summary` in the `revenue` block and by
`GET /admin/kpi` as `revenue.aov_idr`, alongside `order_count` / `total_revenue_idr`.

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

**Status:** Live. Returned by `GET /events/summary` as `search.zero_result_rate` and by
`GET /search/insights` as `zero_result_rate`, and by `GET /admin/kpi` as
`search_health.zero_result_rate`.

**Definition:** Share of search events whose result count was zero.

**Formula:**

```
zero_result_rate = COUNT(search events WHERE results_count = 0)
                 / COUNT(search events)
```

**Source:** `events` (`event_type='search'`, `results_count`), written by the catalog after a search
response in [`shop/src/components/Catalog.js`](../shop/src/components/Catalog.js); aggregated in
[`api/routers/events.py`](../api/routers/events.py) and
[`api/routers/search.py`](../api/routers/search.py). `GET /search/insights` also returns the top 20
zero-result queries and the top 20 queries with a `zero_result` flag. `GET /search` now retries an
empty result set once with the deterministic ID→EN query rewriter in
[`api/search.py`](../api/search.py).

**Caveats:**

- **Event-level, not session-level.** A popular query repeated by many sessions weighs more.
- **One event per distinct query.** The catalog logs once per query string per mounted view; page
  changes, facet changes and retries may not emit a new event. Image search does not emit a `search`
  event at all.
- **`results_count` is the API total at event time**, after duplicate-title suppression and any
  department/category facet. Historic rows may predate server-side changes, so compare like with like.
- **Zero results is not always failure.** It can mean a genuine catalog gap, a typo, an unsupported
  SKU, or a stopword-only query; pair the rate with `top_zero_result_queries` / `top_queries` from `GET /search/insights`.
- **Spoofable.** Any client can post a `search` event with an arbitrary `results_count`.
- **`top_queries` groups exact strings.** No case folding or typo clustering; "Sepatu" and "sepatu"
  are different rows. The rewrite fallback helps retrieval but does not rewrite the logged event.

---

## Search-to-PDP

**Status:** Live (proxy). Returned by `GET /events/summary` as `rates.search_to_pdp`, by
`GET /search/insights` as `search_to_pdp_rate`, and by `GET /admin/kpi` as
`search_health.search_to_pdp_rate`.

**Definition:** Share of search sessions that also viewed a product in the same session.

**Formula:**

```
search_to_pdp = |sessions(event_type='search') ∩ sessions(event_type='view_product')|
              / |sessions(event_type='search')|
```

**Source:** `events` (distinct `session_id` intersection), implemented in
[`api/routers/events.py`](../api/routers/events.py) and
[`api/routers/search.py`](../api/routers/search.py); served by both endpoints.

**Caveats:**

- **Session-level proxy, not click-through.** It does not prove a search result was clicked; the
  product view could have happened before the search, or from a recommendation rail in the same
  session.
- **No query-to-product attribution.** The current event schema does not link a click to a query or a
  rank position. A true search CTR needs a `product_click` event carrying query/rank; it remains a
  roadmap item, not part of the current phase plan.
- **Event loss and duplicate views.** Fire-and-forget events can be lost; repeated views collapse to
  one session.
- **The `/search/insights` view is still session-level**, not a per-result CTR; the event schema
  does not carry the search mode, so a by-mode breakdown is not claimed.
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

**Status:** Live (phase 12). Outcomes and the honest fallback are logged since phase 10; the
conversation-level deflection view is exposed by `GET /admin/kpi.copilot_quality` as
`deflection_rate`, with `deflection_eligible_conversations` and
`deflection_answered_conversations` alongside it.

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
`flag_unanswered` sets `outcome='unanswered'` (live in phase 10); aggregated by `/admin/kpi`
(phase 12). The committed schema uses the column names above. The dashboard takes the **last turn per
thread inside the window** (`DISTINCT ON (thread_id) … ORDER BY created_at DESC, id DESC`), then
counts eligible final outcomes.

**Caveats:**

- **No human queue exists.** Do not translate deflection into "support tickets avoided" or "support
  cost saved" without a support-cost baseline.
- **"Answered" means tool-grounded, not correct or satisfying.** Pair it with the thumbs rate and a
  human audit sample. The phase-14 golden set checks deterministic sizing/guardrail/policy contracts, not answer correctness.
- **Final-turn heuristic.** A conversation can contain both unanswered and answered turns; only the
  last one decides. Thread rotation creates a new conversation.
- **Guardrail-blocked-only threads are excluded.** Off-topic/injection blocks are a separate signal.
- **Conversation-level vs turn-level.** Deflection is conversation-level (one final turn per
  thread); the `unanswered_rate` exposed beside it on the dashboard is turn-level over all turns.
  They do not share a denominator, so do not present them as complements or as independent signals.

---

## Unanswered rate

**Status:** Live (phase 10). `GET /events/summary` and `GET /admin/kpi` return
`copilot_quality.unanswered_rate` (the dashboard also returns the raw turn counts).

**Definition:** Share of logged copilot turns where the model called `flag_unanswered` because it
could not answer from tool evidence, and did not complete a cart action.

**Formula:**

```
unanswered_rate = COUNT(outcome = 'unanswered') / COUNT(*)
```

`derive_outcome` precedence is `error → guardrail → cart → unanswered → answered`. A turn that both
calls `add_to_cart` and flags a fallback is counted as `cart`, because the shopper still received a
concrete server-validated outcome.

**Source:** `copilot_turns.outcome`; the tool's `question` argument is stored in
`copilot_turns.unanswered_question` (trimmed to 300 characters) for audit and future query analysis;
`GET /events/summary.copilot_quality`; `GET /admin/kpi.copilot_quality`.

**Caveats:**

- **Measures self-declared inability, not all errors.** A confidently wrong answer is not counted;
  groundedness needs a human audit; the phase-14 golden set evaluates retrieval and scenario contracts, not prose groundedness.
- **The denominator includes guardrail blocks and provider errors.** That is a true share of all
  turns, but it conflates off-topic/abuse with knowledge gaps. A future knowledge-gap variant should
  filter to `outcome IN ('answered', 'unanswered')`; state the denominator explicitly.
- **Depends on the model actually calling `flag_unanswered`.** Prompt/tool compliance is part of the
  metric's validity; the prompt requires the tool before an honest fallback, but the model can still
  answer weakly without it.
- **Raw shopper text is stored.** `unanswered_question` holds the first 300 characters of the user
  question; there is no PII scrub or retention policy yet. Treat it as user content and restrict
  access.
- **Turn-level weights chatty conversations.** Report a conversation-level companion (share of
  threads with any unanswered turn) beside it.
- **No human handoff.** An unanswered turn ends the interaction; it does not escalate.

---

## Thumbs-up rate

**Status:** Live (phase 9). `POST /copilot/feedback` writes a `copilot_feedback` row; the aggregate is
in `GET /events/summary.copilot_quality` and `GET /admin/kpi.copilot_quality`.

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

**Status:** Live (phase 13). `GET /events/summary` and `GET /admin/kpi` return a
`unit_economics` block. `copilot_turns.cost_usd` is populated by
`copilot_metrics.estimate_cost_usd()` whenever the provider reports token usage.

**Definition:** Estimated mean LLM model spend for one copilot conversation (`thread_id`) in the
window, in USD. This is model spend only, not infrastructure or business cost.

**Price model:** USD per one million tokens, configured in `api/config.py`:

```text
LLM_INPUT_COST_PER_MILLION  = $0.15
LLM_OUTPUT_COST_PER_MILLION = $0.60
```

```text
turn_cost = input_tokens  × input_price  / 1,000,000
          + output_tokens × output_price / 1,000,000

total_cost_usd                 = SUM(copilot_turns.cost_usd)
avg_cost_per_turn_usd          = total_cost_usd / COUNT(*) copilot turns
cost_per_conversation_usd      = total_cost_usd / COUNT(DISTINCT thread_id)
cost_per_resolved_conversation = total_cost_usd
                               / COUNT(DISTINCT thread_id
                                       WHERE outcome IN ('answered', 'cart'))
```

All divisions return `null` when the denominator is zero or total cost is unknown. An unpriced turn
is `NULL`, not `$0`; a zero-token guardrail turn is still part of the per-turn denominator.

**Source:** `copilot_turns` (`thread_id`, `input_tokens`, `output_tokens`, `model`, `cost_usd`,
`created_at`); `api/config.py` prices; `api/copilot_metrics.py` (`estimate_cost_usd`, `record_turn`);
`api/routers/events.py` and `api/routers/admin.py` (`unit_economics`). `cost_usd` was reserved in
phase 9 and is written from phase 13 onward.

**Caveats:**

- **Estimate, not invoice.** Provider billing, discounts, free tiers, retries, currency conversion
  and rounding can differ. A free-tier model can make the estimate `$0` while quota still binds.
- **Token coverage.** If a provider omits usage metadata, the turn has no cost and the aggregate
  understates spend. Report the share of turns with reported usage; do not silently call missing
  usage zero.
- **Per-turn denominator includes zero-token guardrail/error turns**, which pulls the mean down but
  is a true share of all logged turns. Recompute by route if comparing model configurations.
- **Conversation boundary is `thread_id`.** A client-generated thread reset creates a new
  conversation and hides the prior spend.
- **Model spend only.** Excludes embeddings, vector search, hosting, payment fees, marketing and
  human time.
- **Guardrail tokens are not included.** The pre-agent classifier's LLM call happens outside the
  agent result whose token usage is recorded, so switching `GUARDRAIL_LLM_MODE` can change true
  spend without moving this metric. Treat the estimate as a lower bound when the guardrail is active.
- **Outliers and small n.** A single long conversation can dominate the mean; report count and median
  beside it.
- **Price changes over time.** Historical estimates must record the configured price version; this
  repository exposes one current deployment price, not a price table by date.

---

## Cost per assisted order

**Status:** Live (phase 13). This is the north-star **guardrail** metric, not a revenue metric.

**Definition:** Estimated copilot model spend allocated across server-confirmed orders whose session
had at least one copilot turn in the window.

**Formula:**

```text
assisted_orders = paid orders o
                  WHERE o.session_id IS NOT NULL
                    AND EXISTS (
                        SELECT 1 FROM copilot_turns ct
                        WHERE ct.session_id = o.session_id
                          AND ct.created_at > now() - :window
                    )

cost_per_assisted_order_usd = total_cost_usd / COUNT(assisted_orders)
```

`total_cost_usd` is all copilot model spend in the window, `SUM(cost_usd)`, not only spend in
converting sessions. If a session has multiple orders, the full session cost is effectively allocated
across all of them; do not present this as a per-order causal cost.

**Source:** `copilot_turns.cost_usd` and `copilot_turns.session_id` joined to `orders.session_id`;
`GET /events/summary.unit_economics`; `GET /admin/kpi.unit_economics`; SLO budgets in
[`SLOS.md`](./SLOS.md#43-cost-budgets).

**Caveats:**

- **Correlational attribution.** The order may have happened without the copilot. There is no
  holdout, so this is cost *associated with* assisted orders, not incremental cost or ROI.
- **Includes abandoned conversations** in converting sessions; excludes copilot conversations that
  influenced a purchase in a later session or another device.
- **Simulated orders.** No real margin, refunds or fulfilment; do not present this as a real
  acquisition cost.
- **Model spend only.** Excludes infrastructure, embeddings, search, hosting, payment fees and
  marketing.
- **Missing token usage understates spend**, and therefore understates this cost ratio.
- **Extremely unstable at small order counts.** Report numerator (spend), denominator (assisted
  orders) and a confidence interval; never a bare ratio.
- **Report beside conversion and AOV, never alone.** A low cost per assisted order can simply mean
  few assisted orders; a high one can be a few expensive conversations.
- **Budget, not result.** The `< $0.02` target in [`SLOS.md`](./SLOS.md) is a budget; it is not a
  measured historical claim.

---

## Latency p50 and p95

**Status:** Live (phase 9; dashboard phase 12). `copilot_turns.latency_ms` is written per turn;
`GET /events/summary` returns `copilot_quality.avg_latency_ms`, `p50_latency_ms` and
`p95_latency_ms`; `GET /admin/kpi` returns `latency_p50_ms` and `latency_p95_ms` from the same
aggregation.

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
`GET /admin/kpi.copilot_quality` (phase 12); SLO targets and alert thresholds are in
[`SLOS.md`](./SLOS.md).

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

## Service-level objectives

**Status:** Live (policy, phase 13). Targets and alert thresholds are committed in
[`SLOS.md`](./SLOS.md). **Attainment is not yet computed**: `/events/summary` and `/admin/kpi`
measure copilot total-turn latency, but text/image search, sizing latency and copilot TTFT are not
persisted as timing metrics. Do not quote a target as a measured result.

| SLO | Target | Measurement status |
|---|---|---|
| Text `/search` latency | p50 < 35 ms · p95 < 80 ms | Target only; no timing table yet |
| `/search/image` latency | p50 < 120 ms · p95 < 250 ms | Target only; no timing table yet |
| `/sizing/recommend` latency | p50 < 5 ms · p95 < 15 ms | Target only; no timing table yet |
| Copilot TTFT | p50 < 1.2 s · p95 < 3.0 s | Target only; total-turn p50/p95 is live, TTFT is not |
| Core REST availability | 99.5% (30-day) | No hosted SLO calculation committed |
| AI copilot availability | 99.0% (30-day) | No hosted SLO calculation committed |
| Cost per conversation | < $0.005 | Live estimate via `unit_economics`; budget not an invoice |
| Cost per assisted order | < $0.02 | Live estimate via `unit_economics`; budget not an invoice |

Error budgets, provider-rollover criteria, alert thresholds and the `GUARDRAIL_LLM_MODE` /
policy-cache trade-offs are detailed in [`SLOS.md`](./SLOS.md).

---

## Copilot business scenario benchmark

**Status:** Live (CI, phase 14). `pipelines/eval_copilot.py` runs **54 authored scenarios** from
[`pipelines/golden/copilot.json`](../pipelines/golden/copilot.json) and writes
[`pipelines/results/copilot_eval.json`](../pipelines/results/copilot_eval.json). It is an offline
contract gate, not a live-traffic or human-quality metric.

| Suite | Cases | What is checked | Hard gate |
|---|---:|---|---|
| `guardrails` | 18 | ID/EN greetings, off-topic coding/math/politics, injections/jailbreaks, and valid shopping queries that must pass through (`None`) | accuracy ≥ 95% (current 18/18) |
| `sizing` | 18 | Exact `size_kind`, `applicable`, suggested size/waist/conversion, source/confidence, brand offsets, disclaimer; unsupported lingerie/luggage/kids must not guess | accuracy = 100% (current 18/18) |
| `budget_outfits` | 8 | `within_budget is True` and `total_idr <= budget_idr` at 500k, 1M, 1.5M and 2.5M IDR | invariant violations = 0 |
| `policy_rag` | 10 | Expected authored policy/sizing chunk title is in the top-3 retrieved chunks | recall ≥ 90% (current 10/10) |

The guardrail suite runs `classify(..., use_llm=False)`, so it measures the deterministic regex layer,
not the live LLM classifier. The sizing suite calls `recommend_size_core()` plus two ASIN-backed
`recommend_size()` cases against Postgres. The outfit suite uses the seeded catalog. Policy retrieval
uses `lookup_store_policy(k=3)`.

**Caveats:**

- **Authored scenarios, not a random sample.** The gates catch regressions in known contracts; they do
  not estimate production accuracy or user satisfaction.
- **Guardrail coverage is finite.** Eighteen labelled cases do not replace an adversarial corpus or a
  human false-positive audit.
- **Sizing exactness is chart conformance**, not fit accuracy; the underlying tops/bottoms/dress charts
  are authored store estimates, and brand offsets are demo data.
- **Outfit checks prove the budget invariant**; `item_count` is reported but is not a hard gate. The
  suite does not measure style relevance, stock, comfort, or whether the shopper likes the combination.
- **Policy recall checks retrieval only.** The expected chunk title appearing in top-3 does not prove
  the copilot's final prose is correct or complete.
- **Catalog-dependent.** Outfit cases can change if the seeded catalog or search ranking changes; a
  failure there is a product/search regression signal, not a revenue signal.

---

## Supporting telemetry

These are useful for trust and diagnosis, but they are not north-star or revenue metrics.

| Metric | Status | Definition / formula | Source | Caveat |
|---|---|---|---|---|
| Guardrail short-circuit rate | Live (phase 9) | `turns with outcome='guardrail' / all copilot turns` | `copilot_turns.outcome` → `copilot_quality.guardrail_rate`, `/admin/kpi.copilot_quality.guardrail_turns` | Counts every guardrail label (regex or LLM), including greetings; it is not only unsafe blocks. The phase-14 golden set locks 18 labelled guardrail cases, but broader false positives still need a human audit. |
| Items per order | Computable now, not exposed | `SUM(orders.item_count) / COUNT(orders)` or `SUM(order_items.qty) / COUNT(DISTINCT order_id)` | `orders.item_count`, `order_items.qty` | Same simulation caveats as AOV; no cancellation model. |
| Offline retrieval quality | Live (offline) | nDCG@10, MRR@10, HR@10, P@10 over a fixed query set | [`search_eval_multilingual.json`](../pipelines/results/search_eval_multilingual.json), [`search_eval.json`](../pipelines/results/search_eval.json) | 32 queries, authored relevance labels; a proxy, not online behaviour. |
| Offline policy/size-guide RAG | Live (offline) | RAGAS Context Precision / Recall at k=3 | [`rag_eval.json`](../pipelines/results/rag_eval.json) | 12 bilingual queries; measures retrieval, not answer correctness or fit accuracy. Current committed run: recall 1.0000, context precision 0.8750. |
| Sizing category coverage | Live (structural) | Share of seed `(department, category)` pairs with an explicit `size_kind` | [`catalog_categories.json`](../api/knowledge/catalog_categories.json), [`size_kind_map.json`](../api/knowledge/size_kind_map.json) | 260/260 pairs mapped; this is coverage, **not** fit accuracy. The map is authored and some ambiguous categories stay `unsupported`. |
| Offline recommendation ranking | Live (offline) | Sequential leave-one-out HR@10 / HR@5 / MRR@10 / nDCG@10 | [`recs_eval.json`](../pipelines/results/recs_eval.json) | 5,000 sampled active users, seed 42; offline holdout, not online CTR. |

---

## Phase map — when each metric becomes quotable

| Phase | What becomes available |
|---|---|
| 8 | Dictionary and business framing only; no new metric is computed. |
| 9 (shipped) | AOV/revenue from `orders`; `copilot_turns` logging (latency, tools, guardrail, outcome, tokens); `copilot_feedback` + thumbs UI; copilot-user vs non-user cohorts (descriptive); `copilot_quality` aggregate (unanswered/guardrail/latency/thumbs). |
| 10 (shipped) | `outcome='unanswered'` logging through `flag_unanswered` (question stored, 300-char cap); copilot refocused on sizing, policy and outfit jobs. |
| 10b (shipped) | Category-aware sizing: every seed `(department, category)` pair mapped to an explicit size kind; tops/bottoms/dress use authored men's/women's TB/BB charts, footwear/waist/belt/headwear use cited standards, brand offsets marked `source=demo`; `GET /sizing/{asin}` + `POST /sizing/recommend` and a no-LLM product-page advisor. **No fit-accuracy metric** — coverage is structural, not a quality claim. |
| 11 (shipped) | `GET /search/insights`: top queries, top zero-result queries, zero-result rate and session-level search-to-PDP; deterministic ID→EN rewrite retry on zero-result `/search` and `search_catalog`. |
| 12 (shipped) | Token-gated `GET /admin/kpi` and `/admin/kpi` page: confirmed-order revenue/AOV, session funnel, copilot vs non-copilot cohorts, conversation-level deflection, turn outcomes, search health, p50/p95 and a mandatory `synthetic` badge. Cost is added in phase 13. |
| 13 (shipped) | Per-turn cost from configurable per-million prices; `unit_economics` cost per turn / conversation / resolved conversation / assisted order; `GUARDRAIL_LLM_MODE=ambiguous`; 10-minute bounded policy-RAG cache; [`SLOS.md`](./SLOS.md) latency, availability, cost and fallback targets. Attainment is not yet instrumented for search/image/sizing/TTFT. |
| 14 (shipped) | Committed 54-scenario business benchmark in CI: guardrail accuracy/per-label precision/recall, exact sizing contracts, outfit budget invariant, policy top-3 recall; hard failure gates. |
| 15 | Seeded traffic simulator and case study; every simulated metric must carry a `synthetic` label. |

---

## Do not compute / do not claim

- **Revenue or ROI from `purchase_mock`.** Use `orders`; even then checkout is simulated.
- **AOV from `events.price_idr`.** The client supplies it.
- **Profit, contribution margin or support-cost savings.** No COGS, margin or support baseline exists.
- **Lift or causality from cohort comparison.** There is no holdout; state the selection bias.
- **A provider invoice or ROI from the cost estimate.** Prices are configurable planning inputs, not billing records.
- **A single blended north-star score.** Conversion and AOV are reported separately.
- **"Deflection" as human-agent deflection.** There is no human handoff.
- **Any number from the simulator as real traffic.** Label it `synthetic`, always.
