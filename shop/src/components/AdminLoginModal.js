"use client";

import { useEffect, useId, useRef, useState } from "react";
import { adminLogin } from "@/lib/api";

const DEMO_USERNAME = "admin";
const DEMO_PASSWORD = "marcell-admin-2026";

export default function AdminLoginModal({
  open = true,
  onClose,
  onSuccess,
  dismissible = true,
  initialError = "",
}) {
  const titleId = useId();
  const descriptionId = useId();
  const usernameId = useId();
  const passwordId = useId();
  const usernameRef = useRef(null);

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(initialError);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    queueMicrotask(() => setError(initialError));
  }, [initialError]);

  useEffect(() => {
    if (open) {
      usernameRef.current?.focus();
    }
  }, [open]);

  useEffect(() => {
    if (!open || !dismissible) {
      return undefined;
    }

    function handleKeyDown(event) {
      if (event.key === "Escape") {
        onClose?.();
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open, dismissible, onClose]);

  async function submit(credentials) {
    setSubmitting(true);
    setError("");
    try {
      const session = await adminLogin(credentials);
      onSuccess?.(session);
    } catch (loginError) {
      setError(loginError.message || "Could not sign in. Check the credentials.");
    } finally {
      setSubmitting(false);
    }
  }

  function handleSubmit(event) {
    event.preventDefault();
    if (!username.trim() || !password) {
      setError("Enter both a username and a password.");
      return;
    }
    submit({ username: username.trim(), password });
  }

  function handleDemoLogin() {
    setUsername(DEMO_USERNAME);
    setPassword(DEMO_PASSWORD);
    submit({ username: DEMO_USERNAME, password: DEMO_PASSWORD });
  }

  if (!open) {
    return null;
  }

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center overflow-y-auto bg-black/30 px-4 py-8 backdrop-blur-sm"
      onMouseDown={(event) => {
        if (dismissible && event.target === event.currentTarget) {
          onClose?.();
        }
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        className="w-full max-w-md rounded-2xl border border-border bg-surface p-7 shadow-2xl"
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-muted">
              Toko Marcell · Operations
            </p>
            <h2
              id={titleId}
              className="mt-2 text-2xl font-semibold tracking-tight text-foreground"
            >
              Admin Dashboard Login
            </h2>
          </div>
          {dismissible ? (
            <button
              type="button"
              aria-label="Close login dialog"
              onClick={onClose}
              className="rounded-full border border-border px-2.5 py-1 text-sm text-muted transition hover:bg-brand-soft hover:text-foreground"
            >
              ✕
            </button>
          ) : null}
        </div>

        <p id={descriptionId} className="mt-2 text-sm text-muted">
          Executive KPI, operations telemetry, and unit economics.
        </p>

        <button
          type="button"
          onClick={handleDemoLogin}
          disabled={submitting}
          className="mt-6 flex w-full items-center justify-center gap-2 rounded-xl bg-cta px-5 py-3 text-sm font-semibold text-white transition hover:bg-cta-hover disabled:cursor-not-allowed disabled:opacity-50"
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
            className="h-4 w-4"
          >
            <path d="M12 3l7 3v5c0 4.5-3 7.5-7 10-4-2.5-7-5.5-7-10V6l7-3z" />
            <path d="M9 12l2 2 4-4" />
          </svg>
          {submitting ? "Signing in…" : "Demo Admin (1-Click)"}
        </button>

        <div className="my-5 flex items-center gap-3 text-[11px] font-semibold uppercase tracking-[0.14em] text-muted">
          <span className="h-px flex-1 bg-border" />
          or sign in manually
          <span className="h-px flex-1 bg-border" />
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label
              htmlFor={usernameId}
              className="block text-sm font-medium text-foreground"
            >
              Username
            </label>
            <input
              ref={usernameRef}
              id={usernameId}
              name="username"
              type="text"
              autoComplete="username"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              className="mt-1.5 w-full rounded-lg border border-border bg-background px-3 py-2.5 text-sm text-foreground outline-none transition focus:border-foreground"
            />
          </div>

          <div>
            <label
              htmlFor={passwordId}
              className="block text-sm font-medium text-foreground"
            >
              Password
            </label>
            <input
              id={passwordId}
              name="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className="mt-1.5 w-full rounded-lg border border-border bg-background px-3 py-2.5 text-sm text-foreground outline-none transition focus:border-foreground"
            />
          </div>

          {error ? (
            <p
              role="alert"
              className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800"
            >
              {error}
            </p>
          ) : null}

          <button
            type="submit"
            disabled={submitting}
            className="w-full rounded-full border border-border bg-surface px-5 py-2.5 text-sm font-medium text-foreground transition hover:bg-brand-soft disabled:cursor-not-allowed disabled:opacity-50"
          >
            {submitting ? "Checking credentials…" : "Sign In"}
          </button>
        </form>

        <p className="mt-4 text-xs leading-relaxed text-muted">
          Access tokens expire after 15 minutes. The refresh token is stored in an
          HttpOnly cookie and rotated on every refresh.
        </p>
      </div>
    </div>
  );
}
