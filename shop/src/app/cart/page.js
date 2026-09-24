"use client";

import Link from "next/link";
import { useContext, useState } from "react";
import { useRouter } from "next/navigation"
import { CartContext } from "../../components/CartProvider";
import { formatRp } from "../../lib/formatRp";
import ProductImage from "@/components/ProductImage";

export default function CartPage() {
  const { items, cartCount, increaseQty, decreaseQty, updateQty, removeItem, subtotal } = useContext(CartContext);
  const router = useRouter();
  const [checkoutError, setCheckoutError] = useState("");

  function handleCheckout() {
    if (cartCount === 0) {
      setCheckoutError("Your cart is empty. Cannot checkout.");

      setTimeout(() => {
          setCheckoutError("");
        }, 3000
      ); 

      return;
    }

    setCheckoutError("");
    router.push("/checkout");
}

  return (
    <main className="py-12">
      <div className="mx-auto max-w-6xl px-7">
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
                  className="flex flex-col gap-4 border-t border-border pt-4 sm:flex-row sm:items-center sm:justify-between"
                >
                  <div className="flex items-start gap-4">
                    <div className="h-20 w-20 shrink-0 overflow-hidden rounded border border-border">
                      <ProductImage src={item.imageUrl} alt={item.title} padding="p-1" />
                    </div>
                    <div>
                      <Link
                        href={`/product/${item.id}`}
                        className="line-clamp-2 font-semibold text-foreground no-underline hover:underline"
                      >
                        {item.title}
                      </Link>
                      <p className="mt-1 text-sm text-muted">{formatRp(item.priceIdr)} each</p>
                      <p className="mt-0.5 text-sm font-medium text-foreground">
                        Line total: {formatRp(item.priceIdr * item.qty)}
                      </p>
                    </div>
                  </div>

                  <div className="flex items-center justify-between gap-3 sm:justify-end">
                    <div className="flex items-center gap-1.5">
                      <button
                        type="button"
                        onClick={() => decreaseQty(item.id)}
                        className="h-8 w-8 rounded-full border border-border bg-surface text-sm font-medium text-foreground hover:bg-brand-soft"
                        aria-label="Decrease quantity"
                      >
                        −
                      </button>
                      <input
                        type="number"
                        min="1"
                        max="99"
                        value={item.qty}
                        onChange={(e) => updateQty(item.id, e.target.value)}
                        className="h-8 w-14 rounded-md border border-border bg-surface text-center text-sm font-medium text-foreground outline-none focus:border-foreground"
                        aria-label="Adjust quantity"
                      />
                      <button 
                        type="button"
                        onClick={() => increaseQty(item.id)}
                        className="h-8 w-8 rounded-full border border-border bg-surface text-sm font-medium text-foreground hover:bg-brand-soft"
                        aria-label="Increase quantity"
                      >
                        +
                      </button>
                    </div>

                    <button 
                      type="button"
                      onClick={() => removeItem(item.id)}
                      className="ml-2 text-xs font-medium text-muted underline hover:text-cta transition-colors"
                    >
                      Remove
                    </button>
                  </div>
                </li>
              ))}
            </ul>

            <div className="mt-6 flex justify-between border-y border-border py-4 ">
              <span className="text-muted">Subtotal</span>
              <span className="font-semibold">{formatRp(subtotal)}</span>
            </div>
          </>
        )}
        <div className="mt-4 pt-10">
           <div className="relative">
              {checkoutError && (
                <p className="absolute bottom-full right-0 mb-2 text-sm text-muted">
                  {checkoutError}
                </p>
              )}

            <div className="flex items-start justify-between gap-4">
              <Link
                href="/#catalog"
                className="text-sm font-medium text-foreground no-underline hover:underline"
              >
                Continue shopping
              </Link>

              <button
                type="button"
                onClick={handleCheckout}
                className="rounded-full bg-cta px-6 py-3 text-sm font-medium text-white"
              >
                Checkout
              </button>
            </div>
          </div>
        </div>
      </div>
    </main>
  );
}
