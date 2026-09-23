"use client";

import { useEffect, useRef } from "react";
import { postEvent } from "../lib/api";

// Fires `view_product` when a product page is actually opened in a browser.
//
// It lives in its own component because the PDP is a server component: that code
// runs on the server, where there is no session id and no browser to report from.
export default function TrackView({ asin, priceIdr }) {
  // React runs effects twice on mount in development (StrictMode), which would
  // double every view. The ref keeps it to one event per page view.
  const fired = useRef(false);

  useEffect(() => {
    if (fired.current) {
      return;
    }
    fired.current = true;
    postEvent({ eventType: "view_product", asin, priceIdr });
  }, [asin, priceIdr]);

  return null;
}
