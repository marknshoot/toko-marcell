"use client";

import { useEffect, useRef } from "react";
import { postEvent } from "../lib/api";

// Fires `view_product` when a product page is actually opened in a browser.
//
// It lives in its own component because the PDP is a server component: that code
// runs on the server, where there is no session id and no browser to report from.
export default function TrackView({ asin, priceIdr }) {
  // Track by ASIN so navigation between products records each view,
  // while development StrictMode double-invocations are prevented.
  const lastTrackedAsin = useRef(null);

  useEffect(() => {
    if (!asin || lastTrackedAsin.current === asin) {
      return;
    }
    lastTrackedAsin.current = asin;
    postEvent({ eventType: "view_product", asin, priceIdr });
  }, [asin, priceIdr]);

  return null;
}
