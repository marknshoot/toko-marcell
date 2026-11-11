"use client";

import { useContext } from "react";
import Link from "next/link";
import { formatRp } from "../lib/formatRp";
import ProductImage from "./ProductImage";
import { CartContext } from "./CartProvider";

/**
 * The one product card used by the catalog, both recommendation rails and the
 * copilot chat.
 *
 * ``variant="grid"`` is the vertical card in a product grid. ``variant="chat"``
 * is the compact row inside a copilot answer: it shows the numeric reference
 * badge, links the whole row to the PDP and renders ``children`` as its action
 * buttons, so the chat keeps owning attach/add behaviour.
 */
export default function ProductCard({
  id,
  title,
  priceIdr,
  category,
  brand,
  imageUrl,
  variant = "grid",
  refNumber = null,
  onNavigate = null,
  children = null,
}) {
  const { items } = useContext(CartContext) || { items: [] };

  if (variant === "chat") {
    return (
      <div className="rounded-xl border border-border bg-surface p-2.5 shadow-xs">
        <Link
          href={id ? `/product/${id}` : "#"}
          className="group flex items-start gap-2.5"
          onClick={onNavigate || undefined}
        >
          {refNumber ? (
            <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-foreground text-[10px] font-bold text-background">
              {refNumber}
            </span>
          ) : null}
          <div className="relative h-12 w-12 shrink-0 overflow-hidden rounded-lg border border-border/80 bg-white">
            <ProductImage src={imageUrl} alt={title} padding="p-1" />
          </div>
          <div className="min-w-0 flex-1">
            <p className="line-clamp-1 text-xs font-medium text-foreground group-hover:underline">
              {title}
            </p>
            <div className="flex items-center gap-1.5 text-[11px]">
              <span className="font-semibold text-foreground">{formatRp(priceIdr || 0)}</span>
              {brand ? <span className="text-muted">· {brand}</span> : null}
            </div>
          </div>
        </Link>
        {children ? <div className="mt-2 flex flex-wrap gap-1.5">{children}</div> : null}
      </div>
    );
  }

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
        </div>

        <div className="p-3">
          <p className="line-clamp-2 text-sm font-semibold leading-snug text-foreground">
            {title}
          </p>

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
