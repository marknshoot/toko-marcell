import { redirect } from "next/navigation";
import { getProducts, searchProducts } from "@/lib/api";
import { PAGE_SIZE, buildCatalogUrl, getTotalPages } from "@/lib/catalogParams";
import CatalogPagination from "./CatalogPagination";
import ProductCard from "./ProductCard";
import RetryButton from "./RetryButton";
import SearchAnalytics from "./SearchAnalytics";

function ErrorState() {
  return (
    <div className="mt-6 rounded-lg border border-border bg-surface p-4">
      <p className="text-sm font-medium text-foreground">
        Could not load products.
      </p>
      <p className="mt-1 text-xs text-muted">
        The catalog backend may be waking up. Try again in a moment.
      </p>
      <RetryButton />
    </div>
  );
}

// The only place the catalog talks to the API now. It sits inside a Suspense
// boundary in app/page.js, so the shell (hero, search box) paints first and the
// grid streams in when the backend answers.
export default async function CatalogResults({ department, page, q }) {
  const offset = (page - 1) * PAGE_SIZE;
  let data = null;

  try {
    data = q
      ? await searchProducts({ q, department, limit: PAGE_SIZE, offset })
      : await getProducts({ department, limit: PAGE_SIZE, offset });
  } catch {
    // A cold Render/Neon backend or the fetch deadline in lib/api.js lands here.
    return <ErrorState />;
  }

  const items = Array.isArray(data?.items) ? data.items : [];
  const total = Number(data?.total) || 0;
  const totalPages = getTotalPages(total);

  // Same behaviour the client catalog had: a stale ?page= link lands on the last
  // real page. redirect() throws, so it stays outside the try/catch above.
  if (items.length === 0 && total > 0 && page > totalPages) {
    redirect(buildCatalogUrl({ department, q, page: totalPages }));
  }

  return (
    <div className="mt-6">
      <p className="text-muted">
        {q
          ? `${total.toLocaleString()} result${total === 1 ? "" : "s"} for “${q}”`
          : `${total.toLocaleString()} products · filter by department or search`}
      </p>

      {items.length === 0 ? (
        <p className="mt-4 text-muted">
          {q ? `Nothing matched “${q}”.` : "No products found."}
        </p>
      ) : (
        <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
          {items.map((product) => (
            <ProductCard key={product.id} {...product} />
          ))}
        </div>
      )}

      {q ? <SearchAnalytics query={q} resultsCount={total} /> : null}

      <CatalogPagination
        department={department}
        q={q}
        page={page}
        totalPages={totalPages}
        total={total}
      />
    </div>
  );
}
