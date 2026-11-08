import SkeletonLine from "./SkeletonLine";

// Mirrors ProductReviews: a summary column (score + histogram) and a review list.
export default function ReviewsSkeleton({ announce = true }) {
  return (
    <section className="mt-12 border-t border-border pt-8" aria-busy="true">
      {announce ? (
        <span role="status" className="sr-only">
          Loading reviews…
        </span>
      ) : null}
      <SkeletonLine className="h-4 w-44" />

      <div className="mt-6 flex flex-col gap-8 md:flex-row md:items-start md:justify-between">
        <div className="w-full md:w-1/3">
          <div className="flex items-baseline gap-3">
            <SkeletonLine className="h-9 w-16" />
            <div className="space-y-2">
              <SkeletonLine className="h-3 w-24" />
              <SkeletonLine className="h-3 w-32" />
            </div>
          </div>
          <div className="mt-6 space-y-2">
            {[0, 1, 2, 3, 4].map((row) => (
              <SkeletonLine key={row} className="h-2 w-full" />
            ))}
          </div>
        </div>

        <div className="w-full space-y-6 md:w-2/3">
          {[0, 1, 2].map((row) => (
            <div key={row} className="space-y-2">
              <div className="flex items-center gap-2.5">
                <span className="h-7 w-7 animate-pulse rounded-full bg-border motion-reduce:animate-none" />
                <SkeletonLine className="h-3 w-28" />
              </div>
              <SkeletonLine className="h-3 w-24" />
              <SkeletonLine className="h-3 w-full" />
              <SkeletonLine className="h-3 w-5/6" />
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
