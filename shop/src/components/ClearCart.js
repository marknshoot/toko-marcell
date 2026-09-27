"use client";

import { useContext, useEffect, useRef } from "react";
import { CartContext } from "./CartProvider";

export default function ClearCart() {
  const { clearCart } = useContext(CartContext);
  const hasClearedRef = useRef(false);

  useEffect(() => {
    if (hasClearedRef.current) {
      return;
    }
    hasClearedRef.current = true;
    clearCart();
  }, [clearCart]);

  return null;
}
