"use client";

import { useEffect } from "react";
import Link, { useLinkStatus } from "next/link";
import { buildCatalogUrl } from "../lib/catalogParams";

// Only a Prev/Next click should pull the viewport back to the catalog. A filter
// change also resets ?page=1, but the shopper is already at the top of this
// section, so it must not scroll. Module scope survives the RSC swap.
let pendingScroll = false;

function scrollToCatalog() {
  const catalog = document.getElementById("catalog");
  if (!catalog) {
    return;
  }

  const reduceMotion =
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  catalog.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth" });
}

// Inside the <Link> on purpose: useLinkStatus() only reports the navigation
// owned by that link.
function PagerPending() {
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

function PagerLink({ href, disabled, children }) {
  if (disabled) {
    return (
      <span
        aria-disabled="true"
        className="rounded-full border border-border bg-surface px-4 py-2 text-xs font-medium text-foreground opacity-40"
      >
        {children}
      </span>
    );
  }

  return (
    <Link
      href={href}
      scroll={false}
      onClick={(event) => {
        const isModifiedClick =
          event.metaKey ||
          event.ctrlKey ||
          event.shiftKey ||
          event.altKey ||
          event.button !== 0;
        if (!isModifiedClick) {
          pendingScroll = true;
        }
      }}
      className="rounded-full border border-border bg-surface px-4 py-2 text-xs font-medium text-foreground no-underline transition-colors hover:border-foreground/40"
    >
      {children}
      <PagerPending />
    </Link>
  );
}

export default function CatalogPagination({
  department,
  q,
  page,
  totalPages,
  total,
}) {
  // Runs after the new page commits (and on mount if the component was replaced
  // by the RSC swap). A direct visit to /?page=3 never scrolls because the flag
  // is only set by a click on Prev/Next.
  useEffect(() => {
    if (!pendingScroll) {
      return;
    }
    pendingScroll = false;
    scrollToCatalog();
  }, [page]);

  if (total <= 0) {
    return null;
  }

  return (
    <div className="mt-6 flex items-center justify-between gap-3">
      <PagerLink
        href={buildCatalogUrl({ department, page: page - 1, q })}
        disabled={page <= 1}
      >
        Prev
      </PagerLink>

      <p className="text-xs text-muted">
        Page {page} of {totalPages} · {total.toLocaleString()} items
      </p>

      <PagerLink
        href={buildCatalogUrl({ department, page: page + 1, q })}
        disabled={page >= totalPages}
      >
        Next
      </PagerLink>
    </div>
  );
}
