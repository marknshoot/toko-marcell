import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import CatalogFiltersSkeleton from "../CatalogFiltersSkeleton";
import CatalogResultsSkeleton from "../CatalogResultsSkeleton";

describe("catalog skeletons", () => {
  it("announces one loading status and reserves 24 cards", () => {
    const { container } = render(<CatalogResultsSkeleton />);

    expect(screen.getByRole("status").textContent).toMatch(/Loading products/);
    expect(container.querySelectorAll(".aspect-square")).toHaveLength(24);
    expect(
      container.querySelector(".grid")?.className
    ).toContain("sm:grid-cols-4");
  });

  it("renders an aria-hidden pill row for the facets", () => {
    const { container } = render(<CatalogFiltersSkeleton />);

    expect(container.firstChild?.getAttribute("aria-hidden")).toBe("true");
    expect(container.querySelectorAll(".rounded-full")).toHaveLength(6);
  });
});
