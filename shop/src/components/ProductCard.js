import Link from "next/link";
import { formatRp } from "../lib/formatRp";
import ProductImage from "./ProductImage";

export default function ProductCard({ id, title, priceIdr, category, brand, imageUrl }) {
  return (
    <article className="overflow-hidden rounded-lg border border-border bg-surface">
      <Link href={`/product/${id}`} className="block no-underline">
        <ProductImage src={imageUrl} alt={title} />

        <div className="p-3">
          {/* line-clamp-2 keeps long real titles (up to 199 chars in this
              catalog) to two lines and adds the ellipsis itself. */}
          <p className="line-clamp-2 text-sm font-semibold leading-snug text-foreground">
            {title}
          </p>

          {/* filter(Boolean) drops empty values, so a product with no brand
              shows just its category instead of a stray "·" */}
          <p className="mt-1 truncate text-xs text-muted">
            {[brand, category].filter(Boolean).join(" · ")}
          </p>

          <p className="mt-1 text-sm text-foreground">{formatRp(priceIdr)}</p>
        </div>
      </Link>
    </article>
  );
}
