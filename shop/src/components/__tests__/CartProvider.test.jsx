import { renderHook, act } from "@testing-library/react";
import { describe, it, expect, beforeEach, vi } from "vitest";
import { useContext } from "react";
import { CartProvider, CartContext } from "../CartProvider";

// Mock postEvent so it doesn't fail on network in node test environment
vi.mock("../../lib/api", () => ({
  postEvent: vi.fn(),
}));

const mockProductA = {
  id: 1,
  asin: "B000TEST1",
  title: "Classic Canvas Sneaker",
  priceIdr: 250000,
  imageUrl: "https://example.com/shoe.jpg",
};

const mockProductB = {
  id: 2,
  asin: "B000TEST2",
  title: "Denim Work Pant",
  priceIdr: 450000,
  imageUrl: "https://example.com/pants.jpg",
};

function useTestCart() {
  const context = useContext(CartContext);
  if (!context) {
    throw new Error("useTestCart must be used within CartProvider");
  }
  return context;
}

describe("CartProvider", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("starts with an empty cart and zero subtotal", () => {
    const { result } = renderHook(() => useTestCart(), { wrapper: CartProvider });
    expect(result.current.items).toEqual([]);
    expect(result.current.cartCount).toBe(0);
    expect(result.current.subtotal).toBe(0);
  });

  it("adds an item and calculates subtotal correctly", () => {
    const { result } = renderHook(() => useTestCart(), { wrapper: CartProvider });

    act(() => {
      result.current.addToCart(mockProductA, 2);
    });

    expect(result.current.items).toHaveLength(1);
    expect(result.current.items[0].qty).toBe(2);
    expect(result.current.cartCount).toBe(2);
    expect(result.current.subtotal).toBe(500000);
  });

  it("increments quantity when duplicate product is added", () => {
    const { result } = renderHook(() => useTestCart(), { wrapper: CartProvider });

    act(() => {
      result.current.addToCart(mockProductA, 1);
    });
    act(() => {
      result.current.addToCart(mockProductA, 3);
    });

    expect(result.current.items).toHaveLength(1);
    expect(result.current.items[0].qty).toBe(4);
    expect(result.current.cartCount).toBe(4);
    expect(result.current.subtotal).toBe(1000000);
  });

  it("handles multiple distinct products correctly", () => {
    const { result } = renderHook(() => useTestCart(), { wrapper: CartProvider });

    act(() => {
      result.current.addToCart(mockProductA, 1);
      result.current.addToCart(mockProductB, 2);
    });

    expect(result.current.items).toHaveLength(2);
    expect(result.current.cartCount).toBe(3);
    expect(result.current.subtotal).toBe(250000 + 2 * 450000);
  });

  it("updates and decreases quantity, removing when reaching 0", () => {
    const { result } = renderHook(() => useTestCart(), { wrapper: CartProvider });

    act(() => {
      result.current.addToCart(mockProductA, 2);
    });

    act(() => {
      result.current.decreaseQty(mockProductA.id);
    });
    expect(result.current.items[0].qty).toBe(1);

    act(() => {
      result.current.decreaseQty(mockProductA.id);
    });
    expect(result.current.items).toHaveLength(0);
    expect(result.current.cartCount).toBe(0);
    expect(result.current.subtotal).toBe(0);
  });

  it("clears all items when clearCart is called", () => {
    const { result } = renderHook(() => useTestCart(), { wrapper: CartProvider });

    act(() => {
      result.current.addToCart(mockProductA, 2);
      result.current.addToCart(mockProductB, 1);
    });
    expect(result.current.items).toHaveLength(2);

    act(() => {
      result.current.clearCart();
    });
    expect(result.current.items).toHaveLength(0);
    expect(result.current.cartCount).toBe(0);
    expect(result.current.subtotal).toBe(0);
  });

  it("persists items to localStorage under 'toko-cart-v2'", () => {
    const { result } = renderHook(() => useTestCart(), { wrapper: CartProvider });

    act(() => {
      result.current.addToCart(mockProductA, 1);
    });

    const stored = JSON.parse(localStorage.getItem("toko-cart-v2"));
    expect(stored).toHaveLength(1);
    expect(stored[0].asin).toBe("B000TEST1");
    expect(stored[0].qty).toBe(1);
  });
});
