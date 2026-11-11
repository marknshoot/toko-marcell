import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  getProducts: vi.fn(),
  searchProducts: vi.fn(),
  postEvent: vi.fn(),
}));
const navigation = vi.hoisted(() => ({ redirect: vi.fn() }));

vi.mock("@/lib/api", () => api);
vi.mock("next/navigation", () => ({
  redirect: navigation.redirect,
  useRouter: () => ({ refresh: vi.fn() }),
}));
vi.mock("next/link", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, useLinkStatus: () => ({ pending: false }) };
});

import CatalogResults from "../CatalogResults";

const PRODUCT = {
  id: 42,
  title: "Running Shoes",
  priceIdr: 750000,
  category: "Shoes",
  brand: "Adidas",
  imageUrl: null,
};

describe("CatalogResults", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("fetches the browse page and renders the count and cards", async () => {
    api.getProducts.mockResolvedValue({ items: [PRODUCT], total: 4670 });

    render(await CatalogResults({ department: "All", page: 1, q: "" }));

    expect(api.getProducts).toHaveBeenCalledWith({
      department: "All",
      limit: 24,
      offset: 0,
    });
    expect(api.searchProducts).not.toHaveBeenCalled();
    expect(screen.getByText("Running Shoes")).toBeTruthy();
    expect(screen.getByText(/4,670 products/)).toBeTruthy();
  });

  it("uses the search endpoint and reports the full result count", async () => {
    api.searchProducts.mockResolvedValue({ items: [PRODUCT], total: 1 });

    render(
      await CatalogResults({ department: "Women", page: 2, q: "levis" })
    );

    expect(api.searchProducts).toHaveBeenCalledWith({
      q: "levis",
      department: "Women",
      limit: 24,
      offset: 24,
    });
    expect(screen.getByText(/1 result for “levis”/)).toBeTruthy();
    await waitFor(() =>
      expect(api.postEvent).toHaveBeenCalledWith({
        eventType: "search",
        query: "levis",
        resultsCount: 1,
      })
    );
  });

  it("renders the empty state for a browse page", async () => {
    api.getProducts.mockResolvedValue({ items: [], total: 0 });

    render(await CatalogResults({ department: "All", page: 1, q: "" }));

    expect(screen.getByText("No products found.")).toBeTruthy();
  });

  it("renders the empty state for a search", async () => {
    api.searchProducts.mockResolvedValue({ items: [], total: 0 });

    render(await CatalogResults({ department: "All", page: 1, q: "zzz" }));

    expect(screen.getByText("Nothing matched “zzz”.")).toBeTruthy();
  });

  it("renders a retry affordance when the backend fails", async () => {
    api.getProducts.mockRejectedValue(new Error("timeout"));

    render(await CatalogResults({ department: "All", page: 1, q: "" }));

    expect(screen.getByText("Could not load products.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
  });

  it("redirects an out-of-range page to the last real page", async () => {
    api.searchProducts.mockResolvedValue({ items: [], total: 30 });

    await CatalogResults({ department: "Women", page: 5, q: "levis" });

    expect(navigation.redirect).toHaveBeenCalledWith(
      "/?department=Women&page=2&q=levis"
    );
  });
});
