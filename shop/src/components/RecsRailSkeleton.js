import ProductCardSkeleton from "./ProductCardSkeleton";
import SkeletonLine from "./SkeletonLine";

// Mirrors the "Customers Also Viewed" rail: heading plus four cards.
export default function RecsRailSkeleton({ announce = true }) {
  return (
    <section className="mt-12 border-t border-border pt-8" aria-busy="true">
      {announce ? (
        <span role="status" className="sr-only">
          Loading similar products…
        </span>
      ) : null}
      <SkeletonLine className="h-4 w-52" />
      <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
        {Array.from({ length: 4 }, (_, index) => (
          <ProductCardSkeleton key={index} />
        ))}
      </div>
    </section>
  );
}
