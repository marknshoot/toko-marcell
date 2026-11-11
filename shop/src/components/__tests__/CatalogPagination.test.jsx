import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/link", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, useLinkStatus: () => ({ pending: false }) };
});

import CatalogPagination from "../CatalogPagination";

const BASE = { department: "All", q: "", total: 48, totalPages: 2 };

describe("CatalogPagination", () => {
  it("links forward and disables Prev on the first page", () => {
    render(<CatalogPagination {...BASE} page={1} />);

    expect(screen.queryByRole("link", { name: /Prev/ })).toBeNull();
    expect(screen.getByText("Prev").getAttribute("aria-disabled")).toBe("true");
    expect(screen.getByRole("link", { name: /Next/ }).getAttribute("href")).toBe(
      "/?page=2"
    );
    expect(screen.getByText(/Page 1 of 2 · 48 items/)).toBeTruthy();
  });

  it("links back to the bare route and disables Next on the last page", () => {
    render(<CatalogPagination {...BASE} page={2} />);

    expect(screen.getByRole("link", { name: /Prev/ }).getAttribute("href")).toBe(
      "/"
    );
    expect(screen.queryByRole("link", { name: /Next/ })).toBeNull();
    expect(screen.getByText("Next").getAttribute("aria-disabled")).toBe("true");
  });

  it("keeps the department and query in pagination links", () => {
    render(
      <CatalogPagination
        department="Women"
        q="levis"
        page={1}
        totalPages={3}
        total={60}
      />
    );

    expect(screen.getByRole("link", { name: /Next/ }).getAttribute("href")).toBe(
      "/?department=Women&page=2&q=levis"
    );
  });

  it("renders nothing when there are no results", () => {
    const { container } = render(
      <CatalogPagination
        department="All"
        q="zzz"
        page={1}
        totalPages={1}
        total={0}
      />
    );

    expect(container.firstChild).toBeNull();
  });
});
