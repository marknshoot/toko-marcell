"use client";

import Link from "next/link";
import { useContext } from "react";
import { CartContext } from "../../components/CartProvider";
import { formatRp } from "../../lib/formatRp";

export default function CartPage() {
  const { items, cartCount, increaseQty, decreaseQty, removeItem, subtotal } = useContext(CartContext);

  return (
    <main className="py-12">
      <div className="mx-auto max-w-2xl px-7">
        <h1 className="text-3xl font-semibold tracking-tight text-foreground">
          Cart ({cartCount})
        </h1>

        {cartCount === 0 ? (
          <p className="mt-4 text-muted">Your cart is empty.</p>
        ) : (
          <>
            <ul className="mt-6 space-y-4">
              {items.map((item) => (
                  <li
                    key={item.id}
                    className="flex items-start justify-between border-t border-border pt-4"
                  >
                    <div className="h-20 w-20 shrink-0 bg-brand-soft" />
                    <div className="flex flex-col gap-y-2 items-end">
                      <p className="font-semibold text-foreground">{item.title}</p>
                      <p className="text-sm text-muted">{formatRp(item.priceIdr)}</p>
                      <p className="text-foreground">{formatRp(item.priceIdr * item.qty)}</p>
                      <div className="flex items-center justify-end gap-2 mt-2">
                        <button
                          type="button"
                          onClick={() => decreaseQty(item.id)}
                          className="h-8 w-8 rounded-full border border-border text-sm"
                        >
                          -
                        </button>
                        <span className="min-w-6 text-center text-sm">
                          {item.qty}
                        </span>
                        <button 
                          type="button"
                          onClick={() => increaseQty(item.id)}
                          className="h-8 w-8 rounded-full border border-border text-sm"
                        >
                          +
                        </button>
                        <button 
                          type="button"
                          onClick={() => removeItem(item.id)}
                        >
                          Remove
                        </button>
                      </div>
                    </div>
                  </li>
              ))}
            </ul>

            <div className="mt-6 flex justify-between border-t border-border pt-4">
              <span className="text-muted">Subtotal</span>
              <span className="font-semibold">{formatRp(subtotal)}</span>
            </div>
          </>
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
