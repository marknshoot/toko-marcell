"use client";

import Link from "next/link";
import { useContext } from "react";
import { CartContext } from "@/components/CartProvider";
import { formatRp } from "@/lib/formatRp";

export default function CheckoutPage() {
  const { items, cartCount, subtotal } = useContext(CartContext);

  if (cartCount === 0) {
    return (
      <main className="py-12">
        <div className="mx-auto max-w-2xl px-7">
            <h1 className="text-3xl font-semibold text-foreground">Checkout</h1>
            <p className="mt-4 text-muted">Your cart is empty.</p>
            <Link
              href="/#catalog"
              className="mt-6 inline-block text-sm font-medium text-foreground no-underline hover:underline"
            >
                Continue shopping
            </Link>
        </div>
      </main>
    );
  }

  return (
    <main className="py-12">
        <div className="mx-auto max-w-2xl px-7">
            <h1 className="text-3xl font-semibold text-foreground">Checkout</h1>
            <p className="mt-2 text-sm text-muted">Demo only, no real payment.</p>

            <ul className="mt-6 space-y-3">
            {items.map((item) => (
                <li key={item.id} className="flex justify-between border-t border-border pt-3">
                <span className="text-foreground">
                    {item.title} x {item.qty}
                </span>
                <span className="text-foreground">
                    {formatRp(item.priceIdr * item.qty)}
                </span>
                </li>
            ))}
            </ul>

            <div className="mt-6 flex justify-between border-t border-border pt-4">
            <span className="text-muted">Subtotal</span>
            <span className="font-semibold">{formatRp(subtotal)}</span>
            </div>
            <div className="flex items-start justify-between gap-4 mt-4 pt-10">
                <Link href="/cart" className="text-sm font-medium text-foreground no-underline hover:underline">
                Back to cart
                </Link>
                
                <Link
                href="/checkout/qris"
                className="rounded-full bg-cta px-6 py-3 text-sm font-medium text-white"
                >
                Pay with QRIS (demo)
                </Link>
            </div>
        </div>
    </main>
  );
}