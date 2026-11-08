import RecsRailSkeleton from "./RecsRailSkeleton";
import ReviewsSkeleton from "./ReviewsSkeleton";
import SkeletonLine from "./SkeletonLine";

// Shape of the product page while it renders: hero on the left/right split, then
// the details and description blocks, then reviews and the recommendation rail.
// Dimensions match the real sections so nothing jumps when the page arrives.
export default function ProductPageSkeleton({ reviews = true, recs = true }) {
  return (
    <main className="py-12" aria-busy="true">
      <span role="status" className="sr-only">
        Loading product…
      </span>

      <div className="mx-auto max-w-6xl px-7">
        <div className="mt-6 grid gap-8 md:grid-cols-2">
          <div className="aspect-square w-full animate-pulse rounded-lg bg-border motion-reduce:animate-none" />
          <div className="space-y-3">
            <SkeletonLine className="h-3 w-40" />
            <SkeletonLine className="h-9 w-3/4" />
            <SkeletonLine className="h-3 w-24" />
            <SkeletonLine className="h-3 w-36" />
            <SkeletonLine className="h-5 w-28" />
            <div className="h-11 w-full animate-pulse rounded-lg bg-border motion-reduce:animate-none" />
            <div className="h-11 w-full animate-pulse rounded-lg bg-border motion-reduce:animate-none" />
          </div>
        </div>

        <div className="mt-10 space-y-2 border-t border-border pt-6">
          <SkeletonLine className="h-3 w-20" />
          <SkeletonLine className="h-3 w-full" />
          <SkeletonLine className="h-3 w-5/6" />
          <SkeletonLine className="h-3 w-2/3" />
        </div>

        <div className="mt-8 space-y-2 border-t border-border pt-6">
          <SkeletonLine className="h-3 w-28" />
          <SkeletonLine className="h-3 w-full" />
          <SkeletonLine className="h-3 w-full" />
          <SkeletonLine className="h-3 w-3/4" />
        </div>

        {reviews ? <ReviewsSkeleton announce={false} /> : null}
        {recs ? <RecsRailSkeleton announce={false} /> : null}
      </div>
    </main>
  );
}
