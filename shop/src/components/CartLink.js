"use client";

import Link from "next/link";
import { CartContext } from "./CartProvider";
import { useContext } from "react";

export default function CartLink() {
    const { cartCount } = useContext(CartContext);

    return (
        <Link
            href="/cart"
            className="text-muted no-underline transition-colors hover:text-foreground"
        >
            Cart ({cartCount})
        </Link>
    );
}