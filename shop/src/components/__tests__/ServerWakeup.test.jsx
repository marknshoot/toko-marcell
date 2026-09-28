import { render, screen, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import ServerWakeup from "../ServerWakeup";
import * as api from "../../lib/api";

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
    act(() => {
      vi.advanceTimersByTime(650);
    });

    expect(screen.getByText(/Membangunkan server\.\.\./i)).toBeTruthy();
    expect(screen.getByText(/Render free tier cold start/i)).toBeTruthy();
  });

  it("shows ready indicator when health check succeeds", async () => {
    let resolveHealth;
    const healthPromise = new Promise((resolve) => {
      resolveHealth = resolve;
    });

    vi.spyOn(api, "checkHealth").mockImplementation(() => healthPromise);

    render(<ServerWakeup />);

    // Advance to waking state
    act(() => {
      vi.advanceTimersByTime(650);
    });
    expect(screen.getByText(/Membangunkan server\.\.\./i)).toBeTruthy();

    // Resolve health check as true
    await act(async () => {
      resolveHealth(true);
      await healthPromise;
    });

    expect(screen.getByText(/Server siap & aktif!/i)).toBeTruthy();

    // After 2.5s, it hides cleanly
    act(() => {
      vi.advanceTimersByTime(2600);
    });

    expect(screen.queryByText(/Server siap/i)).toBeNull();
  });
});
