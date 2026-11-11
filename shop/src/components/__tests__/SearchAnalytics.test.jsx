import { render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ postEvent: vi.fn() }));

vi.mock("@/lib/api", () => api);

import SearchAnalytics from "../SearchAnalytics";

describe("SearchAnalytics", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("logs a query once, even when the same result set re-renders", () => {
    const { rerender } = render(
      <SearchAnalytics query="levis" resultsCount={12} />
    );

    expect(api.postEvent).toHaveBeenCalledTimes(1);
    expect(api.postEvent).toHaveBeenCalledWith({
      eventType: "search",
      query: "levis",
      resultsCount: 12,
    });

    rerender(<SearchAnalytics query="levis" resultsCount={12} />);
    expect(api.postEvent).toHaveBeenCalledTimes(1);

    rerender(<SearchAnalytics query="shoes" resultsCount={4} />);
    expect(api.postEvent).toHaveBeenCalledTimes(2);
  });

  it("does not log an empty query", () => {
    render(<SearchAnalytics query="   " resultsCount={0} />);

    expect(api.postEvent).not.toHaveBeenCalled();
  });
});
