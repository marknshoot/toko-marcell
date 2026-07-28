"use client";

import Link from "next/link";
import { useContext, useState } from "react";
import { useRouter } from "next/navigation"
import { CartContext } from "../../components/CartProvider";
import { formatRp } from "../../lib/formatRp";

export default function CartPage() {
  const { items, cartCount, increaseQty, decreaseQty, removeItem, subtotal } = useContext(CartContext);
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
