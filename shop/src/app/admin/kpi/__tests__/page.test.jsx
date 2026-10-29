import { fireEvent, render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AdminKpiPage from "../page";
import { getAdminKpi } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  getAdminKpi: vi.fn(),
}));

const fixture = {
  metadata: {
    since_days: 30,
    generated_at: "2026-01-01T00:00:00+00:00",
    synthetic: true,
    environment: "demo",
    caveat:
      "All metrics are derived from seeded/simulated demo data; not real customer revenue.",
  },
  revenue: {
    order_count: 42,
    total_revenue_idr: 46007100,
    aov_idr: 1095407.14,
  },
  funnel: {
    sessions: 120,
    view_product_sessions: 90,
    add_to_cart_sessions: 30,
    checkout_start_sessions: 12,
    order_sessions: 8,
    rates: {
      view_to_cart: 0.3333,
      cart_to_checkout: 0.4,
      checkout_to_order: 0.6667,
      overall_conversion: 0.0667,
    },
  },
  cohorts: {
    copilot_sessions: {
      sessions: 45,
      add_to_cart_rate: 0.3333,
      conversion_rate: 0.1333,
      aov_idr: 1095407.14,
    },
    non_copilot_sessions: {
      sessions: 75,
      add_to_cart_rate: 0.2,
      conversion_rate: 0.04,
      aov_idr: 850000,
    },
    caveat:
      "Observational comparison; copilot usage is self-selected, not a randomized trial.",
  },
  search_health: {
    total_searches: 18,
    zero_result_searches: 2,
    zero_result_rate: 0.1111,
    search_to_pdp_rate: 0.5,
    top_zero_result_queries: [{ query: "sepatu lari pria", count: 2 }],
    top_queries: [{ query: "jeans", count: 9 }],
  },
  copilot_quality: {
    total_turns: 99,
    answered_turns: 42,
    guardrail_turns: 7,
    unanswered_turns: 3,
    unanswered_rate: 0.0303,
    deflection_eligible_conversations: 21,
    deflection_answered_conversations: 18,
    deflection_rate: 0.8571,
    latency_p50_ms: 820.5,
    latency_p95_ms: 2100.25,
    thumbs_up: 9,
    thumbs_down: 3,
    thumbs_up_rate: 0.75,
  },
};

describe("AdminKpiPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.sessionStorage.clear();
    window.history.replaceState({}, "", "/");
    getAdminKpi.mockResolvedValue(fixture);
  });

  it("submits a token and renders the KPI cards", async () => {
    const { findByText, getByLabelText, getByRole, getByText } = render(
      <AdminKpiPage />
    );

    fireEvent.change(getByLabelText("Admin token"), {
      target: { value: "secret-token" },
    });
    fireEvent.click(getByRole("button", { name: "Open dashboard" }));

    await waitFor(() =>
      expect(getAdminKpi).toHaveBeenCalledWith("secret-token", 30)
    );

    expect(await findByText("SYNTHETIC / DEMO DATA")).toBeTruthy();
    expect(
      getByText("Toko Marcell — Operations & KPI Dashboard")
    ).toBeTruthy();
    expect(getByText("Executive Revenue & Funnel")).toBeTruthy();
    expect(getByText("Cohort Analysis")).toBeTruthy();
    expect(getByText("Copilot Performance & Safety")).toBeTruthy();
    expect(getByText("Search Health & Demand Signals")).toBeTruthy();
    expect(getByText("sepatu lari pria")).toBeTruthy();
  });

  it("refetches when the time window changes", async () => {
    const { getByLabelText, getByRole } = render(<AdminKpiPage />);

    fireEvent.change(getByLabelText("Admin token"), {
      target: { value: "secret-token" },
    });
    fireEvent.click(getByRole("button", { name: "Open dashboard" }));
    await waitFor(() =>
      expect(getAdminKpi).toHaveBeenCalledWith("secret-token", 30)
    );

    fireEvent.change(getByLabelText("Time window"), {
      target: { value: "7" },
    });

    await waitFor(() =>
      expect(getAdminKpi).toHaveBeenCalledWith("secret-token", 7)
    );
  });

  it("shows the token form again after a 403", async () => {
    getAdminKpi.mockRejectedValue(
      Object.assign(new Error("Invalid admin token."), { status: 403 })
    );
    const { findByRole, getByLabelText, getByRole } = render(<AdminKpiPage />);

    fireEvent.change(getByLabelText("Admin token"), {
      target: { value: "wrong-token" },
    });
    fireEvent.click(getByRole("button", { name: "Open dashboard" }));

    const alert = await findByRole("alert");
    expect(alert.textContent).toContain("rejected");
    expect(getByLabelText("Admin token")).toBeTruthy();
  });

  it("loads a token saved in sessionStorage", async () => {
    window.sessionStorage.setItem("toko_admin_token", "saved-token");
    const { findByText } = render(<AdminKpiPage />);

    await waitFor(() =>
      expect(getAdminKpi).toHaveBeenCalledWith("saved-token", 30)
    );
    expect(await findByText("SYNTHETIC / DEMO DATA")).toBeTruthy();
  });

  it("accepts a token from the query string and removes it from the URL", async () => {
    window.history.replaceState({}, "", "/admin/kpi?token=query-token");
    const { findByText } = render(<AdminKpiPage />);

    await waitFor(() =>
      expect(getAdminKpi).toHaveBeenCalledWith("query-token", 30)
    );
    expect(window.location.search).toBe("");
    expect(await findByText("SYNTHETIC / DEMO DATA")).toBeTruthy();
  });
});
