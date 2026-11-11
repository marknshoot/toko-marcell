"use client";

import { useTransition } from "react";
import { useRouter } from "next/navigation";

// router.refresh() re-runs the current route on the server, which is what the
// results Suspense boundary needs after a cold-start or timeout failure.
export default function RetryButton() {
  const router = useRouter();
  const [isPending, startTransition] = useTransition();

  return (
    <button
      type="button"
      onClick={() => startTransition(() => router.refresh())}
      disabled={isPending}
      className="mt-3 rounded-full border border-border bg-surface px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:border-foreground/40 disabled:opacity-50"
    >
      {isPending ? "Retrying…" : "Try again"}
    </button>
  );
}
