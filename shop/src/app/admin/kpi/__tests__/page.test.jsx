import { fireEvent, render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AdminKpiPage from "../page";
import {
  adminLogin,
  adminLogout,
  getAdminKpi,
  getAdminSession,
} from "@/lib/api";

vi.mock("@/lib/api", () => ({
  adminLogin: vi.fn(),
  adminLogout: vi.fn(),
  adminRefresh: vi.fn(),
  getAdminKpi: vi.fn(),
  getAdminSession: vi.fn(() => null),
}));

const SESSION = {
  access_token: "jwt-token",
  token_type: "bearer",
  user: { username: "admin", role: "admin" },
};

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
  unit_economics: {
    total_cost_usd: 1.2345,
    avg_cost_per_turn_usd: 0.0125,
    cost_per_conversation_usd: 0.0588,
    cost_per_resolved_conversation_usd: 0.0686,
    cost_per_assisted_order_usd: 0.19,
  },
  search_health: {
    total_searches: 18,
    zero_result_searches: 2,
    zero_result_rate: 0.1111,
    search_to_pdp_rate: 0.5,
    top_zero_result_queries: [{ query: "sepatu lari pria", count: 2 }],
    top_queries: [
      { query: "jeans", count: 9 },
      { query: "sepatu lari pria", count: 2 },
    ],
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

async function signInWithDemoButton(findByRole) {
  fireEvent.click(
    await findByRole("button", { name: /Demo Admin \(1-Click\)/i })
  );
  await waitFor(() =>
    expect(adminLogin).toHaveBeenCalledWith({
      username: "admin",
      password: "marcell-admin-2026",
    })
  );
}

describe("AdminKpiPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.sessionStorage.clear();
    window.history.replaceState({}, "", "/");
    getAdminSession.mockReturnValue(null);
    adminLogin.mockResolvedValue(SESSION);
    adminLogout.mockResolvedValue({ ok: true });
    getAdminKpi.mockResolvedValue(fixture);
  });

  it("signs in with the demo admin and renders the KPI dashboard", async () => {
    const { findByRole, findByText, getAllByText, getByRole, getByText } = render(
      <AdminKpiPage />
    );

    expect(
      await findByRole("heading", { name: "Admin Dashboard Login" })
    ).toBeTruthy();
    expect(
      getByText("Executive KPI, operations telemetry, and unit economics.")
    ).toBeTruthy();

    await signInWithDemoButton(findByRole);

    await waitFor(() =>
      expect(getAdminKpi).toHaveBeenCalledWith("jwt-token", 30)
    );

    expect(await findByText("SYNTHETIC / DEMO DATA")).toBeTruthy();
    expect(getByText("Operations & KPI")).toBeTruthy();
    expect(getByText("Conversion funnel")).toBeTruthy();
    expect(getByText("Cohort lift & unit economics")).toBeTruthy();
    expect(getByText("Assistant & demand")).toBeTruthy();
    expect(getByText("Other / unlisted")).toBeTruthy();
    expect(getByText("sepatu lari pria")).toBeTruthy();
    expect(getAllByText("Within SLA")).toHaveLength(2);
    expect(getByText("admin")).toBeTruthy();
    expect(getByText("Sign Out")).toBeTruthy();
  });

  it("supports manual username/password login", async () => {
    const { findByText, getByLabelText, getByRole } = render(
      <AdminKpiPage />
    );

    await findByText("Admin Dashboard Login");
    fireEvent.change(getByLabelText("Username"), {
      target: { value: "admin" },
    });
    fireEvent.change(getByLabelText("Password"), {
      target: { value: "marcell-admin-2026" },
    });
    fireEvent.click(getByRole("button", { name: "Sign In" }));

    await waitFor(() =>
      expect(adminLogin).toHaveBeenCalledWith({
        username: "admin",
        password: "marcell-admin-2026",
      })
    );
    await waitFor(() =>
      expect(getAdminKpi).toHaveBeenCalledWith("jwt-token", 30)
    );
  });

  it("refetches when the time window changes and on Refresh", async () => {
    const { findByRole, getByRole } = render(<AdminKpiPage />);

    await signInWithDemoButton(findByRole);
    await waitFor(() =>
      expect(getAdminKpi).toHaveBeenCalledWith("jwt-token", 30)
    );

    fireEvent.click(getByRole("button", { name: "7d" }));
    await waitFor(() =>
      expect(getAdminKpi).toHaveBeenCalledWith("jwt-token", 7)
    );

    fireEvent.click(getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(getAdminKpi).toHaveBeenCalledTimes(3));
    expect(getAdminKpi).toHaveBeenLastCalledWith("jwt-token", 7);

    // The segmented control reflects the active window.
    expect(getByRole("button", { name: "7d" }).getAttribute("aria-pressed")).toBe(
      "true"
    );
    expect(
      getByRole("button", { name: "30d" }).getAttribute("aria-pressed")
    ).toBe("false");
  });

  it("signs out and returns to the login gate", async () => {
    const { findByRole, getByRole } = render(<AdminKpiPage />);

    await signInWithDemoButton(findByRole);
    await waitFor(() =>
      expect(getAdminKpi).toHaveBeenCalledWith("jwt-token", 30)
    );

    fireEvent.click(getByRole("button", { name: "Sign Out" }));

    await waitFor(() => expect(adminLogout).toHaveBeenCalledTimes(1));
    expect(
      await findByRole("heading", { name: "Admin Dashboard Login" })
    ).toBeTruthy();
  });

  it("returns to the login gate when the admin API reports 401", async () => {
    getAdminKpi.mockRejectedValue(
      Object.assign(new Error("Token expired"), { status: 401 })
    );
    const { findByRole, getByRole } = render(<AdminKpiPage />);

    await signInWithDemoButton(findByRole);

    const alert = await findByRole("alert");
    expect(alert.textContent).toContain("session");
    expect(
      await findByRole("heading", { name: "Admin Dashboard Login" })
    ).toBeTruthy();
  });

  it("loads an access token saved for the tab", async () => {
    getAdminSession.mockReturnValue({
      access_token: "stored-jwt",
      user: { username: "admin", role: "admin" },
    });
    const { findByText, queryByRole } = render(<AdminKpiPage />);

    await waitFor(() =>
      expect(getAdminKpi).toHaveBeenCalledWith("stored-jwt", 30)
    );
    expect(await findByText("SYNTHETIC / DEMO DATA")).toBeTruthy();
    expect(
      queryByRole("heading", { name: "Admin Dashboard Login" })
    ).toBeNull();
  });

  it("accepts a legacy query token and removes it from the URL", async () => {
    window.history.replaceState({}, "", "/admin/kpi?token=query-token");
    const { findByText } = render(<AdminKpiPage />);

    await waitFor(() =>
      expect(getAdminKpi).toHaveBeenCalledWith("query-token", 30)
    );
    expect(window.location.search).toBe("");
    expect(await findByText("SYNTHETIC / DEMO DATA")).toBeTruthy();
  });
});
