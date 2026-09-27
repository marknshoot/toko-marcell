import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import AddToCartButton from "../AddToCartButton";
import { CartContext } from "../CartProvider";

const mockPush = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
}));

const mockProduct = {
  id: 101,
  asin: "B000TEST",
  title: "Levi's 501 Original Fit Jeans",
  priceIdr: 899000,
};

describe("AddToCartButton", () => {
  it("increments and decrements quantity selector", () => {
    const addToCartMock = vi.fn();
    render(
      <CartContext.Provider value={{ addToCart: addToCartMock, items: [] }}>
        <AddToCartButton product={mockProduct} />
      </CartContext.Provider>
    );

    const plusBtn = screen.getByText("+");
    const minusBtn = screen.getByText("−");

    expect(screen.getByText("1")).toBeTruthy();

    fireEvent.click(plusBtn);
    expect(screen.getByText("2")).toBeTruthy();

    fireEvent.click(plusBtn);
    expect(screen.getByText("3")).toBeTruthy();

    fireEvent.click(minusBtn);
    expect(screen.getByText("2")).toBeTruthy();

    // Does not go below 1
    fireEvent.click(minusBtn);
    fireEvent.click(minusBtn);
    expect(screen.getByText("1")).toBeTruthy();
  });

  it("adds selected quantity to cart and navigates home", () => {
    const addToCartMock = vi.fn();
    render(
      <CartContext.Provider value={{ addToCart: addToCartMock, items: [] }}>
        <AddToCartButton product={mockProduct} />
      </CartContext.Provider>
    );

    const plusBtn = screen.getByText("+");
    fireEvent.click(plusBtn); // qty = 2

    const addBtn = screen.getByRole("button", { name: "Add to cart" });
    fireEvent.click(addBtn);

    expect(addToCartMock).toHaveBeenCalledWith(mockProduct, 2);
    expect(mockPush).toHaveBeenCalledWith("/");
  });

  it("displays existing quantity if already in cart", () => {
    render(
      <CartContext.Provider
        value={{
          addToCart: vi.fn(),
          items: [{ id: 101, qty: 3 }],
        }}
      >
        <AddToCartButton product={mockProduct} />
      </CartContext.Provider>
    );

    expect(screen.getByText(/3 currently in your cart/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Add more to cart" })).toBeTruthy();
  });
});
