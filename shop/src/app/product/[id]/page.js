import { Suspense } from "react";
import { notFound } from "next/navigation";
import { formatRp } from "../../../lib/formatRp";
import { getProduct } from "../../../lib/api";
import AddToCartButton from "@/components/AddToCartButton";
import CustomersAlsoViewed from "@/components/CustomersAlsoViewed";
import ProductCopilotEntry from "@/components/ProductCopilotEntry";
import ProductImage from "@/components/ProductImage";
import ProductReviewsSection from "@/components/ProductReviewsSection";
import RecsRailSkeleton from "@/components/RecsRailSkeleton";
import ReviewsSkeleton from "@/components/ReviewsSkeleton";
import TrackView from "@/components/TrackView";

// Incremental Static Regeneration (ISR): cache at the edge and revalidate in the
// background every 5 minutes. Keep this in sync with PRODUCT_REVALIDATE_SECONDS in
// lib/api.js, which is what actually lets the three fetches below be cached.
export const revalidate = 300;

// No product pages are prerendered at build time; this only opts the dynamic segment
// into on-demand ISR, so the first visit renders and the result is cached for 5 minutes.
export async function generateStaticParams() {
  return [];
}

export async function generateMetadata({ params }) {
  const { id } = await params;
  const product = await getProduct(id);

  if (!product) {
    // loading.js flushes the shell (and the 200 status) before the page can call
    // notFound(), so a missing product is a soft 404: keep it out of the index.
    return {
      title: "Product not found — Toko Marcell",
      robots: { index: false, follow: false },
    };
  }

  return {
    title: `${product.title} — Toko Marcell`,
    description: [
      product.brand,
      product.category,
      product.priceIdr ? formatRp(product.priceIdr) : null,
    ]
      .filter(Boolean)
      .join(" · "),
  };
}

export default async function ProductPage({ params }) {
  const { id } = await params;
  const product = await getProduct(id);

  if (!product) {
    notFound();
  }

  const breadcrumb = (product.categoryPath || []).join(" › ");
  const features = product.features || [];

  return (
    <main className="py-12">
      <TrackView asin={product.asin} priceIdr={product.priceIdr} />
      <div className="mx-auto max-w-6xl px-7">
        <div className="mt-6 grid gap-8 md:grid-cols-2">
          <ProductImage src={product.imageUrl} alt={product.title} padding="p-6" />

          <div>
            {breadcrumb ? (
              <p className="text-xs font-medium uppercase tracking-[0.12em] text-muted">
                {breadcrumb}
              </p>
            ) : null}

            <h1 className="mt-2 text-3xl font-semibold tracking-tight text-foreground">
              {product.title}
            </h1>

            {product.brand ? (
              <p className="mt-1 text-sm text-muted">{product.brand}</p>
            ) : null}

            {product.avgRating !== null && product.avgRating !== undefined ? (
              <p className="mt-3 text-sm text-foreground">
                <span aria-hidden="true">★</span> {product.avgRating.toFixed(1)}
                <span className="text-muted">
                  {" "}
                  · {product.ratingCount.toLocaleString()} reviews
                </span>
              </p>
            ) : null}

            <p className="mt-4 text-lg text-foreground">
              {formatRp(product.priceIdr)}
            </p>

            <AddToCartButton product={product} />

            <div className="mt-4">
              <ProductCopilotEntry asin={product.asin} title={product.title} />
            </div>
          </div>
        </div>

        {features.length > 0 ? (
          <section className="mt-10 border-t border-border pt-6">
            <h2 className="text-xs font-medium uppercase tracking-[0.12em] text-muted">
              Details
            </h2>
            <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-foreground">
              {features.map((feature) => (
                <li key={feature}>{feature}</li>
              ))}
            </ul>
          </section>
        ) : null}

        {product.description ? (
          <section className="mt-8 border-t border-border pt-6">
            <h2 className="text-xs font-medium uppercase tracking-[0.12em] text-muted">
              Description
            </h2>
            <p className="mt-3 text-sm leading-relaxed text-muted">
              {product.description}
            </p>
          </section>
        ) : null}

        {/* Reviews and recommendations stream in after the product is painted, so a
            slow reviews query or an empty rail never delays what the shopper clicked. */}
        <Suspense fallback={<ReviewsSkeleton />}>
          <ProductReviewsSection productId={product.id} />
        </Suspense>

        <Suspense fallback={<RecsRailSkeleton />}>
          <CustomersAlsoViewed asin={product.asin} />
        </Suspense>
      </div>
    </main>
  );
}
