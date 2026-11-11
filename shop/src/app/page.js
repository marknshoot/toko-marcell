import { Suspense } from "react";
import CatalogFilters from "@/components/CatalogFilters";
import CatalogFiltersSkeleton from "@/components/CatalogFiltersSkeleton";
import CatalogResults from "@/components/CatalogResults";
import CatalogResultsSkeleton from "@/components/CatalogResultsSkeleton";
import CatalogSearch from "@/components/CatalogSearch";
import RecommendationsRail from "@/components/RecommendationsRail";
import { parseCatalogParams } from "@/lib/catalogParams";

// The route reads searchParams, so it renders per request; the catalog fetches
// themselves are cached for 5 minutes in lib/api.js. Keep the two in sync.
export const revalidate = 300;

export async function generateMetadata({ searchParams }) {
  const { q } = parseCatalogParams(await searchParams);

  if (!q) {
    return {};
  }

  // Internal search-result pages are shareable but should not be indexed:
  // there are infinitely many query URLs and they are thin duplicates of the
  // catalog. follow: true still lets crawlers walk the product links.
  return {
    title: `${q} — Toko Marcell`,
    robots: { index: false, follow: true },
  };
}

export default async function Home({ searchParams }) {
  const { department, page, q } = parseCatalogParams(await searchParams);

  return (
    <main>
      <section className="py-12 md:py-16">
        <div className="mx-auto max-w-6xl px-7">
          <p className="text-xs font-medium uppercase tracking-[0.12em] text-muted">
            Multi-brand fashion · 4,670 verified products
          </p>
          <h1 className="mt-2 text-7xl font-semibold leading-[1.1] tracking-tight text-foreground md:text-8xl">
            Toko Marcell
          </h1>
          <p className="mt-4 max-w-lg text-base leading-relaxed text-muted md:text-lg md:mt-3">
            Browse a 4,670-product curated fashion catalog with hybrid search, session
            recommendations and a QRIS demo checkout.
          </p>
          <div className="mt-8">
            <a
              href="#catalog"
              className="inline-flex items-center justify-center rounded-full bg-cta px-4 py-3 text-sm font-medium text-white no-underline transition hover:bg-cta-hover"
            >
              Browse catalog
            </a>
          </div>
        </div>
      </section>

      <RecommendationsRail />

      <section id="catalog" className="border-border py-12">
        <div className="mx-auto max-w-6xl px-7">
          <p className="text-xs font-medium uppercase tracking-[0.12em] text-muted">
            Catalog
          </p>
          <h2 className="mt-2 text-3xl font-semibold tracking-tight text-foreground">
            Shop
          </h2>

          <CatalogSearch q={q} department={department} />

          <Suspense fallback={<CatalogFiltersSkeleton />}>
            <CatalogFilters department={department} q={q} />
          </Suspense>

          <Suspense fallback={<CatalogResultsSkeleton />}>
            <CatalogResults department={department} page={page} q={q} />
          </Suspense>
        </div>
      </section>
    </main>
  );
}
