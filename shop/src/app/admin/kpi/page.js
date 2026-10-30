"use client";

import { useEffect, useState } from "react";
import AdminLoginModal from "@/components/AdminLoginModal";
import { adminLogout, getAdminKpi, getAdminSession } from "@/lib/api";
import { formatRp } from "@/lib/formatRp";

const WINDOWS = [7, 30, 90];
const SLA_TARGET_MS = 5000;
const FUNNEL_INTERVALS = 4;

const numberFormat = new Intl.NumberFormat("id-ID");

function num(value) {
  return value == null || Number.isNaN(Number(value))
    ? "—"
    : numberFormat.format(Number(value));
}

function decimal(value, digits = 1) {
  return Number(value).toFixed(digits).replace(".", ",");
}

function pct(value, digits = 1) {
  return value == null || Number.isNaN(Number(value))
    ? "—"
    : `${decimal(Number(value) * 100, digits)}%`;
}

function money(value) {
  return value == null ? "—" : formatRp(value);
}

function usd(value) {
  if (value == null || Number.isNaN(Number(value))) {
    return "—";
  }
  const digits = Math.abs(Number(value)) < 10 ? 4 : 2;
  return `$${new Intl.NumberFormat("id-ID", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(Number(value))}`;
}

function ms(value) {
  if (value == null || Number.isNaN(Number(value))) {
    return "—";
  }
  if (Number(value) >= 1000) {
    return `${decimal(Number(value) / 1000, 2)} s`;
  }
  return `${Math.round(Number(value))} ms`;
}

function generatedAt(value) {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "—";
  }
  return date.toLocaleString("id-ID", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function niceStep(value) {
  if (!value || value <= 0) {
    return 1;
  }
  const magnitude = 10 ** Math.floor(Math.log10(value));
  const normalized = value / magnitude;
  const candidates = [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10];
  const step = candidates.find((candidate) => normalized <= candidate) ?? 10;
  return step * magnitude;
}

function funnelScale(sessions) {
  const entered = Number(sessions) > 0 ? Number(sessions) : FUNNEL_INTERVALS;
  const step = niceStep(entered / FUNNEL_INTERVALS);
  return { max: step * FUNNEL_INTERVALS, step };
}

function buildFunnelStages(funnel = {}) {
  const sessions = Number(funnel.sessions) || 0;
  const viewed = Number(funnel.view_product_sessions) || 0;
  const cart = Number(funnel.add_to_cart_sessions) || 0;
  const checkout = Number(funnel.checkout_start_sessions) || 0;
  const ordered = Number(funnel.order_sessions) || 0;

  return [
    {
      label: "Viewed product",
      transition: "Sessions → Viewed",
      value: viewed,
      previous: sessions,
      strong: true,
    },
    {
      label: "Added to cart",
      transition: "Viewed → Cart",
      value: cart,
      previous: viewed,
      strong: false,
    },
    {
      label: "Started checkout",
      transition: "Cart → Checkout",
      value: checkout,
      previous: cart,
      strong: false,
    },
    {
      label: "Paid order",
      transition: "Checkout → Paid",
      value: ordered,
      previous: checkout,
      strong: true,
    },
  ];
}

function biggestDrop(funnel) {
  return buildFunnelStages(funnel).reduce(
    (worst, stage) => {
      const lost = Math.max(0, stage.previous - stage.value);
      return lost > worst.lost ? { label: stage.transition, lost } : worst;
    },
    { label: "none", lost: 0 }
  );
}

function SectionHead({ title, hint }) {
  return (
    <div className="mb-3 flex flex-wrap items-baseline gap-2.5 border-b border-border pb-2.5">
      <h2 className="text-[11.5px] font-bold uppercase tracking-[0.1em] text-foreground">
        {title}
      </h2>
      {hint ? (
        <span className="ml-auto text-xs text-muted">{hint}</span>
      ) : null}
    </div>
  );
}

function Tile({ label, value, foot, hero = false }) {
  return (
    <div className="relative flex flex-col gap-1 overflow-hidden rounded-xl border border-border bg-surface px-4 py-3.5">
      {hero ? (
        <span className="absolute inset-x-0 top-0 h-0.5 bg-[#0060c0]" />
      ) : null}
      <p className="text-[11px] font-semibold uppercase tracking-[0.07em] text-muted">
        {label}
      </p>
      <p
        className={
          hero
            ? "text-[27px] font-semibold leading-tight tracking-[-0.028em] text-foreground tabular-nums"
            : "text-[23px] font-semibold leading-tight tracking-[-0.028em] text-foreground tabular-nums"
        }
      >
        {value}
      </p>
      <p className="min-h-[18px] text-xs text-muted">{foot}</p>
    </div>
  );
}

function Card({ children, className = "" }) {
  return (
    <div
      className={`rounded-xl border border-border bg-surface p-4 sm:p-[18px] ${className}`}
    >
      {children}
    </div>
  );
}

function MiniStat({ label, value, sub }) {
  return (
    <div className="border-l border-border px-3 first:border-l-0 first:pl-0 last:pr-0">
      <p className="text-[10.5px] font-semibold uppercase leading-tight tracking-[0.06em] text-muted">
        {label}
      </p>
      <p className="mt-0.5 text-[17px] font-semibold tracking-[-0.022em] tabular-nums">
        {value}
        {sub ? (
          <span className="ml-1 text-[.66em] font-medium text-muted">{sub}</span>
        ) : null}
      </p>
    </div>
  );
}

function DeltaPill({ value, unit }) {
  if (value == null || Number.isNaN(Number(value))) {
    return <span className="text-muted">—</span>;
  }
  const positive = Number(value) >= 0;
  const text =
    unit === "pt"
      ? `${positive ? "+" : ""}${decimal(Number(value) * 100, 1)} pt`
      : `${positive ? "+" : ""}${decimal(Number(value) * 100, 1)}%`;
  return (
    <span
      className={
        positive
          ? "inline-flex rounded bg-[#e6f4ee] px-1.5 py-0.5 text-[11px] font-semibold text-[#0b6b45] tabular-nums"
          : "inline-flex rounded bg-[#fbeaea] px-1.5 py-0.5 text-[11px] font-semibold text-[#b3242a] tabular-nums"
      }
    >
      {text}
    </span>
  );
}

function StatusPill({ state, children }) {
  if (state === "none") {
    return (
      <span className="inline-flex rounded-full bg-brand-soft px-2 py-0.5 text-[10.5px] font-semibold text-muted">
        {children}
      </span>
    );
  }
  return (
    <span
      className={
        state === "ok"
          ? "inline-flex rounded-full bg-[#e6f4ee] px-2 py-0.5 text-[10.5px] font-semibold text-[#0b6b45]"
          : "inline-flex rounded-full bg-[#fbeaea] px-2 py-0.5 text-[10.5px] font-semibold text-[#b3242a]"
      }
    >
      {children}
    </span>
  );
}

function FunnelCard({ funnel }) {
  const stages = buildFunnelStages(funnel);
  const { max, step } = funnelScale(funnel.sessions);
  const ticks = Array.from({ length: FUNNEL_INTERVALS + 1 }, (_, index) =>
    Math.round(step * index)
  );

  return (
    <Card>
      <div className="mb-3 flex flex-wrap items-baseline gap-2">
        <h3 className="text-[11px] font-semibold uppercase tracking-[0.06em] text-muted">
          Sessions reaching each stage
        </h3>
        <span className="ml-auto text-[11.5px] text-muted">
          {num(funnel.sessions)} sessions entered · axis 0 — {num(max)}
        </span>
      </div>

      <div className="grid grid-cols-[104px_minmax(0,1fr)_48px] items-center sm:grid-cols-[150px_minmax(0,1fr)_62px_84px]">
        <div className="pb-2 text-[10.5px] font-bold uppercase tracking-[0.07em] text-muted">
          Stage
        </div>
        <div className="pb-2" />
        <div className="pb-2 text-right text-[10.5px] font-bold uppercase tracking-[0.07em] text-muted">
          Sessions
        </div>
        <div className="hidden pb-2 text-right text-[10.5px] font-bold uppercase tracking-[0.07em] text-muted sm:block">
          % of prev.
        </div>

        {stages.map((stage) => {
          const reached = Math.min(Math.max(0, stage.value), max);
          const lost = Math.max(0, stage.previous - stage.value);
          const reachedShare = (reached / max) * 100;
          const lostShare = Math.min(
            100 - reachedShare,
            (lost / max) * 100
          );
          const rate =
            stage.previous > 0 ? stage.value / stage.previous : null;

          return (
            <div className="contents" key={stage.label}>
              <div className="truncate py-1.5 pr-3 text-[12.5px] text-muted">
                {stage.strong ? (
                  <b className="font-semibold text-foreground">{stage.label}</b>
                ) : (
                  stage.label
                )}
              </div>
              <div className="py-1.5">
                <div
                  className="relative h-6 overflow-hidden rounded-[4px]"
                  style={{
                    backgroundColor: "#f6f6f7",
                    backgroundImage:
                      "linear-gradient(90deg, #e6e6e9 1px, transparent 1px)",
                    backgroundSize: `${100 / FUNNEL_INTERVALS}% 100%`,
                  }}
                  aria-hidden="true"
                >
                  {lostShare > 0 ? (
                    <span
                      className="absolute inset-y-0 block"
                      style={{
                        left: `${reachedShare}%`,
                        width: `${lostShare}%`,
                        backgroundColor: "#d9d9de",
                      }}
                    />
                  ) : null}
                  <span
                    className="absolute inset-y-0 left-0 block"
                    style={{
                      width: `${reachedShare}%`,
                      backgroundColor: "#141414",
                    }}
                  />
                </div>
              </div>
              <div className="py-1.5 text-right text-[14px] font-semibold tabular-nums text-foreground">
                {num(stage.value)}
              </div>
              <div className="hidden py-1.5 text-right text-[12px] tabular-nums text-muted sm:block">
                {pct(rate)}
              </div>
            </div>
          );
        })}

        <div />
        <div className="flex justify-between border-t border-border pt-1.5 text-[10.5px] tabular-nums text-muted">
          {ticks.map((tick) => (
            <span key={tick}>{num(tick)}</span>
          ))}
        </div>
        <div />
        <div className="hidden sm:block" />
      </div>

      <div className="mt-3.5 flex flex-wrap gap-x-4 gap-y-1.5 border-t border-border pt-3 text-[11.5px] text-muted">
        <span className="inline-flex items-center gap-1.5">
          <i className="block h-2.5 w-2.5 rounded-[2px] bg-[#141414]" />
          reached this stage
        </span>
        <span className="inline-flex items-center gap-1.5">
          <i className="block h-2.5 w-2.5 rounded-[2px] bg-[#d9d9de]" />
          dropped off at this step
        </span>
        <span className="ml-auto">
          each bar totals the previous stage — rows are additive
        </span>
      </div>
    </Card>
  );
}

function CohortCard({ cohorts = {} }) {
  const baseline = cohorts.non_copilot_sessions ?? {};
  const copilot = cohorts.copilot_sessions ?? {};

  const addDelta =
    baseline.add_to_cart_rate != null && copilot.add_to_cart_rate != null
      ? copilot.add_to_cart_rate - baseline.add_to_cart_rate
      : null;
  const conversionDelta =
    baseline.conversion_rate != null && copilot.conversion_rate != null
      ? copilot.conversion_rate - baseline.conversion_rate
      : null;
  const aovDelta =
    baseline.aov_idr && copilot.aov_idr != null
      ? (copilot.aov_idr - baseline.aov_idr) / baseline.aov_idr
      : null;

  return (
    <Card>
      <div className="mb-3">
        <h3 className="text-[14px] font-semibold tracking-[-0.012em] text-foreground">
          Copilot vs. baseline
        </h3>
        <p className="mt-1 text-[12.5px] text-muted">
          <b className="font-semibold text-foreground">
            {num(copilot.sessions)}
          </b>{" "}
          copilot sessions compared with{" "}
          <b className="font-semibold text-foreground">
            {num(baseline.sessions)}
          </b>{" "}
          non-copilot sessions.
        </p>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-left">
          <thead>
            <tr>
              <th className="border-b border-border pb-2 text-[10.5px] font-bold uppercase tracking-[0.07em] text-muted">
                Metric
              </th>
              <th className="border-b border-border pb-2 text-right text-[10.5px] font-bold uppercase tracking-[0.07em] text-muted">
                Baseline
              </th>
              <th className="border-b border-border pb-2 text-right text-[10.5px] font-bold uppercase tracking-[0.07em] text-[#0060c0]">
                Copilot
              </th>
              <th className="border-b border-border pb-2 pl-3.5 text-left text-[10.5px] font-bold uppercase tracking-[0.07em] text-muted">
                Δ vs baseline
              </th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td className="border-b border-border py-2.5 text-[12.5px] text-muted">
                Add to cart
                <small className="mt-0.5 block text-[10.5px] text-muted">
                  of cohort sessions
                </small>
              </td>
              <td className="border-b border-border py-2.5 text-right text-[13.5px] font-semibold tabular-nums text-muted">
                {pct(baseline.add_to_cart_rate)}
              </td>
              <td className="border-b border-border py-2.5 text-right text-[13.5px] font-semibold tabular-nums text-[#0060c0]">
                {pct(copilot.add_to_cart_rate)}
              </td>
              <td className="border-b border-border py-2.5 pl-3.5">
                <DeltaPill value={addDelta} unit="pt" />
              </td>
            </tr>
            <tr>
              <td className="border-b border-border py-2.5 text-[12.5px] text-muted">
                Conversion
                <small className="mt-0.5 block text-[10.5px] text-muted">
                  of cohort sessions
                </small>
              </td>
              <td className="border-b border-border py-2.5 text-right text-[13.5px] font-semibold tabular-nums text-muted">
                {pct(baseline.conversion_rate)}
              </td>
              <td className="border-b border-border py-2.5 text-right text-[13.5px] font-semibold tabular-nums text-[#0060c0]">
                {pct(copilot.conversion_rate)}
              </td>
              <td className="border-b border-border py-2.5 pl-3.5">
                <DeltaPill value={conversionDelta} unit="pt" />
              </td>
            </tr>
            <tr>
              <td className="py-2.5 text-[12.5px] text-muted">
                AOV
                <small className="mt-0.5 block text-[10.5px] text-muted">
                  per paid order
                </small>
              </td>
              <td className="py-2.5 text-right text-[13.5px] font-semibold tabular-nums text-muted">
                {money(baseline.aov_idr)}
              </td>
              <td className="py-2.5 text-right text-[13.5px] font-semibold tabular-nums text-[#0060c0]">
                {money(copilot.aov_idr)}
              </td>
              <td className="py-2.5 pl-3.5">
                <DeltaPill value={aovDelta} unit="%" />
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <p className="mt-3.5 border-t border-border pt-3 text-[11.5px] leading-relaxed text-muted">
        <b className="font-semibold text-muted">pt</b> = percentage points (an
        absolute difference between two rates);{" "}
        <b className="font-semibold text-muted">%</b> = a relative change.{" "}
        {cohorts.caveat || "Observational comparison, not a randomized trial."}
      </p>
    </Card>
  );
}

function UnitEconomicsCard({ economics = {} }) {
  return (
    <Card>
      <div className="mb-3">
        <h3 className="text-[14px] font-semibold tracking-[-0.012em] text-foreground">
          Assistant cost
        </h3>
        <p className="mt-1 text-[12.5px] text-muted">
          Provider spend attributed to copilot activity.
        </p>
      </div>

      <div className="flex flex-wrap items-baseline gap-2">
        <b className="text-[26px] font-semibold tracking-[-0.028em] tabular-nums text-foreground">
          {usd(economics.total_cost_usd)}
        </b>
        <span className="text-xs text-muted">
          total spend, last selected window
        </span>
      </div>

      <dl className="mt-3 border-t border-border">
        {[
          ["Per turn", economics.avg_cost_per_turn_usd],
          ["Per conversation", economics.cost_per_conversation_usd],
          [
            "Per resolved conversation",
            economics.cost_per_resolved_conversation_usd,
          ],
          ["Per assisted order", economics.cost_per_assisted_order_usd],
        ].map(([label, value]) => (
          <div
            key={label}
            className="flex items-baseline gap-3 border-b border-border py-2.5 last:border-b-0"
          >
            <dt className="text-[12.5px] text-muted">{label}</dt>
            <dd className="ml-auto text-[14px] font-semibold tabular-nums text-foreground">
              {usd(value)}
            </dd>
          </div>
        ))}
      </dl>

      <p className="mt-3.5 border-t border-border pt-3 text-[11.5px] leading-relaxed text-muted">
        Cost per assisted order is the only ratio here that also moves with
        revenue, so it is the one to track week to week.
      </p>
    </Card>
  );
}

function AssistantCard({ quality = {} }) {
  const total = Number(quality.total_turns) || 0;
  const answered = Number(quality.answered_turns) || 0;
  const guardrail = Number(quality.guardrail_turns) || 0;
  const unanswered = Number(quality.unanswered_turns) || 0;
  const residual = Math.max(0, total - answered - guardrail - unanswered);

  const segments = [
    { label: "Answered", count: answered, color: "#1a63c9" },
    { label: "Guardrail (refused)", count: guardrail, color: "#b26a00" },
    { label: "Unanswered", count: unanswered, color: "#b3242a" },
    {
      label: "Cart / error (residual)",
      count: residual,
      color: "#d4d4d8",
      residual: true,
    },
  ];

  const p50 = quality.latency_p50_ms;
  const p95 = quality.latency_p95_ms;
  const ratio = p50 && p95 ? p95 / p50 : null;

  const latencyRows = [
    {
      label: "p50",
      detail: "typical turn",
      value: p50,
      sub: null,
    },
    {
      label: "p95",
      detail: "tail latency (SLO)",
      value: p95,
      sub: ratio ? `p95 is ${decimal(ratio, 1)}× p50` : null,
    },
  ];

  return (
    <Card>
      <div className="mb-3">
        <h3 className="text-[14px] font-semibold tracking-[-0.012em] text-foreground">
          Copilot performance
        </h3>
        <p className="mt-1 text-[12.5px] text-muted">
          Turn outcomes, SLA latency and explicit sentiment. Outcomes do not sum
          to all turns.
        </p>
      </div>

      <div className="mb-2 flex flex-wrap items-baseline gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-[0.06em] text-muted">
          Turn outcomes
        </span>
        <span className="ml-auto text-[11.5px] text-muted">
          of {num(total)} turns
        </span>
      </div>

      <div
        className="flex h-3 w-full overflow-hidden rounded-[3px] bg-[#f0f0f2]"
        aria-hidden="true"
      >
        {segments.map((segment) => (
          <i
            key={segment.label}
            className="block h-full"
            style={{
              width: `${total ? (segment.count / total) * 100 : 0}%`,
              backgroundColor: segment.color,
            }}
          />
        ))}
      </div>

      <div className="mt-3 grid gap-1.5">
        {segments.map((segment) => (
          <div
            key={segment.label}
            className="grid grid-cols-[12px_minmax(0,1fr)_44px_50px] items-center gap-2 text-xs text-muted"
          >
            <i
              className="block h-2.5 w-2.5 rounded-[2px]"
              style={{ backgroundColor: segment.color }}
            />
            <span className="truncate">
              {segment.label}
              {segment.residual ? (
                <small className="ml-1 not-italic text-muted">
                  (residual)
                </small>
              ) : null}
            </span>
            <span className="text-right font-semibold tabular-nums text-foreground">
              {num(segment.count)}
            </span>
            <span className="text-right tabular-nums text-muted">
              {pct(total ? segment.count / total : null)}
            </span>
          </div>
        ))}
      </div>

      <p className="mt-3 border-t border-border pt-3 text-[11.5px] leading-relaxed text-muted">
        The API exposes three outcome buckets separately; the remainder is the{" "}
        <code>cart</code> and <code>error</code> outcomes combined.{" "}
        <code>cart</code> turns are successful, so “answered” alone understates
        the assistant — it is not the success rate.
      </p>

      <div className="mt-4 border-t border-border pt-3">
        <table className="w-full border-collapse text-left">
          <thead>
            <tr>
              <th className="border-b border-border pb-2 text-[10.5px] font-bold uppercase tracking-[0.07em] text-muted">
                Latency SLA
              </th>
              <th className="border-b border-border pb-2 text-right text-[10.5px] font-bold uppercase tracking-[0.07em] text-muted">
                Observed
              </th>
              <th className="border-b border-border pb-2 text-right text-[10.5px] font-bold uppercase tracking-[0.07em] text-muted">
                Target
              </th>
              <th className="border-b border-border pb-2 pl-3 text-left text-[10.5px] font-bold uppercase tracking-[0.07em] text-muted">
                Status
              </th>
            </tr>
          </thead>
          <tbody>
            {latencyRows.map((row) => {
              const within = row.value != null && row.value < SLA_TARGET_MS;
              const state =
                row.value == null ? "none" : within ? "ok" : "bad";
              return (
                <tr key={row.label}>
                  <td className="border-b border-border py-2.5 text-[12.5px] text-muted">
                    <b className="font-semibold text-foreground">{row.label}</b>{" "}
                    · {row.detail}
                    {row.sub ? (
                      <small className="mt-0.5 block text-[10.5px] text-muted">
                        {row.sub}
                      </small>
                    ) : null}
                  </td>
                  <td className="border-b border-border py-2.5 text-right text-[13.5px] font-semibold tabular-nums text-foreground">
                    {ms(row.value)}
                  </td>
                  <td className="border-b border-border py-2.5 text-right text-[12.5px] tabular-nums text-muted">
                    &lt; 5 s
                  </td>
                  <td className="border-b border-border py-2.5 pl-3">
                    <StatusPill state={state}>
                      {state === "none"
                        ? "No data"
                        : within
                          ? "Within SLA"
                          : "Over SLA"}
                    </StatusPill>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="mt-4 grid grid-cols-3 border-t border-border pt-3">
        <MiniStat
          label="Unanswered rate"
          value={pct(quality.unanswered_rate)}
        />
        <MiniStat
          label="Deflection"
          value={pct(quality.deflection_rate)}
          sub={`${num(quality.deflection_answered_conversations)}/${num(
            quality.deflection_eligible_conversations
          )}`}
        />
        <MiniStat
          label="Thumbs-up"
          value={pct(quality.thumbs_up_rate)}
          sub={`${num(quality.thumbs_up)}/${num(quality.thumbs_down)}`}
        />
      </div>
    </Card>
  );
}

function SearchCard({ search = {} }) {
  const total = Number(search.total_searches) || 0;
  const zeroResultQueries = search.top_zero_result_queries ?? [];
  const zeroSet = new Set(zeroResultQueries.map((row) => row.query));

  const merged = new Map();
  for (const row of search.top_queries ?? []) {
    merged.set(row.query, { ...row, zero: zeroSet.has(row.query) });
  }
  for (const row of zeroResultQueries) {
    if (!merged.has(row.query)) {
      merged.set(row.query, { ...row, zero: true });
    }
  }

  const listed = [...merged.values()]
    .sort((a, b) => b.count - a.count || a.query.localeCompare(b.query))
    .slice(0, 6);
  const listedCount = listed.reduce((sum, row) => sum + row.count, 0);
  const remainder = Math.max(0, total - listedCount);

  return (
    <Card>
      <div className="mb-3">
        <h3 className="text-[14px] font-semibold tracking-[-0.012em] text-foreground">
          Search &amp; unmet demand
        </h3>
        <p className="mt-1 text-[12.5px] text-muted">
          Client-posted search events. Zero-result queries are demand the catalog
          could not serve.
        </p>
      </div>

      <div className="grid grid-cols-3">
        <MiniStat label="Searches" value={num(search.total_searches)} />
        <MiniStat
          label="Zero result"
          value={num(search.zero_result_searches)}
          sub={pct(search.zero_result_rate)}
        />
        <MiniStat label="Search → PDP" value={pct(search.search_to_pdp_rate)} />
      </div>

      <div className="mt-4 border-t border-border pt-3">
        <div className="mb-2 flex flex-wrap items-baseline gap-2">
          <span className="text-[11px] font-semibold uppercase tracking-[0.06em] text-muted">
            Demand share
          </span>
          <span className="ml-auto text-[11.5px] text-muted">
            each bar is that query’s share of {num(total)} searches
          </span>
        </div>

        {total === 0 ? (
          <p className="py-3 text-sm text-muted">
            No searches recorded in this window.
          </p>
        ) : (
          <div className="flex flex-col gap-2.5">
            {listed.map((row) => {
              const share = row.count / total;
              return (
                <div
                  key={row.query}
                  className="grid grid-cols-[minmax(0,1fr)_40px_46px] items-center gap-x-2.5 gap-y-0.5"
                >
                  <div className="flex min-w-0 items-center gap-1.5 text-[12.5px] text-foreground">
                    <span className="truncate">{row.query}</span>
                    {row.zero ? (
                      <em className="shrink-0 rounded bg-[#fbeaea] px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.04em] text-[#b3242a] not-italic">
                        0 results
                      </em>
                    ) : null}
                  </div>
                  <div className="text-right text-xs font-semibold tabular-nums text-foreground">
                    {num(row.count)}
                  </div>
                  <div className="text-right text-[11.5px] tabular-nums text-muted">
                    {pct(share)}
                  </div>
                  <div
                    className="col-span-3 h-2 overflow-hidden rounded-[3px] bg-[#f0f0f2]"
                    style={{
                      backgroundImage:
                        "linear-gradient(90deg, #e6e6e9 1px, transparent 1px)",
                      backgroundSize: "50% 100%",
                    }}
                    aria-hidden="true"
                  >
                    <i
                      className="block h-full"
                      style={{
                        width: `${share * 100}%`,
                        backgroundColor: row.zero ? "#b3242a" : "#5c5c5c",
                      }}
                    />
                  </div>
                </div>
              );
            })}

            <div className="grid grid-cols-[minmax(0,1fr)_40px_46px] items-center gap-x-2.5 gap-y-0.5">
              <div className="truncate text-[12.5px] text-muted">
                Other / unlisted
              </div>
              <div className="text-right text-xs font-semibold tabular-nums text-foreground">
                {num(remainder)}
              </div>
              <div className="text-right text-[11.5px] tabular-nums text-muted">
                {pct(total ? remainder / total : null)}
              </div>
              <div
                className="col-span-3 h-2 overflow-hidden rounded-[3px] bg-[#f0f0f2]"
                style={{
                  backgroundImage:
                    "linear-gradient(90deg, #e6e6e9 1px, transparent 1px)",
                  backgroundSize: "50% 100%",
                }}
                aria-hidden="true"
              >
                <i
                  className="block h-full"
                  style={{
                    width: `${total ? (remainder / total) * 100 : 0}%`,
                    backgroundColor: "#d4d4d8",
                  }}
                />
              </div>
            </div>
          </div>
        )}

        <p className="mt-3 border-t border-border pt-3 text-[11.5px] leading-relaxed text-muted">
          Bars encode each query’s share of all searches (additive, 0–100%); the
          grey row is the unlisted remainder. Both query lists are top-20 slices
          of the same events, so a zero-result query can appear in both — the two
          lists must not be summed.
        </p>
      </div>
    </Card>
  );
}

function TokenGate({ error, onSuccess }) {
  return (
    <main className="py-16">
      <div className="mx-auto max-w-lg px-7 text-center">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-muted">
          Toko Marcell · Operations
        </p>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight text-foreground">
          Operations &amp; KPI
        </h1>
        <p className="mt-2 text-sm text-muted">
          Sign in to view executive KPIs, operations telemetry and unit
          economics. This dashboard reads seeded demo data only.
        </p>
      </div>
      <AdminLoginModal
        open
        dismissible={false}
        onSuccess={onSuccess}
        initialError={error}
      />
    </main>
  );
}

export default function AdminKpiPage() {
  const [booted, setBooted] = useState(false);
  const [session, setSession] = useState(null);
  const [legacyToken, setLegacyToken] = useState(null);
  const [sinceDays, setSinceDays] = useState(30);
  const [kpi, setKpi] = useState(null);
  const [loading, setLoading] = useState(false);
  const [authError, setAuthError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const queryToken = params.get("token");
    const storedSession = queryToken ? null : getAdminSession();

    if (queryToken) {
      params.delete("token");
      const query = params.toString();
      window.history.replaceState(
        {},
        "",
        `${window.location.pathname}${query ? `?${query}` : ""}`
      );
    }

    queueMicrotask(() => {
      if (queryToken) {
        setLegacyToken(queryToken);
      } else if (storedSession?.access_token) {
        setSession(storedSession);
      }
      setBooted(true);
    });
  }, []);

  const accessToken = legacyToken || session?.access_token || null;

  useEffect(() => {
    if (!accessToken) {
      return undefined;
    }

    let cancelled = false;

    async function load() {
      setLoading(true);
      setLoadError("");
      try {
        const data = await getAdminKpi(accessToken, sinceDays);
        if (!cancelled) {
          setKpi(data);
          setAuthError("");
        }
      } catch (error) {
        if (cancelled) {
          return;
        }
        if (error.status === 401 || error.status === 403) {
          setKpi(null);
          setSession(null);
          setLegacyToken(null);
          setAuthError(
            "Your admin session is missing or expired. Sign in again to continue."
          );
        } else {
          setLoadError(error.message || "Could not load the admin dashboard.");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [accessToken, sinceDays, refreshKey]);

  function handleLoginSuccess(nextSession) {
    setSession(nextSession);
    setLegacyToken(null);
    setAuthError("");
    setLoadError("");
  }

  async function handleSignOut() {
    await adminLogout();
    setSession(null);
    setLegacyToken(null);
    setKpi(null);
    setAuthError("");
    setLoadError("");
  }

  if (!booted) {
    return (
      <main className="py-16">
        <p className="mx-auto max-w-6xl px-5 text-sm text-muted">
          Loading admin session…
        </p>
      </main>
    );
  }

  if (!accessToken) {
    return <TokenGate error={authError} onSuccess={handleLoginSuccess} />;
  }

  const metadata = kpi?.metadata ?? {};
  const revenue = kpi?.revenue ?? {};
  const funnel = kpi?.funnel ?? {};
  const cohorts = kpi?.cohorts ?? {};
  const search = kpi?.search_health ?? {};
  const quality = kpi?.copilot_quality ?? {};
  const economics = kpi?.unit_economics ?? {};

  const drop = biggestDrop(funnel);
  const username = session?.user?.username || (legacyToken ? "token" : "admin");
  const overallConversion = funnel.rates?.overall_conversion ?? null;
  const viewRate =
    funnel.sessions > 0
      ? (funnel.view_product_sessions ?? 0) / funnel.sessions
      : null;
  const orderRate =
    funnel.sessions > 0 ? (funnel.order_sessions ?? 0) / funnel.sessions : null;
  const oneIn =
    funnel.sessions > 0 && funnel.order_sessions > 0
      ? Math.round(funnel.sessions / funnel.order_sessions)
      : null;

  return (
    <main className="pb-16">
      <div className="border-b border-border bg-surface/80 backdrop-blur">
        <div className="mx-auto flex max-w-[1200px] flex-wrap items-center gap-3 px-5 py-3 sm:px-7">
          <div className="flex items-center gap-2.5">
            <div className="grid h-[26px] w-[26px] place-items-center rounded-[7px] bg-foreground text-[12px] font-bold text-white">
              TM
            </div>
            <div>
              <p className="text-[15px] font-semibold leading-tight tracking-[-0.015em] text-foreground">
                Toko Marcell
              </p>
              <p className="text-[11px] font-semibold uppercase tracking-[0.06em] text-muted">
                Operations
              </p>
            </div>
          </div>

          <div className="ml-auto flex flex-wrap items-center gap-2.5">
            <div
              role="group"
              aria-label="Time window"
              className="inline-flex rounded-[9px] bg-brand-soft p-0.5"
            >
              {WINDOWS.map((days) => (
                <button
                  key={days}
                  type="button"
                  aria-pressed={sinceDays === days}
                  onClick={() => setSinceDays(days)}
                  className={
                    sinceDays === days
                      ? "rounded-[7px] bg-surface px-2.5 py-1 text-[12.5px] font-medium text-foreground shadow-sm"
                      : "rounded-[7px] px-2.5 py-1 text-[12.5px] font-medium text-muted transition hover:text-foreground"
                  }
                >
                  {days}d
                </button>
              ))}
            </div>

            <button
              type="button"
              onClick={() => setRefreshKey((value) => value + 1)}
              aria-busy={loading}
              className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-surface px-3 py-1.5 text-[12.5px] font-medium text-foreground transition hover:bg-brand-soft"
            >
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.2"
                strokeLinecap="round"
                aria-hidden="true"
                className={loading ? "h-3.5 w-3.5 animate-spin" : "h-3.5 w-3.5"}
              >
                <path d="M20 11a8 8 0 1 0-2.3 5.7" />
                <path d="M20 5v6h-6" />
              </svg>
              Refresh
            </button>

            <span className="inline-flex items-center gap-2 rounded-full border border-border bg-surface px-2.5 py-1.5 text-xs text-muted">
              <span
                className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-[#0b6b45]"
                aria-hidden="true"
              />
              <b className="font-mono text-[11.5px] font-medium text-foreground">
                {username}
              </b>
              <button
                type="button"
                onClick={handleSignOut}
                className="border-b border-border text-muted transition hover:text-foreground"
              >
                Sign Out
              </button>
            </span>
          </div>
        </div>
      </div>

      <div className="mx-auto max-w-[1200px] px-5 sm:px-7">
        <div className="pb-5 pt-7">
          <h1 className="text-[25px] font-semibold leading-tight tracking-[-0.028em] text-foreground">
            Operations &amp; KPI
          </h1>
          <p className="mt-1.5 max-w-[66ch] text-[13.5px] text-muted">
            Funnel, cohort lift, assistant health and demand signals for the
            selected window. Confirmed (paid) orders only — session steps are not
            a strict sequence.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2.5 rounded-[10px] border border-[#ecd9b3] bg-[#fdf6e9] px-3.5 py-2.5 text-[12.5px] text-[#6b4a09]">
          <span className="shrink-0 rounded bg-[#6b4a09] px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-[0.09em] text-white">
            Demo data
          </span>
          <span className="sr-only">SYNTHETIC / DEMO DATA</span>
          <span>Synthetic seed data — not real customer revenue.</span>
          <span className="ml-auto tabular-nums">
            Generated {generatedAt(metadata.generated_at)}
          </span>
          <span>·</span>
          <span>
            env <code className="font-mono text-[11.5px]">{metadata.environment || "demo"}</code>
          </span>
          <details>
            <summary className="cursor-pointer list-none underline underline-offset-2">
              details
            </summary>
            <p className="mt-2 basis-full border-t border-[#ecd9b3] pt-2 text-[12px] leading-relaxed">
              {metadata.caveat ||
                "All metrics are derived from seeded/simulated demo data; not real customer revenue."}{" "}
              {cohorts.caveat || ""}
            </p>
          </details>
        </div>

        {loadError ? (
          <div className="mt-3 rounded-xl border border-red-300 bg-red-50 p-3.5 text-sm text-red-800">
            <p role="alert">{loadError}</p>
            <button
              type="button"
              onClick={() => setRefreshKey((value) => value + 1)}
              className="mt-2 font-semibold underline"
            >
              Try again
            </button>
          </div>
        ) : null}

        {loading && !kpi ? (
          <p className="mt-7 text-sm text-muted">Loading KPI data…</p>
        ) : null}

        {kpi ? (
          <>
            <section className="mt-7">
              <SectionHead
                title="Overview"
                hint={`Last ${sinceDays} days`}
              />
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <Tile
                  hero
                  label="Revenue"
                  value={money(revenue.total_revenue_idr)}
                  foot={`${num(revenue.order_count)} paid orders`}
                />
                <Tile
                  label="Sessions"
                  value={num(funnel.sessions)}
                  foot={`${num(funnel.view_product_sessions)} of ${num(
                    funnel.sessions
                  )} viewed a product (${pct(viewRate)})`}
                />
                <Tile
                  label="Avg. order value"
                  value={money(revenue.aov_idr)}
                  foot={`mean of ${num(revenue.order_count)} paid orders`}
                />
                <Tile
                  label="Session → order"
                  value={pct(overallConversion ?? orderRate)}
                  foot={
                    oneIn
                      ? `${num(funnel.order_sessions)} of ${num(
                          funnel.sessions
                        )} — roughly 1 in ${num(oneIn)}`
                      : `${num(funnel.order_sessions)} of ${num(
                          funnel.sessions
                        )} sessions`
                  }
                />
              </div>
            </section>

            <section className="mt-7">
              <SectionHead
                title="Conversion funnel"
                hint={
                  drop.lost > 0 ? (
                    <>
                      Biggest drop:{" "}
                      <b className="font-semibold text-muted">
                        {drop.label}, {num(drop.lost)} sessions
                      </b>
                    </>
                  ) : (
                    "No stage-to-stage drop in this window"
                  )
                }
              />
              <FunnelCard funnel={funnel} />
              <p className="mt-2 text-[11.5px] leading-relaxed text-muted">
                {num(funnel.order_sessions)} sessions ended in a paid order,
                while <b className="font-semibold text-muted">{num(revenue.order_count)} paid orders</b>{" "}
                were placed in this window — one session can account for more
                than one order. Bars and rates use session counts; revenue uses
                order counts.
              </p>
            </section>

            <section className="mt-7">
              <SectionHead
                title="Cohort lift & unit economics"
                hint="observational — not a randomized trial"
              />
              <div className="grid gap-3 lg:grid-cols-[1.35fr_1fr]">
                <CohortCard cohorts={cohorts} />
                <UnitEconomicsCard economics={economics} />
              </div>
            </section>

            <section className="mt-7">
              <SectionHead
                title="Assistant & demand"
                hint={`${num(quality.total_turns)} copilot turns · ${num(
                  search.total_searches
                )} searches`}
              />
              <div className="grid gap-3 lg:grid-cols-2">
                <AssistantCard quality={quality} />
                <SearchCard search={search} />
              </div>
            </section>

            <div className="mt-7 border-t border-border pt-3.5 text-[11.5px] leading-relaxed text-muted">
              <b className="font-semibold text-muted">Chart rules used here</b>
              <ul className="mt-1.5 list-disc space-y-0.5 pl-4">
                <li>
                  Bar length always encodes one thing, from a zero baseline, with
                  a labelled scale.
                </li>
                <li>
                  Counts and rates are never mixed in the same encoding — rates
                  are text columns.
                </li>
                <li>
                  Two values get a stat pair, not a chart; axes are never
                  truncated to flatter a result.
                </li>
                <li>
                  Colour encodes a category (reached / lost / outcome), never
                  decoration. Every bar is <code>aria-hidden</code> and
                  duplicated by a real number.
                </li>
                <li>
                  Residual buckets are labelled as residuals, never guessed: the
                  copilot remainder is <code>cart</code> + <code>error</code>,
                  and the two query lists overlap rather than partition.
                </li>
              </ul>
            </div>
          </>
        ) : null}
      </div>
    </main>
  );
}
