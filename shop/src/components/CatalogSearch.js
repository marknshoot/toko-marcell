"use client";

import { useCallback, useEffect, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { MAX_QUERY_LENGTH, buildCatalogUrl } from "../lib/catalogParams";

const DEBOUNCE_MS = 250;

// Lives outside the results Suspense boundary: it must never unmount while the
// server renders a new grid, or the shopper loses focus and cursor position.
export default function CatalogSearch({ q, department }) {
  const router = useRouter();
  const [searchInput, setSearchInput] = useState(q);
  const [previousQuery, setPreviousQuery] = useState(q);
  const [isPending, startTransition] = useTransition();

  // Queries this component has already navigated to. When the matching q prop
  // comes back we must NOT copy it into the input: the shopper may have typed
  // more since the request was sent, and adopting the older value would drop
  // those keystrokes. Anything not in this set is an external change
  // (back/forward, a shared link) and is safe to adopt.
  const [requestedQueries, setRequestedQueries] = useState(() => new Set());

  const navigate = useCallback(
    (nextQuery) => {
      setRequestedQueries((current) => {
        const next = new Set(current);
        next.add(nextQuery);
        return next;
      });
      startTransition(() => {
        router.replace(
          buildCatalogUrl({ department, page: 1, q: nextQuery }),
          { scroll: false }
        );
      });
    },
    [department, router, startTransition]
  );

  if (q !== previousQuery) {
    setPreviousQuery(q);
    if (requestedQueries.has(q)) {
      const next = new Set(requestedQueries);
      next.delete(q);
      setRequestedQueries(next);
    } else {
      setSearchInput(q);
    }
  }

  useEffect(() => {
    const trimmed = searchInput.trim();
    if (trimmed === q) {
      return;
    }

    const timer = setTimeout(() => {
      navigate(trimmed);
    }, DEBOUNCE_MS);

    return () => clearTimeout(timer);
  }, [searchInput, q, navigate]);

  function clearQuery() {
    setSearchInput("");
    navigate("");
  }

  return (
    <div className="mt-6 flex flex-col gap-3 sm:flex-row sm:items-center">
      {/* A plain GET form keeps search working before hydration / without JS.
          With JS, onSubmit hands the navigation to the debounced router. */}
      <form
        action="/"
        method="get"
        role="search"
        className="relative flex-1"
        onSubmit={(event) => {
          event.preventDefault();
          const trimmed = searchInput.trim();
          if (trimmed !== q) {
            navigate(trimmed);
          }
        }}
      >
        <input
          type="search"
          name="q"
          maxLength={MAX_QUERY_LENGTH}
          placeholder="Try: levis 501, white sneakers, waterproof jacket…"
          value={searchInput}
          onChange={(event) => setSearchInput(event.target.value)}
          className="w-full rounded-full border border-border bg-surface px-4 py-2.5 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-foreground/20"
        />
        {/* Implicit submission needs a submit control in some browsers; this
            also gives screen readers a labelled action. */}
        <button type="submit" className="sr-only">
          Search
        </button>
        {department !== "All" ? (
          <input type="hidden" name="department" value={department} />
        ) : null}
      </form>

      {searchInput ? (
        <button
          type="button"
          onClick={clearQuery}
          className="shrink-0 rounded-full border border-border bg-surface px-4 py-2.5 text-xs font-medium text-muted"
        >
          Clear
        </button>
      ) : null}

      <span role="status" className="text-xs text-muted">
        {isPending ? "Searching…" : ""}
      </span>
    </div>
  );
}
