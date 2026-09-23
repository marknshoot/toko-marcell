import { Suspense } from "react";
import Catalog from "@/components/Catalog";
import CatalogSkeleton from "@/components/CatalogSkeleton";

export default function Home() {
  return (
    <main>
      <section className="py-12 md:py-16">
        <div className="mx-auto max-w-2xl px-7">
          <p className="text-xs font-medium uppercase tracking-[0.12em] text-muted">
            Multi-brand fashion · 6,000 products
          </p>
          <h1 className="mt-2 text-7xl font-semibold leading-[1.1] tracking-tight text-foreground md:text-8xl">
            Toko Marcell
          </h1>
          <p className="mt-4 max-w-lg text-base leading-relaxed text-muted md:text-lg md:mt-3">
            Browse a 6,000-product fashion catalog with hybrid search, session
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

      {/*
        Catalog reads the filter + page from the URL (useSearchParams), which the
        App Router can only resolve on the client, so it needs a Suspense
        boundary. The fallback is what the server sends first.
      */}
      <Suspense fallback={<CatalogSkeleton />}>
        <Catalog />
      </Suspense>
    </main>
  );
}
