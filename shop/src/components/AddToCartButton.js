"use client";

import { useCart } from "./CartProvider";

export default function AddToCartButton() {
  const { addToCart } = useCart();

  return (
    <button
      type="button"
      className="mt-6 rounded-full bg-cta px-6 py-3 text-sm font-medium text-white"
      onClick={addToCart}
    >
      Add to cart
    </button>
  );
}
