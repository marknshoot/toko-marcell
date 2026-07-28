"use client";

import Link from "next/link";
import { useContext, useEffect } from "react";
import { CartContext } from "@/components/CartProvider";

export default function CheckoutSuccessPage() {
  const { cartCount, clearCart } = useContext(CartContext);
 
  useEffect(() => {
      clearCart();
    }, []
  );

  return (
    <main className="py-12">
      <div className="mx-auto max-w-2xl px-7">
        <h1 className="text-3xl font-semibold tracking-tight text-foreground">
          Order confirmed (demo)
        </h1>
        <p className="mt-4 text-muted">
          This is a demo success screen. No real money was charged.
        </p>
        {cartCount > 0 && (
          <p className="mt-2 text-sm text-muted">
            Demo order total was based on your cart (
            {cartCount} item{cartCount === 1 ? "" : "s"}).
          </p>
        )}

        <Link
          href="/#catalog"
          className="mt-8 inline-flex rounded-full bg-cta px-6 py-3 text-sm font-medium text-white no-underline"
        >
          Back to shop
        </Link>
      </div>
    </main>
  );
}
