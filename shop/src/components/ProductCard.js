"use client";

import { useContext } from "react";
import Link from "next/link";
import { formatRp } from "../lib/formatRp";
import ProductImage from "./ProductImage";
import { CartContext } from "./CartProvider";

export default function ProductCard({ id, title, priceIdr, category, brand, imageUrl, visualSimilarity }) {
  const { items } = useContext(CartContext) || { items: [] };
  const inCartItem = items?.find((item) => item.id === id);
  const inCartQty = inCartItem ? inCartItem.qty : 0;

  return (
    <article className="overflow-hidden rounded-lg border border-border bg-surface transition-shadow hover:shadow-sm">
      <Link href={`/product/${id}`} className="block no-underline">
        <div className="relative">
          <ProductImage src={imageUrl} alt={title} />
          {inCartQty > 0 ? (
            <span className="absolute top-2 right-2 inline-flex items-center gap-1 rounded-full bg-cta px-2 py-0.5 text-[11px] font-semibold text-white shadow-sm">
              <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
              </svg>
              {inCartQty} in cart
            </span>
          ) : null}
          {visualSimilarity ? (
            <span className="absolute bottom-2 left-2 inline-flex items-center gap-1 rounded-full bg-zinc-900/85 backdrop-blur-sm px-2 py-0.5 text-[10px] font-semibold text-zinc-100 shadow-sm">
              {Math.round(visualSimilarity * 100)}% match
            </span>
          ) : null}
        </div>

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

          <div className="mt-2 flex items-center justify-between gap-2">
            <p className="text-sm font-medium text-foreground">{formatRp(priceIdr)}</p>
            {inCartQty > 0 ? (
              <span className="text-[11px] font-medium text-cta">
                In cart ({inCartQty})
              </span>
            ) : null}
          </div>
        </div>
      </Link>
    </article>
  );
}
