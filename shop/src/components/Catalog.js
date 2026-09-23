"use client";

import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import ProductCard from "./ProductCard";
import ProductCardSkeleton from "./ProductCardSkeleton";
import { getProducts, getCategories } from "../lib/api";

const PAGE_SIZE = 24;

// Build the page URL from the current filter + page. URLSearchParams is the same
// tool lib/api.js uses for the API query — here it builds the *page* URL instead.
function buildUrl(department, page) {
  const params = new URLSearchParams();
  if (department !== "All") {
    params.set("department", department);
  }
  if (page > 1) {
    params.set("page", String(page));
  }
  const query = params.toString();
  return query ? `/?${query}` : "/";
}

export default function Catalog() {
  const router = useRouter();
  const searchParams = useSearchParams();

  // The URL is the single source of truth for filter + page. Nothing here is
  // duplicated in useState, so the browser Back button, a shared link and a
  // page refresh all show the same thing.
  const department = searchParams.get("department") || "All";
  const page = Math.max(1, Number(searchParams.get("page")) || 1);

  const [searchQuery, setSearchQuery] = useState("");
  const [products, setProducts] = useState([]);
  const [total, setTotal] = useState(0);
  const [departments, setDepartments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const [debouncedQuery, setDebouncedQuery] = useState("");

  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedQuery(searchQuery);
    }, 200); // 200ms debounce delay
    
    return () => clearTimeout(timer);
  }, [searchQuery]);

  // Facets: fetched once — they describe the catalog, not the current page.
  useEffect(() => {
    let cancelled = false;

    async function loadFacets() {
      try {
        const data = await getCategories();
        if (!cancelled) {
          setDepartments(data.departments);
        }
      } catch (err) {
        // chips are a nice-to-have: if they fail, the catalog must still work
        if (!cancelled) {
          setDepartments([]);
        }
      }
    }

    loadFacets();
    return () => {
      cancelled = true;
    };
  }, []);

  // Items: refetched whenever the filter or the page changes.
  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const data = await getProducts({
          department,
          limit: PAGE_SIZE,
          offset: (page - 1) * PAGE_SIZE,
        });
        if (!cancelled) {
          setProducts(data.items);
          setTotal(data.total);

          // A stale link can point past the end (e.g. ?department=Girls&page=99).
          // Snap to the last real page instead of showing an empty grid.
          const lastPage = Math.max(1, Math.ceil(data.total / PAGE_SIZE));
          if (data.items.length === 0 && data.total > 0 && page > lastPage) {
            router.replace(buildUrl(department, lastPage), { scroll: false });
          }
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
  }, [department, page, router]);

  function selectDepartment(name) {
    // changing the filter must go back to page 1, otherwise page 40 of a
    // 5-page department would show nothing
    router.push(buildUrl(name, 1), { scroll: false });
  }

  function goToPage(nextPage) {
    router.push(buildUrl(department, nextPage), { scroll: false });
  }

  let visibleProducts = products;
  const q = debouncedQuery.trim().toLowerCase();
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
          {total > 0
            ? `${total.toLocaleString()} products · filter by department or type to search`
            : "Filter by department or type to search (updates as you type)."}
        </p>

        <input
          type="search"
          placeholder="Try: jeans, sneakers, dress, watch…"
          value={searchQuery}
          onChange={(event) => setSearchQuery(event.target.value)}
          className="mt-6 w-full rounded-full border border-border bg-surface px-4 py-2.5 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-foreground/20"
        />

        <div className="mt-4 flex flex-wrap gap-2">
          {[{ name: "All", count: null }, ...departments].map((dept) => {
            const isSelected = dept.name === department;
            return (
              <button
                key={dept.name}
                type="button"
                onClick={() => selectDepartment(dept.name)}
                className={
                  isSelected
                    ? "rounded-full bg-foreground px-3 py-1.5 text-xs font-medium text-white"
                    : "rounded-full border border-border bg-surface px-3 py-1.5 text-xs font-medium text-muted"
                }
              >
                {dept.name}
                {dept.count !== null ? ` (${dept.count})` : ""}
              </button>
            );
          })}
        </div>

        <div className="mt-6 grid grid-cols-2 gap-3">
          {loading ? (
            // Skeleton cards instead of a "Loading…" line: the grid keeps its
            // shape, so nothing jumps when the real products arrive.
            <>
              <span className="sr-only" role="status">
                Loading products
              </span>
              {Array.from({ length: 6 }, (_, index) => (
                <ProductCardSkeleton key={index} />
              ))}
            </>
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
                brand={product.brand}
                imageUrl={product.imageUrl}
              />
            ))
          )}
        </div>

        {!loading && !error && total > 0 ? (
          <div className="mt-6 flex items-center justify-between gap-3">
            <button
              type="button"
              onClick={() => goToPage(page - 1)}
              disabled={page <= 1}
              className="rounded-full border border-border bg-surface px-4 py-2 text-xs font-medium text-foreground disabled:cursor-not-allowed disabled:opacity-40"
            >
              ← Prev
            </button>

            <p className="text-xs text-muted">
              Page {page} of {totalPages} · {total.toLocaleString()} items
            </p>

            <button
              type="button"
              onClick={() => goToPage(page + 1)}
              disabled={page >= totalPages}
              className="rounded-full border border-border bg-surface px-4 py-2 text-xs font-medium text-foreground disabled:cursor-not-allowed disabled:opacity-40"
            >
              Next →
            </button>
          </div>
        ) : null}
      </div>
    </section>
  );
}
