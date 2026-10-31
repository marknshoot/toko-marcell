"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { checkHealth } from "../lib/api";

// Cold start overlay timings.  Render's free tier and Neon's scale-to-zero
// both take tens of seconds, so the overlay only appears when a probe is slow
// and then keeps polling until both services answer.
const WAKE_INDICATOR_DELAY_MS = 600;
const POLL_INTERVAL_MS = 2500;
const MAX_WAIT_MS = 65000;
const READY_HIDE_DELAY_MS = 2500;

function clearTimer(timerRef, key) {
  const id = timerRef.current[key];
  if (id) {
    clearTimeout(id);
    timerRef.current[key] = null;
  }
}

/** Drop every pending timer and release a poll loop parked in sleep(). */
function cancelPending(timerRef) {
  clearTimer(timerRef, "wake");
  clearTimer(timerRef, "hide");
  clearTimer(timerRef, "poll");
  const release = timerRef.current.release;
  timerRef.current.release = null;
  if (release) release();
}

export default function ServerWakeup() {
  const [status, setStatus] = useState("idle");

  const statusRef = useRef("idle");
  const runIdRef = useRef(0);
  // wake: cold-start indicator delay, hide: ready → idle fade out,
  // poll: sleep between probes, release: resolver of that sleep.
  const timerRef = useRef({ wake: null, hide: null, poll: null, release: null });

  const updateStatus = useCallback((next) => {
    statusRef.current = next;
    setStatus(next);
  }, []);

  /** Cancel the in-flight poll loop and leave the overlay idle. */
  const stop = useCallback(() => {
    runIdRef.current += 1;
    cancelPending(timerRef);
  }, []);

  const wake = useCallback(async () => {
    // Bumping the run id cancels any loop still running from a previous call
    // (mount, or the "Coba lagi" retry button).
    const runId = ++runIdRef.current;
    const isActive = () => runId === runIdRef.current;

    const sleep = (ms) =>
      new Promise((resolve) => {
        timerRef.current.release = resolve;
        timerRef.current.poll = setTimeout(() => {
          timerRef.current.poll = null;
          timerRef.current.release = null;
          resolve();
        }, ms);
      });

    cancelPending(timerRef);

    if (statusRef.current === "error") {
      // Retry: give instant feedback instead of waiting out the cold-start delay.
      updateStatus("waking");
    } else {
      timerRef.current.wake = setTimeout(() => {
        timerRef.current.wake = null;
        if (isActive()) {
          updateStatus("waking");
        }
      }, WAKE_INDICATOR_DELAY_MS);
    }

    const deadline = Date.now() + MAX_WAIT_MS;

    while (isActive() && Date.now() < deadline) {
      let res;
      try {
        res = await checkHealth();
      } catch {
        res = false;
      }

      if (!isActive()) return;

      // checkHealth() returns { ok, api, database, data }; older booleans
      // (and test doubles) are still understood.
      const isReady = typeof res === "boolean" ? res : Boolean(res?.ok);

      if (isReady) {
        // A fast answer means there was no cold start, so status is still
        // "idle" and the overlay never flashes.
        if (timerRef.current.wake) {
          clearTimer(timerRef, "wake");
        }
        if (statusRef.current === "waking") {
          updateStatus("ready");
          timerRef.current.hide = setTimeout(() => {
            timerRef.current.hide = null;
            if (isActive()) {
              updateStatus("idle");
            }
          }, READY_HIDE_DELAY_MS);
        }
        return;
      }

      if (Date.now() >= deadline) break;
      await sleep(POLL_INTERVAL_MS);
    }

    if (!isActive()) return;
    clearTimer(timerRef, "wake");
    updateStatus("error");
  }, [updateStatus]);

  useEffect(() => {
    wake();
    return stop;
  }, [wake, stop]);

  if (status === "idle") return null;

  return (
    <div
      role="status"
      aria-label="Server status indicator"
      className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-background/70 backdrop-blur-md transition-all duration-300"
    >
      <div className="flex flex-col items-center justify-center gap-4 text-center max-w-sm mx-4">
        {status === "waking" && (
          <>
            {/* Animasi lingkaran loading (spinner) */}
            <div
              className="h-12 w-12 animate-spin rounded-full border-4 border-muted/20 border-t-foreground"
              aria-hidden="true"
            />
            <div className="flex flex-col items-center gap-1.5">
              <span className="text-base font-semibold text-foreground">
                Membangunkan server &amp; database...
              </span>
              <span className="text-xs text-muted">
                Render API &amp; Neon Postgres cold start (~30–50 dtk pada free tier)
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
                Server &amp; Database siap &amp; aktif!
              </span>
              <span className="text-xs text-muted">
                Katalog &amp; AI Copilot siap digunakan.
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
                Server atau database sedang bangun...
              </span>
              <p className="text-xs text-muted">
                Render atau Neon Serverless Postgres membutuhkan waktu lebih lama
                untuk menyala.
              </p>
              <div className="mt-2 flex items-center gap-3">
                <button
                  type="button"
                  onClick={() => {
                    wake();
                  }}
                  className="rounded-md bg-foreground px-4 py-1.5 text-xs font-medium text-background hover:opacity-90 cursor-pointer transition-opacity"
                >
                  Coba lagi
                </button>
                <button
                  type="button"
                  onClick={() => {
                    stop();
                    updateStatus("idle");
                  }}
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
