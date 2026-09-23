"use client";

import { useContext, useEffect, useRef } from "react";
import { CartContext } from "./CartProvider";

// Empties the cart once the server has confirmed the order.
//
// This used to live on /checkout/success, which cleared the cart and then showed
// a confirmation to anyone who opened that URL — including someone who never
// paid. Now it only runs on the page that read a real order back from the server.
export default function ClearCart() {
  const { clearCart } = useContext(CartContext);
  const cleared = useRef(false);

  useEffect(() => {
    if (cleared.current) {
      return;
    }
    cleared.current = true;
    clearCart();
  }, [clearCart]);

  return null;
}
