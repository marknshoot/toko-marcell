"use client";

import { useContext, useState } from "react";
import { useRouter } from "next/navigation";
import { CartContext } from "./CartProvider";

export default function AddToCartButton({ product }) {
  const router = useRouter();
  const { addToCart, items } = useContext(CartContext) || { items: [] };
  const inCartItem = items?.find((item) => item.id === product.id);
  const inCartQty = inCartItem ? inCartItem.qty : 0;

  // useState returns [value, setter] — must use [ ] not { }
  const [qty, setQty] = useState(1);

  function handleDecrease() {
    setQty(qty > 1 ? qty - 1 : 1);
  }

  function handleIncrease() {
    setQty(qty + 1);
  }

  function handleAdd() {
    addToCart(product, qty);
    router.push("/");
  }

  return (
    <div className="mt-6">
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={handleDecrease}
          className="h-8 w-8 rounded-full border border-border text-sm"
        >
          −
        </button>
        <span className="min-w-6 text-center text-sm">{qty}</span>
        <button
          type="button"
          onClick={handleIncrease}
          className="h-8 w-8 rounded-full border border-border text-sm"
        >
          +
        </button>
      </div>

      {inCartQty > 0 ? (
        <p className="mt-2 text-xs font-medium text-cta">
          ✓ {inCartQty} currently in your cart
        </p>
      ) : null}

      <button
        type="button"
        onClick={handleAdd}
        className="mt-4 rounded-full bg-cta px-6 py-3 text-sm font-medium text-white transition hover:bg-cta-hover"
      >
        {inCartQty > 0 ? "Add more to cart" : "Add to cart"}
      </button>
    </div>
  );
}
