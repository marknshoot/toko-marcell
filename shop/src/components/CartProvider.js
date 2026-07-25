"use client";

import { createContext, useState } from "react";

export const CartContext = createContext(null);

export function CartProvider({ children }) {
  const [items, setItems] = useState([]);
  const cartCount = items.reduce((sum, item) => sum + item.qty, 0);

  function addToCart(product) {
    setItems((prev) => {
      const existing = prev.find((item) => item.id === product.id);

      if (existing) {
        return prev.map((item) =>
          item.id === product.id
            ? { ...item, qty: item.qty + 1 }
            : item
        );
      }

      return [
        ...prev,
        {
          id: product.id,
          title: product.title,
          priceIdr: product.priceIdr,
          qty: 1,
        },
      ];
    });
  }

  return (
    <CartContext.Provider value={{ items, cartCount, addToCart }}>
      {children}
    </CartContext.Provider>
  );
}
