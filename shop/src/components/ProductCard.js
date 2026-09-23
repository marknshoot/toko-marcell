"use client";

import { useState } from "react";
import Link from "next/link";
import { formatRp } from "../lib/formatRp";

export default function ProductCard({ id, title, priceIdr, category, brand, imageUrl }) {
  // Some of the catalog's image URLs are dead (~8% of the 2018 Amazon CDN links
  // return 404). Track the failure so the card can fall back to a neutral box
  // instead of showing a broken-image icon.
  const [imageFailed, setImageFailed] = useState(false);
  const hasImage = imageUrl && !imageFailed;

  return (
    <article className="overflow-hidden rounded-lg border border-border bg-surface">
      <Link href={`/product/${id}`} className="block no-underline">
        {/*
          Fixed square box. The box owns the height, so the card is already the
          right size before the image downloads — the grid never jumps.
          object-contain (not cover) because these product photos are shot on
          white and cropping them cuts the product.
        */}
        <div className="relative aspect-square w-full bg-white">
          {hasImage ? (
            <img
              src={imageUrl}
              alt={title}
              loading="lazy"
              decoding="async"
              onError={() => setImageFailed(true)}
              className="h-full w-full object-contain p-3"
            />
          ) : (
            <div className="flex h-full w-full items-center justify-center text-xs text-muted">
              No image
            </div>
          )}
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

          <p className="mt-1 text-sm text-foreground">{formatRp(priceIdr)}</p>
        </div>
      </Link>
    </article>
  );
}
