"use client";

import Link from "next/link";
import { useContext, useState } from "react";
import { useRouter } from "next/navigation";
import { CartContext } from "@/components/CartProvider";
import { confirmCheckout } from "@/lib/api";
import { formatRp } from "@/lib/formatRp";

export default function QrisPage() {
  const { items, cartCount, subtotal } = useContext(CartContext);
  const router = useRouter();
  const [paying, setPaying] = useState(false);
  const [error, setError] = useState(null);

  // "I have paid" is no longer a link to a static success page: it asks the
  // server to create the order. Only the token that comes back is proof, and the
  // purchase event is written by the server (see POST /checkout/confirm).
  async function handlePaid() {
    setPaying(true);
    setError(null);

    try {
      const order = await confirmCheckout(
        items.map((item) => ({ asin: item.asin, qty: item.qty }))
      );
      router.push(`/checkout/confirm/${order.token}`);
    } catch (err) {
      setError("Could not confirm the order. Please try again.");
      setPaying(false);
    }
  }

  if (cartCount === 0) {
    return (
      <main className="py-12">
        <div className="mx-auto max-w-2xl px-7">
          <h1 className="text-3xl font-semibold tracking-tight text-foreground">
            QRIS
          </h1>
          <p className="mt-4 text-muted">
            Nothing to pay. Your cart is empty.
          </p>
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
        <h1 className="text-3xl font-semibold tracking-tight text-foreground">
          QRIS (demo)
        </h1>
        <p className="mt-2 text-sm text-muted">
          Demo only — no real payment.
        </p>

        <p className="mt-8 text-sm text-muted">Amount to pay</p>
        <p className="mt-1 text-3xl font-semibold text-foreground">
          {formatRp(subtotal)}
        </p>

        <div
          className="mx-auto mt-8 h-48 w-48 rounded-lg border border-border bg-brand-soft"
          aria-hidden
        />
        <p className="mt-4 text-center text-sm text-muted">
          Scan with any e-wallet (demo placeholder)
        </p>

        <div className="mt-10 flex flex-col items-stretch gap-4 sm:flex-row sm:items-center sm:justify-between">
          <Link
            href="/checkout"
            className="text-sm font-medium text-foreground no-underline hover:underline"
          >
            Back to checkout
          </Link>
          <button
            type="button"
            onClick={handlePaid}
            disabled={paying}
            className="inline-flex w-fit rounded-full bg-cta px-6 py-3 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-60"
          >
            {paying ? "Confirming…" : "I have paid (demo)"}
          </button>

          {error ? <p className="mt-3 text-sm text-muted">{error}</p> : null}
        </div>
      </div>
    </main>
  );
}
