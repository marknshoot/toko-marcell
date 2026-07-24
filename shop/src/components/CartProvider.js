"use client";

import { createContext, useState } from "react";

export const CartContext = createContext(null);

export function CartProvider({ children }) {
    const [cartCount, setCartCount] = useState(0);

    function addToCart() {
        setCartCount((n) => n + 1);
    }

    return (
        <CartContext.Provider value={{ cartCount, addToCart }}>
            {children}
        </CartContext.Provider>
    );
}