"use client";

import Link from "next/link";
import { useCart } from "./CartProvider";

export default function CartLink() {
  const { cartCount } = useCart();

  return (
    <Link
        href="/cart"
        className="text-muted no-underline transition-colors hover:text-foreground"
    >
        Cart ({cartCount})
    </Link>
  );
}