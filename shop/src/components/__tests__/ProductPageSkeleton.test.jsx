import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import ProductPageSkeleton from "../ProductPageSkeleton";

describe("ProductPageSkeleton", () => {
  it("announces a single loading status", () => {
    const { getAllByRole } = render(<ProductPageSkeleton />);

    const status = getAllByRole("status");
    expect(status).toHaveLength(1);
    expect(status[0].textContent).toMatch(/Loading product/);
  });

  it("reserves the product, reviews and recommendation layouts", () => {
    const { container } = render(<ProductPageSkeleton />);

    expect(container.querySelector("main[aria-busy='true']")).not.toBeNull();
    // Hero image block plus the four cards of the recommendation rail.
    expect(container.querySelectorAll(".aspect-square").length).toBe(5);
    expect(container.querySelectorAll("[aria-busy='true']").length).toBe(3);
  });

  it("can hide the sections that are not part of the fallback", () => {
    const { container } = render(
      <ProductPageSkeleton reviews={false} recs={false} />
    );

    expect(container.querySelectorAll(".aspect-square").length).toBe(1);
    expect(container.querySelectorAll("[aria-busy='true']").length).toBe(1);
  });
});
