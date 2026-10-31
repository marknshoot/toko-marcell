"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import ProductCard from "./ProductCard";
import ProductCardSkeleton from "./ProductCardSkeleton";
import { getProducts, searchProducts, getCategories, postEvent } from "../lib/api";

const PAGE_SIZE = 24;
const DEBOUNCE_MS = 250;

function buildUrl({ department, page, q }) {
  const params = new URLSearchParams();
  if (department && department !== "All") {
    params.set("department", department);
  }
  if (page && page > 1) {
    params.set("page", String(page));
  }
  if (q) {
    params.set("q", q);
  }
  const query = params.toString();
  return query ? `/?${query}` : "/";
}

export default function Catalog() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const department = searchParams.get("department") || "All";
  const page = Math.max(1, Number(searchParams.get("page")) || 1);
  const urlQuery = searchParams.get("q") || "";

  const [searchInput, setSearchInput] = useState(urlQuery);
  const [prevUrlQuery, setPrevUrlQuery] = useState(urlQuery);

  if (urlQuery !== prevUrlQuery) {
    setPrevUrlQuery(urlQuery);
    setSearchInput(urlQuery);
  }

  const [products, setProducts] = useState([]);
  const [total, setTotal] = useState(0);
  const [departments, setDepartments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  useEffect(() => {
    let cancelled = false;

    async function loadFacets() {
      try {
        const data = await getCategories();
        if (!cancelled) {
          setDepartments(data.departments);
        }
      } catch {
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

  useEffect(() => {
    const trimmed = searchInput.trim();
    if (trimmed === urlQuery) {
      return;
    }

    const timer = setTimeout(() => {
      router.replace(buildUrl({ department, page: 1, q: trimmed }), { scroll: false });
    }, DEBOUNCE_MS);

    return () => clearTimeout(timer);
  }, [searchInput, urlQuery, department, router]);

  const lastLoggedQueryRef = useRef("");

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const offset = (page - 1) * PAGE_SIZE;
        const data = urlQuery
          ? await searchProducts({ q: urlQuery, department, limit: PAGE_SIZE, offset })
          : await getProducts({ department, limit: PAGE_SIZE, offset });

        if (!cancelled) {
          setProducts(data.items);
          setTotal(data.total);

          if (urlQuery && urlQuery !== lastLoggedQueryRef.current) {
            lastLoggedQueryRef.current = urlQuery;
            postEvent({ eventType: "search", query: urlQuery, resultsCount: data.total });
          }

          const lastPage = Math.max(1, Math.ceil(data.total / PAGE_SIZE));
          if (data.items.length === 0 && data.total > 0 && page > lastPage) {
            router.replace(buildUrl({ department, page: lastPage, q: urlQuery }), {
              scroll: false,
            });
          }
        }
      } catch (err) {
        if (!cancelled) {
          setError(err.message || "Could not load products");
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
  }, [department, page, urlQuery, router]);

  function selectDepartment(name) {
    router.push(buildUrl({ department: name, page: 1, q: urlQuery }), { scroll: false });
  }

  function goToPage(nextPage) {
    router.push(buildUrl({ department, page: nextPage, q: urlQuery }), { scroll: false });
    const catalogElement = document.getElementById("catalog");
    if (catalogElement) {
      catalogElement.scrollIntoView({ behavior: "smooth" });
    }
  }

  function clearQuery() {
    setSearchInput("");
    router.replace(buildUrl({ department, page: 1, q: "" }), { scroll: false });
  }

  return (
    <section id="catalog" className="border-border py-12">
      <div className="mx-auto max-w-6xl px-7">
        <p className="text-xs font-medium uppercase tracking-[0.12em] text-muted">
          Catalog
        </p>
        <h2 className="mt-2 text-3xl font-semibold tracking-tight text-foreground">
          Shop
        </h2>
        <p className="mt-2 text-muted">
          {urlQuery
            ? `${total.toLocaleString()} result${total === 1 ? "" : "s"} for “${urlQuery}”`
            : `${total.toLocaleString()} products · filter by department or search`}
        </p>

        <div className="mt-6 flex flex-col gap-3 sm:flex-row sm:items-center">
          <div className="relative flex-1">
            <input
              type="search"
              placeholder="Try: levis 501, white sneakers, waterproof jacket…"
              value={searchInput}
              onChange={(event) => setSearchInput(event.target.value)}
              className="w-full rounded-full border border-border bg-surface px-4 py-2.5 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-foreground/20"
            />
          </div>
          {searchInput ? (
            <button
              type="button"
              onClick={clearQuery}
              className="shrink-0 rounded-full border border-border bg-surface px-4 py-2.5 text-xs font-medium text-muted"
            >
              Clear
            </button>
          ) : null}
        </div>

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

        <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
          {loading ? (
            <>
              <span className="sr-only" role="status">
                Loading products
              </span>
              {Array.from({ length: 24 }, (_, index) => (
                <ProductCardSkeleton key={index} />
              ))}
            </>
          ) : error ? (
            <p className="text-muted">{error}</p>
          ) : products.length === 0 ? (
            <p className="text-muted">
              {urlQuery ? `Nothing matched “${urlQuery}”.` : "No products found."}
            </p>
          ) : (
            products.map((product) => (
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
              Prev
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
              Next
            </button>
          </div>
        ) : null}
      </div>
    </section>
  );
}
