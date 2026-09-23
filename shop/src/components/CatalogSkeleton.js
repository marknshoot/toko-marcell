import ProductCardSkeleton from "./ProductCardSkeleton";

// The whole catalog section in its loading state. Used as the Suspense fallback
// in app/page.js, where the server has to send something before the URL (and
// therefore the filter + page) is known on the client.
export default function CatalogSkeleton({ count = 6 }) {
  return (
    <section className="border-t border-border py-12" aria-busy="true">
      <div className="mx-auto max-w-2xl px-7">
        <p className="text-xs font-medium uppercase tracking-[0.12em] text-muted">
          Catalog
        </p>
        <h2 className="mt-2 text-3xl font-semibold tracking-tight text-foreground">
          Shop
        </h2>
        <p className="mt-2 text-muted">Loading products…</p>

        <div className="mt-6 grid grid-cols-2 gap-3">
          {Array.from({ length: count }, (_, index) => (
            <ProductCardSkeleton key={index} />
          ))}
        </div>
      </div>
    </section>
  );
}
