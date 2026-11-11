import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ getCategories: vi.fn() }));

vi.mock("@/lib/api", () => api);
vi.mock("next/link", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, useLinkStatus: () => ({ pending: false }) };
});

import CatalogFilters from "../CatalogFilters";

describe("CatalogFilters", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders department pills that preserve the query", async () => {
    api.getCategories.mockResolvedValue({
      departments: [
        { name: "Women", count: 10 },
        { name: "Men", count: 5 },
      ],
    });

    render(await CatalogFilters({ department: "Women", q: "levis" }));

    expect(api.getCategories).toHaveBeenCalledTimes(1);

    const all = screen.getByRole("link", { name: "All" });
    expect(all.getAttribute("href")).toBe("/?q=levis");
    expect(all.getAttribute("aria-current")).toBeNull();

    const women = screen.getByRole("link", { name: /Women/ });
    expect(women.getAttribute("href")).toBe("/?department=Women&q=levis");
    expect(women.getAttribute("aria-current")).toBe("page");

    const men = screen.getByRole("link", { name: /Men/ });
    expect(men.getAttribute("href")).toBe("/?department=Men&q=levis");
  });

  it("falls back to the All pill when the facet fetch fails", async () => {
    api.getCategories.mockRejectedValue(new Error("offline"));

    render(await CatalogFilters({ department: "All", q: "" }));

    expect(screen.getByRole("link", { name: "All" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: /Women/ })).toBeNull();
  });
});
