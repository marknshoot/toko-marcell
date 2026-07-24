import Link from "next/link";

export default function CartPage() {
  return (
    <main className="py-12">
      <div className="mx-auto max-w-2xl px-7">
        <h1 className="text-3xl font-semibold tracking-tight text-foreground">
          Cart
        </h1>
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