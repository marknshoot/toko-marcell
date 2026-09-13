"use client";

import { useEffect, useState } from "react";
import ProductCard from "./ProductCard";
import { getProducts } from "../lib/api";

const CATEGORIES = ["All", "Tops", "Bottoms", "Accessories"];

export default function Catalog() {
  const [selectedCategory, setSelectedCategory] = useState("All");
  const [searchQuery, setSearchQuery] = useState("");
  const [products, setProducts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const data = await getProducts({ category: selectedCategory });
        if (!cancelled) {
          setProducts(data.items);
        }
      } catch (err) {
        if (!cancelled) {
          setError("Could not load products");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [selectedCategory]);

  let visibleProducts = products;
  const q = searchQuery.trim().toLowerCase();
  if (q !== "") {
    visibleProducts = visibleProducts.filter((product) =>
      product.title.toLowerCase().includes(q)
    );
  }

  return (
    <section id="catalog" className="border-t border-border py-12">
      <div className="mx-auto max-w-2xl px-7">
        <p className="text-xs font-medium uppercase tracking-[0.12em] text-muted">
          Catalog
        </p>
        <h2 className="mt-2 text-3xl font-semibold tracking-tight text-foreground">
          Shop
        </h2>
        <p className="mt-2 text-muted">
          Filter by category or type to search (updates as you type).
        </p>

        <input
          type="search"
          placeholder="Try: tee, linen, chino, tote…"
          value={searchQuery}
          onChange={(event) => setSearchQuery(event.target.value)}
          className="mt-6 w-full rounded-full border border-border bg-surface px-4 py-2.5 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-foreground/20"
        />

        <div className="mt-4 flex flex-wrap gap-2">
          {CATEGORIES.map((cat) => {
            const isSelected = cat === selectedCategory;
            return (
              <button
                key={cat}
                type="button"
                onClick={() => setSelectedCategory(cat)}
                className={
                  isSelected
                    ? "rounded-full bg-foreground px-3 py-1.5 text-xs font-medium text-white"
                    : "rounded-full border border-border bg-surface px-3 py-1.5 text-xs font-medium text-muted"
                }
              >
                {cat}
              </button>
            );
          })}
        </div>

        <div className="mt-6 grid grid-cols-2 gap-3">
          {loading ? (
            <p className="text-muted">Loading…</p>
          ) : error ? (
            <p className="text-muted">{error}</p>
          ) : visibleProducts.length === 0 ? (
            <p className="text-muted">No products found.</p>
          ) : (
            visibleProducts.map((product) => (
              <ProductCard
                key={product.id}
                id={product.id}
                title={product.title}
                priceIdr={product.priceIdr}
                category={product.category}
              />
            ))
          )}
        </div>
      </div>
    </section>
  );
}
