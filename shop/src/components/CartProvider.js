"use client";

import { createContext, useState, useEffect } from "react";
import { postEvent } from "../lib/api";

export const CartContext = createContext(null);

export function CartProvider({ children }) {
    const [items, setItems] = useState([]);
    const [hasLoaded, setHasLoaded] = useState(false);

    useEffect(() => {
        try {
            // v2: cart lines now carry `asin`, which is what checkout sends to the
            // server. Old v1 carts have no asin, so the key is bumped instead of
            // migrating — a cart line that cannot be priced is worse than no cart.
            const raw = localStorage.getItem("toko-cart-v2");
            if (raw) {
            const parsed = JSON.parse(raw);
            if (Array.isArray(parsed)) {
                setItems(parsed);
            }
            }
        } catch {
            
        } finally {
            setHasLoaded(true);
        }
    }, []); 


    const cartCount = items.reduce((sum, item) => sum + item.qty, 0);

    function addToCart(product, q) {
        // Funnel event (M2). Fired here because this is the single place a cart
        // line is created, so every entry point is covered by construction.
        postEvent({
            eventType: "add_to_cart",
            asin: product.asin,
            qty: q,
            priceIdr: product.priceIdr,
        });

        setItems((prev) => {
        const existing = prev.find((item) => item.id === product.id);

        if (existing) {
            return prev.map((item) =>
            item.id === product.id
                ? { ...item, qty: item.qty + q }
                : item
            );
        }

        return [
            ...prev,
            {
            id: product.id,
            asin: product.asin,
            title: product.title,
            priceIdr: product.priceIdr,
            qty: q,
            },
        ];
        });
    }

    function increaseQty(id) {
        setItems(
            (prev) => prev.map(
                (item) => item.id === id
                    ? { ...item, qty: item.qty + 1 } 
                    : item
            )
        );
    }

    function decreaseQty(id) {
        setItems(
            (prev) => prev.map(
                (item) => item.id === id
                    ? { ...item, qty: item.qty - 1 } 
                    : item
            ).filter((item) => item.qty > 0)
        );
    }

    function removeItem(id){
        setItems((prev) => prev.filter((item) => item.id !== id));
    }

    const subtotal = items.reduce(
        (sum, item) => sum + item.priceIdr * item.qty, 0
    );

    function clearCart() {
        // Keep same [] reference if already empty — avoids extra re-renders
        setItems((prev) => (prev.length === 0 ? prev : []));
    }

    useEffect(() => {
        if (!hasLoaded) return; // important!
        localStorage.setItem("toko-cart-v2", JSON.stringify(items));
    }, [items, hasLoaded]);

    return (
        <CartContext.Provider value={{ items, cartCount, addToCart, increaseQty, decreaseQty, removeItem, subtotal, clearCart }}>
        {children}
        </CartContext.Provider>
    );
}
