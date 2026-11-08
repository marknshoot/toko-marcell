"use client";

import { useState } from "react";
import Image from "next/image";

export default function ProductImage({ src, alt, padding = "p-3" }) {
  // Track *which* src finished, not a boolean: a card reused for another product
  // then shows the placeholder again with no effect resetting it. next/image calls
  // onLoad for images that were already cached before hydration.
  const [loadedSrc, setLoadedSrc] = useState(null);
  const [failedSrc, setFailedSrc] = useState(null);
  const showImage = src && failedSrc !== src;
  const loaded = loadedSrc === src;

  return (
    <div className="relative aspect-square w-full bg-white">
      {showImage && !loaded ? (
        <span
          aria-hidden="true"
          className="absolute inset-0 animate-pulse bg-border motion-reduce:animate-none"
        />
      ) : null}
      {showImage ? (
        <Image
          src={src}
          alt={alt || "Product image"}
          fill
          sizes="(max-width: 640px) 50vw, (max-width: 1024px) 33vw, 25vw"
          unoptimized
          onLoad={() => setLoadedSrc(src)}
          onError={() => setFailedSrc(src)}
          className={`object-contain ${padding}`}
        />
      ) : (
        <div className="flex h-full w-full items-center justify-center text-xs text-muted">
          No image
        </div>
      )}
    </div>
  );
}
