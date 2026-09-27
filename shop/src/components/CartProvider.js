"use client";

import { createContext, useState, useEffect } from "react";
import { postEvent } from "../lib/api";

export const CartContext = createContext(null);

const CART_STORAGE_KEY = "toko-cart-v2";

export function CartProvider({ children }) {
    const [items, setItems] = useState([]);
    const [hasLoaded, setHasLoaded] = useState(false);

    useEffect(() => {
        try {
            const raw = localStorage.getItem(CART_STORAGE_KEY);
            if (raw) {
                const parsed = JSON.parse(raw);
                if (Array.isArray(parsed)) {
                    // eslint-disable-next-line react-hooks/set-state-in-effect
                    setItems(parsed);
                }
            }
        } catch {
            
        } finally {
            setHasLoaded(true);
        }

        function handleStorage(event) {
            if (event.key === CART_STORAGE_KEY) {
                try {
                    const parsed = event.newValue ? JSON.parse(event.newValue) : [];
                    if (Array.isArray(parsed)) {
                        setItems(parsed);
                    }
                } catch {
                }
            }
        }

        window.addEventListener("storage", handleStorage);
        return () => window.removeEventListener("storage", handleStorage);
    }, []); 

    const cartCount = items.reduce((sum, item) => sum + item.qty, 0);

    function addToCart(product, q) {
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
            imageUrl: product.imageUrl,
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

    function updateQty(id, newQty) {
        const val = parseInt(newQty, 10);
        if (isNaN(val) || val <= 0) {
            removeItem(id);
            return;
        }
        const clamped = Math.min(99, Math.max(1, val));
        setItems((prev) =>
            prev.map((item) => (item.id === id ? { ...item, qty: clamped } : item))
        );
    }

    const subtotal = items.reduce(
        (sum, item) => sum + item.priceIdr * item.qty, 0
    );

    function clearCart() {
        setItems((prev) => (prev.length === 0 ? prev : []));
    }

    useEffect(() => {
        if (!hasLoaded) return;
        localStorage.setItem(CART_STORAGE_KEY, JSON.stringify(items));
    }, [items, hasLoaded]);

    return (
        <CartContext.Provider value={{ items, cartCount, addToCart, increaseQty, decreaseQty, updateQty, removeItem, subtotal, clearCart }}>
        {children}
        </CartContext.Provider>
    );
}
