"use client";

import { useState } from "react";
import Image from "next/image";

export default function ProductImage({ src, alt, padding = "p-3" }) {
  const [failed, setFailed] = useState(false);
  const showImage = src && !failed;

  return (
    <div className="relative aspect-square w-full bg-white">
      {showImage ? (
        <Image
          src={src}
          alt={alt || "Product image"}
          fill
          sizes="(max-width: 640px) 50vw, (max-width: 1024px) 33vw, 25vw"
          unoptimized
          onError={() => setFailed(true)}
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
