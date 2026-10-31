import { render, screen, act, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import ServerWakeup from "../ServerWakeup";
import * as api from "../../lib/api";

const WAKE_INDICATOR_DELAY_MS = 600;
const POLL_INTERVAL_MS = 2500;
const HIDE_DELAY_MS = 2500;
const MAX_WAIT_MS = 65000;

const HEALTHY = {
  ok: true,
  api: true,
  database: true,
  data: { status: "ok", services: { api: "ok", database: "ok" } },
};

const DATABASE_WAKING = {
  ok: false,
  api: true,
  database: false,
  data: {
    status: "waking",
    services: { api: "ok", database: "waking" },
    error: "Database is waking up or unreachable",
  },
};

describe("ServerWakeup", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("shows waking indicator with circular spinner when server takes time to respond", async () => {
    // Keep health check pending
    vi.spyOn(api, "checkHealth").mockImplementation(
      () => new Promise(() => {})
    );

    render(<ServerWakeup />);

    // Initially idle
    expect(screen.queryByText(/Membangunkan server/i)).toBeNull();

    // After 600ms, the cold start indicator appears
    await act(async () => {
      await vi.advanceTimersByTimeAsync(WAKE_INDICATOR_DELAY_MS + 50);
    });

    expect(screen.getByText(/Membangunkan server & database\.\.\./i)).toBeTruthy();
    expect(
      screen.getByText(/Render API & Neon Postgres cold start/i)
    ).toBeTruthy();
  });

  it("shows ready indicator when health check succeeds", async () => {
    let resolveHealth;
    const healthPromise = new Promise((resolve) => {
      resolveHealth = resolve;
    });

    vi.spyOn(api, "checkHealth").mockImplementation(() => healthPromise);

    render(<ServerWakeup />);

    // Advance to waking state
    await act(async () => {
      await vi.advanceTimersByTimeAsync(WAKE_INDICATOR_DELAY_MS + 50);
    });
    expect(screen.getByText(/Membangunkan server & database\.\.\./i)).toBeTruthy();

    // Resolve health check as true (legacy boolean contract)
    await act(async () => {
      resolveHealth(true);
      await healthPromise;
    });

    expect(screen.getByText(/Server & Database siap & aktif!/i)).toBeTruthy();
    expect(
      screen.getByText(/Katalog & AI Copilot siap digunakan\./i)
    ).toBeTruthy();

    // After 2.5s, it hides cleanly
    await act(async () => {
      await vi.advanceTimersByTimeAsync(HIDE_DELAY_MS + 100);
    });

    expect(screen.queryByText(/Server & Database siap/i)).toBeNull();
  });

  it("understands the dual Render/Neon object result from checkHealth", async () => {
    vi.spyOn(api, "checkHealth").mockResolvedValue(HEALTHY);

    render(<ServerWakeup />);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(WAKE_INDICATOR_DELAY_MS + 50);
    });

    // A sub-600ms answer means no cold start: the overlay never flashes.
    expect(screen.queryByRole("status")).toBeNull();
    expect(api.checkHealth).toHaveBeenCalledTimes(1);
  });

  it("polls every 2.5s and becomes ready once the database wakes up", async () => {
    const health = vi
      .spyOn(api, "checkHealth")
      .mockResolvedValueOnce(DATABASE_WAKING)
      .mockResolvedValueOnce(DATABASE_WAKING)
      .mockResolvedValue(HEALTHY);

    render(<ServerWakeup />);

    // First probe fails immediately (API up, Neon waking).
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    expect(health).toHaveBeenCalledTimes(1);

    // Overlay shows after the 600ms delay; no extra poll before the interval.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(WAKE_INDICATOR_DELAY_MS);
    });
    expect(screen.getByText(/Membangunkan server & database\.\.\./i)).toBeTruthy();
    expect(health).toHaveBeenCalledTimes(1);

    // Next poll happens after the poll interval, not on every tick.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    });
    expect(health).toHaveBeenCalledTimes(2);
    expect(screen.getByText(/Membangunkan server & database\.\.\./i)).toBeTruthy();

    // Third probe succeeds: database is up.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    });
    expect(health).toHaveBeenCalledTimes(3);
    expect(screen.getByText(/Server & Database siap & aktif!/i)).toBeTruthy();

    // Polling stops once ready.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(HIDE_DELAY_MS + 100);
    });
    expect(health).toHaveBeenCalledTimes(3);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("falls back to the error state when neither service answers in time", async () => {
    const health = vi.spyOn(api, "checkHealth").mockResolvedValue(DATABASE_WAKING);

    render(<ServerWakeup />);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(MAX_WAIT_MS + 1000);
    });

    expect(screen.getByText(/Server atau database sedang bangun\.\.\./i)).toBeTruthy();
    expect(
      screen.getByText(
        /Render atau Neon Serverless Postgres membutuhkan waktu lebih lama untuk menyala\./i
      )
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: /Coba lagi/i })).toBeTruthy();
    expect(health.mock.calls.length).toBeGreaterThan(1);
  });

  it("restarts polling from the error state when 'Coba lagi' is clicked", async () => {
    const health = vi
      .spyOn(api, "checkHealth")
      .mockResolvedValue(DATABASE_WAKING);

    render(<ServerWakeup />);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(MAX_WAIT_MS + 1000);
    });
    expect(screen.getByText(/Server atau database sedang bangun\.\.\./i)).toBeTruthy();

    // Retry: the waking text appears immediately (no second 600ms delay).
    let resolveRetry;
    health.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveRetry = resolve;
        })
    );

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Coba lagi/i }));
    });

    expect(screen.getByText(/Membangunkan server & database\.\.\./i)).toBeTruthy();
    expect(screen.queryByText(/Server atau database sedang bangun/i)).toBeNull();

    await act(async () => {
      resolveRetry(HEALTHY);
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(screen.getByText(/Server & Database siap & aktif!/i)).toBeTruthy();
  });
});
