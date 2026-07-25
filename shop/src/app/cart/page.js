"use client";

import Link from "next/link";
import { useContext } from "react";
import { CartContext } from "../../components/CartProvider";
import { formatRp } from "../../lib/formatRp";

export default function CartPage() {
  const { items, cartCount } = useContext(CartContext);

  return (
    <main className="py-12">
      <div className="mx-auto max-w-2xl px-7">
        <h1 className="text-3xl font-semibold tracking-tight text-foreground">
          Cart ({cartCount})
        </h1>

        {cartCount === 0 ? (
          <p className="mt-4 text-muted">Your cart is empty.</p>
        ) : (
          <ul className="mt-6 space-y-4">
            {items.map((item) => (
              <li
                key={item.id}
                className="flex items-start justify-between border-t border-border p-3"
              >
                <div className="h-20 w-20 shrink-0 bg-brand-soft" />
                <div className="text-right">
                  <p className="font-semibold text-foreground">{item.title}</p>
                  <p className="text-sm text-muted">
                    {formatRp(item.priceIdr)} · qty {item.qty}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        )}

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
