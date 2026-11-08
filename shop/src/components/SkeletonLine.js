export default function SkeletonLine({ className = "" }) {
  return (
    <span
      aria-hidden="true"
      className={`block animate-pulse rounded bg-border motion-reduce:animate-none ${className}`}
    />
  );
}
