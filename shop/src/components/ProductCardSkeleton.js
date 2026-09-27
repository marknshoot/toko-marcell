export default function ProductCardSkeleton() {
  return (
    <div
      aria-hidden="true"
      className="overflow-hidden rounded-lg border border-border bg-surface"
    >
      <div className="aspect-square w-full animate-pulse bg-border motion-reduce:animate-none" />
      <div className="space-y-2 p-3">
        <div className="h-3 w-full animate-pulse rounded bg-border motion-reduce:animate-none" />
        <div className="h-3 w-2/3 animate-pulse rounded bg-border motion-reduce:animate-none" />
        <div className="mt-2 h-3 w-1/3 animate-pulse rounded bg-border motion-reduce:animate-none" />
      </div>
    </div>
  );
}
