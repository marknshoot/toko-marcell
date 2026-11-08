import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import Loading from "../loading";

describe("product route loading", () => {
  it("renders the product skeleton while the page resolves", () => {
    render(<Loading />);

    expect(screen.getByRole("status").textContent).toMatch(/Loading product/);
    expect(document.querySelector("main[aria-busy='true']")).not.toBeNull();
  });
});
