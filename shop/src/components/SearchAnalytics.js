"use client";

import { useEffect, useRef } from "react";
import { postEvent } from "../lib/api";

// The session id lives in sessionStorage, so the search event has to be sent
// from the browser. The ref keeps StrictMode's double effect (and re-renders of
// the same result set) from logging the same query twice.
export default function SearchAnalytics({ query, resultsCount }) {
  const lastLogged = useRef("");

  useEffect(() => {
    const normalized = (query || "").trim();
    if (!normalized || normalized === lastLogged.current) {
      return;
    }

    lastLogged.current = normalized;
    postEvent({ eventType: "search", query: normalized, resultsCount });
  }, [query, resultsCount]);

  return null;
}
