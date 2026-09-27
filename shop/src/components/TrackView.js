"use client";

import { useEffect, useRef } from "react";
import { postEvent } from "../lib/api";

export default function TrackView({ asin, priceIdr }) {
  const lastTrackedAsinRef = useRef(null);

  useEffect(() => {
    if (!asin || lastTrackedAsinRef.current === asin) {
      return;
    }
    lastTrackedAsinRef.current = asin;
    postEvent({ eventType: "view_product", asin, priceIdr });
  }, [asin, priceIdr]);

  return null;
}
