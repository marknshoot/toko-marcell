"use client";

import Link, { useLinkStatus } from "next/link";

// Rendered inside the <Link> on purpose: useLinkStatus() only reports the
// navigation owned by that link, so the pill the shopper clicked is the one
// that reacts while the server renders the new grid.
function PendingDot() {
  const { pending } = useLinkStatus();
  if (!pending) {
    return null;
  }

  return (
    <span
      aria-hidden="true"
      className="ml-1.5 inline-block h-2 w-2 animate-spin rounded-full border border-current border-t-transparent motion-reduce:animate-none"
    />
  );
}

/**
 * A department pill that is a real anchor: it works without JavaScript, can be
 * opened in a new tab, and is prefetched by Next when it enters the viewport.
 */
export default function FilterLink({ href, active, children }) {
  const className = active
    ? "rounded-full bg-foreground px-3 py-1.5 text-xs font-medium text-white no-underline"
    : "rounded-full border border-border bg-surface px-3 py-1.5 text-xs font-medium text-muted no-underline transition-colors hover:border-foreground/40 hover:text-foreground";

  return (
    <Link
      href={href}
      scroll={false}
      aria-current={active ? "page" : undefined}
      className={className}
    >
      {children}
      <PendingDot />
    </Link>
  );
}
