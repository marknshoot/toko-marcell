"use client";

import { useState } from "react";

// Product image with a graceful failure state.
//
// ~13% of the 2018 Amazon CDN URLs are dead, so an <img> alone would show a
// broken-image icon on roughly 1 in 8 cards. The wrapper owns the square box
// (aspect-square), which means the space is reserved before the image arrives —
// that's what keeps the grid from jumping while images stream in.
export default function ProductImage({ src, alt, padding = "p-3" }) {
  const [failed, setFailed] = useState(false);
  const showImage = src && !failed;

  return (
    <div className="relative aspect-square w-full bg-white">
      {showImage ? (
        <img
          src={src}
          alt={alt}
          loading="lazy"
          decoding="async"
          onError={() => setFailed(true)}
          className={`h-full w-full object-contain ${padding}`}
        />
      ) : (
        <div className="flex h-full w-full items-center justify-center text-xs text-muted">
          No image
        </div>
      )}
    </div>
  );
}
