"use client";

import { useEffect, useState } from "react";
import { checkHealth } from "../lib/api";

export default function ServerWakeup() {
  const [status, setStatus] = useState("idle");

  useEffect(() => {
    let cancelled = false;
    let wakeTimer = null;

    async function wake() {
      wakeTimer = setTimeout(() => {
        if (!cancelled) {
          setStatus("waking");
        }
      }, 600);

      const ok = await checkHealth();

      if (cancelled) return;
      if (wakeTimer) clearTimeout(wakeTimer);

      if (ok) {
        setStatus("ready");
        const hideTimer = setTimeout(() => {
          if (!cancelled) {
            setStatus("idle");
          }
        }, 2500);
        return () => clearTimeout(hideTimer);
      } else {
        setStatus("error");
      }
    }

    wake();

    return () => {
      cancelled = true;
      if (wakeTimer) clearTimeout(wakeTimer);
    };
  }, []);

  if (status === "idle") return null;

  return (
    <div
      role="status"
      aria-label="Server status indicator"
      className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-background/70 backdrop-blur-md transition-all duration-300"
    >
      <div className="flex flex-col items-center justify-center gap-4 text-center px-6 py-8 rounded-2xl bg-surface/80 border border-border/60 shadow-xl backdrop-blur-sm max-w-sm mx-4">
        {status === "waking" && (
          <>
            {/* Animasi lingkaran loading (spinner) */}
            <div
              className="h-12 w-12 animate-spin rounded-full border-4 border-muted/20 border-t-foreground"
              aria-hidden="true"
            />
            <div className="flex flex-col items-center gap-1.5">
              <span className="text-base font-semibold text-foreground">
                Membangunkan server...
              </span>
              <span className="text-xs text-muted">
                Render free tier cold start (~30-50 dtk)
              </span>
            </div>
          </>
        )}

        {status === "ready" && (
          <>
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-emerald-500/10 text-emerald-600">
              <svg
                className="h-6 w-6"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2.5}
                aria-hidden="true"
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
              </svg>
            </div>
            <div className="flex flex-col items-center gap-1">
              <span className="text-base font-semibold text-foreground">
                Server siap &amp; aktif!
              </span>
              <span className="text-xs text-muted">
                Katalog siap digunakan.
              </span>
            </div>
          </>
        )}

        {status === "error" && (
          <>
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-amber-500/10 text-amber-600">
              <svg
                className="h-6 w-6"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2}
                aria-hidden="true"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
                />
              </svg>
            </div>
            <div className="flex flex-col items-center gap-2">
              <span className="text-sm font-semibold text-foreground">
                Server sedang bangun...
              </span>
              <p className="text-xs text-muted">
                Server membutuhkan waktu lebih lama untuk menyala.
              </p>
              <div className="mt-2 flex items-center gap-3">
                <button
                  type="button"
                  onClick={() => {
                    setStatus("waking");
                    checkHealth().then((ok) => setStatus(ok ? "ready" : "error"));
                  }}
                  className="rounded-md bg-foreground px-4 py-1.5 text-xs font-medium text-background hover:opacity-90 cursor-pointer transition-opacity"
                >
                  Coba lagi
                </button>
                <button
                  type="button"
                  onClick={() => setStatus("idle")}
                  className="rounded-md border border-border px-3 py-1.5 text-xs font-medium text-muted hover:text-foreground cursor-pointer transition-colors"
                >
                  Tutup
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
