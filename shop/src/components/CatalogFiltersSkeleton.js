// The facet fetch has its own Suspense boundary so a slow /categories call only
// delays the pills — the search box above them never waits for it.
export default function CatalogFiltersSkeleton({ count = 6 }) {
  return (
    <div className="mt-4 flex flex-wrap gap-2" aria-hidden="true">
      {Array.from({ length: count }, (_, index) => (
        <div
          key={index}
          className="h-7 w-16 animate-pulse rounded-full border border-border bg-surface motion-reduce:animate-none"
        />
      ))}
    </div>
  );
}
