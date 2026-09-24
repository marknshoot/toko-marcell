"use client";

import { useEffect, useState } from "react";
import { getSessionRecs } from "../lib/api";
import ProductCard from "./ProductCard";

export default function RecommendationsRail() {
  const [data, setData] = useState({ items: [], strategy: "cold_popularity" });
  const [loading, setLoading] = useState(true);
  const [visibleCount, setVisibleCount] = useState(4);

  useEffect(() => {
    let cancelled = false;

    async function loadRecs() {
      try {
        const res = await getSessionRecs(20);
        if (!cancelled && res && res.items) {
          setData(res);
        }
      } catch (err) {
        // best effort
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    loadRecs();

    return () => {
      cancelled = true;
    };
  }, []);

  if (loading || !data.items || data.items.length === 0) {
    return null;
  }

  const isPersonalized = data.strategy === "session_cf";
  const visibleItems = data.items.slice(0, visibleCount);

  function handleLoadMore() {
    setVisibleCount((prev) => prev + 4);
  }

  return (
    <section className="border-b border-border bg-surface/50 py-10">
      <div className="mx-auto max-w-6xl px-7">
        <div className="flex flex-col gap-1 sm:flex-row sm:items-baseline sm:justify-between">
          <div className="flex items-center gap-2">
            <h2 className="text-base font-semibold tracking-tight text-foreground md:text-lg">
              {isPersonalized ? "Recommended For You" : "Trending Right Now"}
            </h2>
            {isPersonalized ? (
              <span className="rounded-full bg-cta/10 px-2 py-0.5 text-[10px] font-semibold text-cta">
                Live feedback
              </span>
            ) : null}
          </div>
          <p className="text-xs text-muted">
            {isPersonalized
              ? "Personalized in real-time from your recent browsing clicks"
              : "Popular bestsellers across the catalog"}
          </p>
        </div>

        <div className="mt-5 grid grid-cols-2 gap-4 sm:grid-cols-4">
          {visibleItems.map((product) => (
            <ProductCard key={product.id} {...product} />
          ))}
        </div>

        {visibleCount < data.items.length ? (
          <div className="mt-8 flex justify-center">
            <button
              type="button"
              onClick={handleLoadMore}
              className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface px-5 py-2 text-xs font-medium text-foreground shadow-xs transition-colors hover:border-foreground hover:bg-surface/80 cursor-pointer"
            >
              Load more recommendations ↓
            </button>
          </div>
        ) : null}
      </div>
    </section>
  );
}
