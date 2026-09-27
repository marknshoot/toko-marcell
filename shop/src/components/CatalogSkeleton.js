import ProductCardSkeleton from "./ProductCardSkeleton";

export default function CatalogSkeleton({ count = 24 }) {
  return (
    <section id="catalog" className="border-border py-12" aria-busy="true">
      <div className="mx-auto max-w-6xl px-7">
        <p className="text-xs font-medium uppercase tracking-[0.12em] text-muted">
          Catalog
        </p>
        <h2 className="mt-2 text-3xl font-semibold tracking-tight text-foreground">
          Shop
        </h2>
        <p className="mt-2 text-muted">Loading products…</p>

        <div className="mt-6 flex flex-col gap-3 sm:flex-row sm:items-center">
          <div className="h-10 flex-1 rounded-full border border-border bg-surface/50 animate-pulse motion-reduce:animate-none" />
        </div>

        <div className="mt-4 flex flex-wrap gap-2">
          {["w-12", "w-16", "w-14", "w-16", "w-14", "w-16"].map((w, index) => (
            <div
              key={index}
              className={`h-7 ${w} rounded-full border border-border bg-surface animate-pulse motion-reduce:animate-none`}
            />
          ))}
        </div>

        <div className="mt-6 grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-4">
          {Array.from({ length: count }, (_, index) => (
            <ProductCardSkeleton key={index} />
          ))}
        </div>
      </div>
    </section>
  );
}
