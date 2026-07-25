"use client";

import { createContext, useState, useEffect } from "react";

export const CartContext = createContext(null);

export function CartProvider({ children }) {
    const [items, setItems] = useState([]);
    const [hasLoaded, setHasLoaded] = useState(false);

    useEffect(() => {
        try {
            const raw = localStorage.getItem("toko-cart");
            if (raw) {
            const parsed = JSON.parse(raw);
            if (Array.isArray(parsed)) {
                setItems(parsed);
            }
            }
        } catch {
            // ignore bad data
        } finally {
            setHasLoaded(true);
        }
    }, []); // empty = once on client


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

    useEffect(() => {
        if (!hasLoaded) return; // important!
        localStorage.setItem("toko-cart", JSON.stringify(items));
    }, [items, hasLoaded]);

    return (
        <CartContext.Provider value={{ items, cartCount, addToCart, increaseQty, decreaseQty, removeItem, subtotal }}>
        {children}
        </CartContext.Provider>
    );
}
