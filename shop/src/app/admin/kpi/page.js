"use client";

import { useEffect, useState } from "react";
import { getAdminKpi } from "@/lib/api";
import { formatRp } from "@/lib/formatRp";

const STORAGE_KEY = "toko_admin_token";
const WINDOWS = [7, 30, 90];

function pct(value) {
  return value == null ? "—" : `${(value * 100).toFixed(1)}%`;
}

function num(value) {
  return value == null ? "—" : value.toLocaleString("id-ID");
}

function ms(value) {
  return value == null ? "—" : `${value.toFixed(2)} ms`;
}

function money(value) {
  return value == null ? "—" : formatRp(value);
}

function Stat({ label, value, hint }) {
  return (
    <div className="rounded-xl border border-border bg-background px-4 py-3">
      <p className="text-xs font-medium uppercase tracking-wide text-muted">{label}</p>
      <p className="mt-1 text-xl font-semibold tracking-tight text-foreground">{value}</p>
      {hint ? <p className="mt-1 text-xs text-muted">{hint}</p> : null}
    </div>
  );
}

function Card({ title, subtitle, children }) {
  return (
    <section className="rounded-2xl border border-border bg-surface p-5 shadow-sm">
      <h2 className="text-lg font-semibold tracking-tight text-foreground">{title}</h2>
      {subtitle ? <p className="mt-1 text-sm text-muted">{subtitle}</p> : null}
      <div className="mt-4">{children}</div>
    </section>
  );
}

function TokenGate({ tokenInput, setTokenInput, onSubmit, error, disabled }) {
  return (
    <main className="py-16">
      <div className="mx-auto max-w-lg px-7">
        <div className="rounded-2xl border border-border bg-surface p-7 shadow-sm">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-muted">
            Toko Marcell
          </p>
          <h1 className="mt-2 text-2xl font-semibold tracking-tight text-foreground">
            Operations &amp; KPI Dashboard
          </h1>
          <p className="mt-2 text-sm text-muted">
            This view is gated by the deployment&apos;s <code>ADMIN_TOKEN</code>. It reads
            seeded demo data only.
          </p>

          <form onSubmit={onSubmit} className="mt-6 space-y-3">
            <label
              htmlFor="admin-token"
              className="block text-sm font-medium text-foreground"
            >
              Admin token
            </label>
            <input
              id="admin-token"
              type="password"
              autoComplete="current-password"
              value={tokenInput}
              onChange={(event) => setTokenInput(event.target.value)}
              placeholder="Paste ADMIN_TOKEN"
              className="w-full rounded-lg border border-border bg-background px-3 py-2.5 text-sm text-foreground outline-none transition focus:border-foreground"
            />
            {error ? (
              <p role="alert" className="text-sm text-red-700">
                {error}
              </p>
            ) : null}
            <button
              type="submit"
              disabled={disabled}
              className="w-full rounded-full bg-cta px-5 py-2.5 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
            >
              {disabled ? "Checking…" : "Open dashboard"}
            </button>
          </form>

          <p className="mt-4 text-xs text-muted">
            The token is kept in this tab&apos;s <code>sessionStorage</code> only. You can also
            open this page with <code>?token=…</code>.
          </p>
        </div>
      </div>
    </main>
  );
}

export default function AdminKpiPage() {
  const [token, setToken] = useState(null);
  const [tokenInput, setTokenInput] = useState("");
  const [sinceDays, setSinceDays] = useState(30);
  const [kpi, setKpi] = useState(null);
  const [loading, setLoading] = useState(false);
  const [authError, setAuthError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const queryToken = params.get("token");
    const storedToken = window.sessionStorage.getItem(STORAGE_KEY);
    const nextToken = queryToken || storedToken;

    if (queryToken) {
      window.sessionStorage.setItem(STORAGE_KEY, queryToken);
      params.delete("token");
      const query = params.toString();
      window.history.replaceState(
        {},
        "",
        `${window.location.pathname}${query ? `?${query}` : ""}`
      );
    }

    if (nextToken) {
      queueMicrotask(() => setToken(nextToken));
    }
  }, []);

  useEffect(() => {
    if (!token) {
      return undefined;
    }

    let cancelled = false;

    async function load() {
      setLoading(true);
      setLoadError("");
      try {
        const data = await getAdminKpi(token, sinceDays);
        if (!cancelled) {
          setKpi(data);
          setAuthError("");
        }
      } catch (error) {
        if (cancelled) {
          return;
        }
        if (error.status === 403) {
          window.sessionStorage.removeItem(STORAGE_KEY);
          setKpi(null);
          setToken(null);
          setAuthError("That admin token was rejected. Paste a valid ADMIN_TOKEN.");
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
  }, [token, sinceDays, refreshKey]);

  function handleTokenSubmit(event) {
    event.preventDefault();
    const nextToken = tokenInput.trim();
    if (!nextToken) {
      setAuthError("Enter the admin token to continue.");
      return;
    }
    window.sessionStorage.setItem(STORAGE_KEY, nextToken);
    setAuthError("");
    setLoadError("");
    setToken(nextToken);
  }

  function handleSignOut() {
    window.sessionStorage.removeItem(STORAGE_KEY);
    setToken(null);
    setKpi(null);
    setTokenInput("");
    setAuthError("");
    setLoadError("");
  }

  if (!token) {
    return (
      <TokenGate
        tokenInput={tokenInput}
        setTokenInput={setTokenInput}
        onSubmit={handleTokenSubmit}
        error={authError}
        disabled={loading}
      />
    );
  }

  const metadata = kpi?.metadata;
  const revenue = kpi?.revenue;
  const funnel = kpi?.funnel;
  const cohorts = kpi?.cohorts;
  const search = kpi?.search_health;
  const quality = kpi?.copilot_quality;

  return (
    <main className="py-10">
      <div className="mx-auto max-w-6xl px-5 sm:px-7">
        <header className="flex flex-col gap-5 border-b border-border pb-6 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-muted">
              Admin Toko Marcell
            </p>
            <h1 className="mt-2 text-3xl font-semibold tracking-tight text-foreground">
              Toko Marcell — Operations &amp; KPI Dashboard
            </h1>
            <p className="mt-2 max-w-3xl text-sm text-muted">
              Funnel, cohort, copilot-quality and search-demand signals for the selected
              window.
            </p>
          </div>

          <div className="flex flex-wrap items-end gap-3">
            <label className="text-xs font-medium text-muted">
              <span className="mb-1 block">Time window</span>
              <select
                aria-label="Time window"
                value={sinceDays}
                onChange={(event) => setSinceDays(Number(event.target.value))}
                className="rounded-lg border border-border bg-surface px-3 py-2 text-sm text-foreground outline-none focus:border-foreground"
              >
                {WINDOWS.map((days) => (
                  <option key={days} value={days}>
                    Last {days} days
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              onClick={() => setRefreshKey((value) => value + 1)}
              className="rounded-full border border-border bg-surface px-4 py-2 text-sm font-medium text-foreground hover:bg-brand-soft"
            >
              Refresh
            </button>
            <button
              type="button"
              onClick={handleSignOut}
              className="rounded-full px-3 py-2 text-sm font-medium text-muted underline hover:text-foreground"
            >
              Change token
            </button>
          </div>
        </header>

        {metadata ? (
          <div className="mt-5 flex flex-col gap-3 rounded-2xl border border-amber-300 bg-amber-50 p-4 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <p className="text-sm font-bold uppercase tracking-[0.14em] text-amber-900">
                SYNTHETIC / DEMO DATA
              </p>
              <p className="mt-1 text-sm text-amber-900">{metadata.caveat}</p>
            </div>
            <div className="shrink-0 text-xs text-amber-900">
              <p>Generated {new Date(metadata.generated_at).toLocaleString()}</p>
              <p className="mt-1">Environment: {metadata.environment}</p>
            </div>
          </div>
        ) : null}

        {loadError ? (
          <div className="mt-5 rounded-2xl border border-red-300 bg-red-50 p-4 text-sm text-red-800">
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
          <p className="mt-8 text-sm text-muted">Loading KPI data…</p>
        ) : null}

        {kpi ? (
          <div className="mt-6 grid gap-5 lg:grid-cols-2">
            <Card
              title="Executive Revenue & Funnel"
              subtitle="Confirmed orders only; session steps are not a strict sequence."
            >
              <div className="grid gap-3 sm:grid-cols-3">
                <Stat label="Orders" value={num(revenue.order_count)} />
                <Stat
                  label="Total Revenue"
                  value={money(revenue.total_revenue_idr)}
                  hint="IDR, synthetic"
                />
                <Stat label="AOV" value={money(revenue.aov_idr)} hint="IDR per paid order" />
              </div>

              <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
                <Stat label="View → Cart" value={pct(funnel.rates.view_to_cart)} />
                <Stat label="Cart → Checkout" value={pct(funnel.rates.cart_to_checkout)} />
                <Stat label="Checkout → Order" value={pct(funnel.rates.checkout_to_order)} />
                <Stat label="Overall conversion" value={pct(funnel.rates.overall_conversion)} />
              </div>

              <p className="mt-4 text-xs text-muted">
                {num(funnel.sessions)} sessions · {num(funnel.view_product_sessions)} viewed ·{" "}
                {num(funnel.add_to_cart_sessions)} added · {num(funnel.checkout_start_sessions)}{" "}
                checked out · {num(funnel.order_sessions)} ordered
              </p>
            </Card>

            <Card
              title="Cohort Analysis"
              subtitle="Copilot sessions versus the non-copilot baseline."
            >
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead className="text-xs uppercase tracking-wide text-muted">
                    <tr>
                      <th className="pb-2 font-medium">Cohort</th>
                      <th className="pb-2 text-right font-medium">Sessions</th>
                      <th className="pb-2 text-right font-medium">Add to cart</th>
                      <th className="pb-2 text-right font-medium">Conversion</th>
                      <th className="pb-2 text-right font-medium">AOV</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    <tr>
                      <td className="py-3 font-medium text-foreground">Copilot</td>
                      <td className="py-3 text-right">{num(cohorts.copilot_sessions.sessions)}</td>
                      <td className="py-3 text-right">
                        {pct(cohorts.copilot_sessions.add_to_cart_rate)}
                      </td>
                      <td className="py-3 text-right">
                        {pct(cohorts.copilot_sessions.conversion_rate)}
                      </td>
                      <td className="py-3 text-right">
                        {money(cohorts.copilot_sessions.aov_idr)}
                      </td>
                    </tr>
                    <tr>
                      <td className="py-3 font-medium text-foreground">
                        Baseline (non-copilot)
                      </td>
                      <td className="py-3 text-right">
                        {num(cohorts.non_copilot_sessions.sessions)}
                      </td>
                      <td className="py-3 text-right">
                        {pct(cohorts.non_copilot_sessions.add_to_cart_rate)}
                      </td>
                      <td className="py-3 text-right">
                        {pct(cohorts.non_copilot_sessions.conversion_rate)}
                      </td>
                      <td className="py-3 text-right">
                        {money(cohorts.non_copilot_sessions.aov_idr)}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <p className="mt-4 text-xs text-muted">{cohorts.caveat}</p>
            </Card>

            <Card
              title="Copilot Performance & Safety"
              subtitle="Turn outcomes, latency, resolution and explicit sentiment."
            >
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <Stat label="Total turns" value={num(quality.total_turns)} />
                <Stat label="Answered" value={num(quality.answered_turns)} />
                <Stat label="Guardrail" value={num(quality.guardrail_turns)} />
                <Stat label="Unanswered" value={num(quality.unanswered_turns)} />
              </div>
              <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
                <Stat label="Unanswered rate" value={pct(quality.unanswered_rate)} />
                <Stat label="Deflection" value={pct(quality.deflection_rate)} />
                <Stat label="Latency p50" value={ms(quality.latency_p50_ms)} />
                <Stat label="Latency p95" value={ms(quality.latency_p95_ms)} />
              </div>
              <p className="mt-4 text-xs text-muted">
                Thumbs up {num(quality.thumbs_up)} · thumbs down {num(quality.thumbs_down)} ·{" "}
                {pct(quality.thumbs_up_rate)} positive. Deflection is final-turn
                answered ÷ (answered + unanswered); it is not human-agent deflection.
              </p>
            </Card>

            <Card
              title="Search Health & Demand Signals"
              subtitle="Client-posted search events; zero-result queries are unmet demand."
            >
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <Stat label="Searches" value={num(search.total_searches)} />
                <Stat label="Zero results" value={num(search.zero_result_searches)} />
                <Stat label="Zero-result rate" value={pct(search.zero_result_rate)} />
                <Stat label="Search → PDP" value={pct(search.search_to_pdp_rate)} />
              </div>

              <h3 className="mt-5 text-sm font-semibold text-foreground">
                Top missed searches
              </h3>
              {search.top_zero_result_queries.length ? (
                <ul className="mt-2 space-y-1.5">
                  {search.top_zero_result_queries.map((row) => (
                    <li
                      key={row.query}
                      className="flex items-center justify-between gap-3 rounded-lg bg-background px-3 py-2 text-sm"
                    >
                      <span className="truncate text-foreground">{row.query}</span>
                      <span className="shrink-0 font-mono text-xs text-muted">{row.count}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-2 text-sm text-muted">
                  No zero-result searches in this window.
                </p>
              )}
            </Card>
          </div>
        ) : null}
      </div>
    </main>
  );
}
