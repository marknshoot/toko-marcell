"use client";

import { CartContext } from "./CartProvider";
import { useContext } from "react";

export default function AddToCartButton({ product }) {
  const { addToCart } = useContext(CartContext);

  return (
    <button
      type="button"
      className="mt-6 rounded-full bg-cta px-6 py-3 text-sm font-medium text-white"
      onClick={() => addToCart(product)}
    >
      Add to cart
    </button>
  );
}
