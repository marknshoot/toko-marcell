"use client";

import { useEffect, useState } from "react";
import { checkHealth } from "../lib/api";

export default function ServerWakeup() {
  const [status, setStatus] = useState("idle"); // "idle" | "waking" | "ready" | "error"

  useEffect(() => {
    let cancelled = false;
    let wakeTimer = null;

    async function wake() {
      // If server doesn't respond within 600ms, it's in cold start -> show indicator
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
    <aside
      aria-label="Server status indicator"
      className="fixed bottom-5 left-4 z-40 max-w-[calc(100%-6rem)] sm:left-6 sm:max-w-sm rounded-full border border-border bg-surface/95 px-4 py-2.5 shadow-lg backdrop-blur-xs transition-all duration-300"
    >
      <div className="flex items-center gap-3">
        {status === "waking" && (
          <>
            <span className="relative flex h-4 w-4 shrink-0" aria-hidden="true">
              <span className="h-4 w-4 animate-spin rounded-full border-2 border-foreground border-t-transparent" />
            </span>
            <div className="flex flex-col min-w-0">
              <span className="text-xs font-semibold text-foreground truncate">
                Membangunkan server...
              </span>
              <span className="text-[10px] text-muted truncate">
                Render free tier cold start (~30-50 dtk)
              </span>
            </div>
          </>
        )}

        {status === "ready" && (
          <>
            <span className="relative flex h-2.5 w-2.5 shrink-0" aria-hidden="true">
              <span className="h-2.5 w-2.5 rounded-full bg-emerald-500" />
            </span>
            <span className="text-xs font-medium text-foreground">
              Server siap &amp; aktif!
            </span>
          </>
        )}

        {status === "error" && (
          <>
            <span className="h-2.5 w-2.5 shrink-0 rounded-full bg-amber-500" aria-hidden="true" />
            <div className="flex items-center gap-2">
              <span className="text-xs font-medium text-foreground">
                Server sedang bangun...
              </span>
              <button
                type="button"
                onClick={() => {
                  setStatus("waking");
                  checkHealth().then((ok) => setStatus(ok ? "ready" : "error"));
                }}
                className="text-[10px] font-semibold text-foreground underline hover:text-muted cursor-pointer"
              >
                Cek lagi
              </button>
            </div>
          </>
        )}
      </div>
    </aside>
  );
}
