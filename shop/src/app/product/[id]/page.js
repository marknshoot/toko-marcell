import Link from "next/link";
import { notFound } from "next/navigation";
import { formatRp } from "../../../lib/formatRp";
import { getProduct, getItemRecs, getProductReviews } from "../../../lib/api";
import AddToCartButton from "@/components/AddToCartButton";
import ProductImage from "@/components/ProductImage";
import ProductCard from "@/components/ProductCard";
import ProductReviews from "@/components/ProductReviews";
import TrackView from "@/components/TrackView";

export async function generateMetadata({ params }) {
  const { id } = await params;
  const product = await getProduct(id);

  if (!product) {
    return { title: "Product not found — Toko Marcell" };
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

    const [recsData, reviewsData] = await Promise.all([
        getItemRecs(product.asin, 4),
        getProductReviews(product.id, 10),
    ]);

    const breadcrumb = (product.categoryPath || []).join(" › ");
    const features = product.features || [];

    return (
        <main className="py-12">
            <TrackView asin={product.asin} priceIdr={product.priceIdr} />
            <div className="mx-auto max-w-6xl px-7">
                <Link
                    href="/#catalog"
                    className="text-xs text-muted no-underline transition-colors hover:text-foreground"
                >
                    All products
                </Link>

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

                <ProductReviews reviewsData={reviewsData} />

                {recsData?.items?.length > 0 ? (
                    <section className="mt-12 border-t border-border pt-8">
                        <div className="flex items-baseline justify-between">
                            <h2 className="text-xs font-semibold uppercase tracking-[0.12em] text-foreground">
                                Customers Also Viewed
                            </h2>
                            <span className="text-xs text-muted">
                                {recsData.strategy === "item_cf"
                                    ? "Based on co-purchases & views"
                                    : "Popular in this category"}
                            </span>
                        </div>
                        <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
                            {recsData.items.map((rec) => (
                                <ProductCard key={rec.id} {...rec} />
                            ))}
                        </div>
                    </section>
                ) : null}
            </div>
        </main>
    );
}
