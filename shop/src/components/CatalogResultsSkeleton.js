import ProductCardSkeleton from "./ProductCardSkeleton";

// Shape of the results block while the server fetch resolves: the count line and
// the same 2/4-column grid the real cards use, so nothing reflows on arrival.
export default function CatalogResultsSkeleton({ count = 24 }) {
  return (
    <div className="mt-6" aria-busy="true">
      <span role="status" className="sr-only">
        Loading products
      </span>
      <div
        aria-hidden="true"
        className="h-4 w-64 animate-pulse rounded bg-border motion-reduce:animate-none"
      />
      <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        {Array.from({ length: count }, (_, index) => (
          <ProductCardSkeleton key={index} />
        ))}
      </div>
    </div>
  );
}
