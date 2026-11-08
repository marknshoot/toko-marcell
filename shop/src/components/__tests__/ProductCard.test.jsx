import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// useLinkStatus() reports the navigation owned by the enclosing <Link>. Mock only
// that hook so the card can be tested in both states without a router.
const linkState = vi.hoisted(() => ({ pending: false }));

vi.mock("next/link", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, useLinkStatus: () => ({ pending: linkState.pending }) };
});

import ProductCard from "../ProductCard";

const PRODUCT = {
  id: 42,
  title: "Running Shoes",
  priceIdr: 750000,
  category: "Shoes",
  brand: "Adidas",
  imageUrl: "https://example.com/shoes.jpg",
};

describe("ProductCard", () => {
  beforeEach(() => {
    linkState.pending = false;
    window.localStorage.clear();
  });

  it("links the card to the product page and renders its fields", () => {
    const { container } = render(<ProductCard {...PRODUCT} />);

    expect(container.querySelector("a").getAttribute("href")).toBe("/product/42");
    expect(screen.getByText("Running Shoes")).toBeTruthy();
    expect(screen.getByText("Rp 750.000")).toBeTruthy();
    expect(screen.getByText("Adidas · Shoes")).toBeTruthy();
  });

  it("shows a pending indicator only while the navigation is in flight", () => {
    const { container, rerender } = render(<ProductCard {...PRODUCT} />);
    expect(screen.queryByRole("status")).toBeNull();

    linkState.pending = true;
    rerender(<ProductCard {...PRODUCT} />);

    expect(screen.getByRole("status").textContent).toMatch(/Opening product/);
    expect(container.querySelector(".animate-spin")).not.toBeNull();

    linkState.pending = false;
    rerender(<ProductCard {...PRODUCT} />);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("renders the chat variant with its reference badge and action buttons", () => {
    const onNavigate = vi.fn((event) => event.preventDefault());
    const { container } = render(
      <ProductCard variant="chat" {...PRODUCT} refNumber={2} onNavigate={onNavigate}>
        <button type="button">📎 Lampirkan</button>
        <button type="button">+ Keranjang</button>
      </ProductCard>
    );

    expect(screen.getByText("2")).toBeTruthy();
    expect(screen.getByText("📎 Lampirkan")).toBeTruthy();
    expect(screen.getByText("+ Keranjang")).toBeTruthy();
    expect(container.querySelector("a").getAttribute("href")).toBe("/product/42");

    fireEvent.click(screen.getByText("Running Shoes"));
    expect(onNavigate).toHaveBeenCalledTimes(1);
  });

  it("falls back to a dead link when the copilot has no product id", () => {
    const { container } = render(
      <ProductCard variant="chat" {...PRODUCT} id={undefined} refNumber={1} />
    );

    expect(container.querySelector("a").getAttribute("href")).toBe("#");
  });
});
