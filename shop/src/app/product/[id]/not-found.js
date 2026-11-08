import Link from "next/link";

export default function ProductNotFound() {
  return (
    <main className="py-16">
      <div className="mx-auto max-w-lg px-7 text-center">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-muted">
          Toko Marcell
        </p>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight text-foreground">
          Product not found
        </h1>
        <p className="mt-2 text-sm text-muted">
          This item may have sold out, or the link is out of date.
        </p>
        <Link
          href="/#catalog"
          className="mt-6 inline-flex rounded-full bg-cta px-4 py-2 text-sm font-semibold text-white transition hover:bg-cta-hover"
        >
          Back to the catalog
        </Link>
      </div>
    </main>
  );
}
