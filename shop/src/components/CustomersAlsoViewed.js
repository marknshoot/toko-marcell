import { getItemRecs } from "@/lib/api";
import ProductCard from "./ProductCard";

// Item-to-item recommendations, fetched inside their own Suspense boundary: a slow
// or empty rail must not delay the product details above it.
export default async function CustomersAlsoViewed({ asin }) {
  const recs = await getItemRecs(asin, 4);
  const items = recs?.items ?? [];

  if (items.length === 0) {
    return null;
  }

  return (
    <section className="mt-12 border-t border-border pt-8">
      <div className="flex items-baseline justify-between">
        <h2 className="text-xs font-semibold uppercase tracking-[0.12em] text-foreground">
          Customers Also Viewed
        </h2>
        <span className="text-xs text-muted">
          {recs.strategy === "item_cf"
            ? "Based on co-purchases & views"
            : "Popular in this category"}
        </span>
      </div>
      <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
        {items.map((rec) => (
          <ProductCard key={rec.id} {...rec} />
        ))}
      </div>
    </section>
  );
}
