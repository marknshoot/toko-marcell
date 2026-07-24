import { formatRp } from "../lib/formatRp";
import Link from "next/link";

export default function ProductCard({ id, title, priceIdr, category}) {
  return (
    <article className="rounded-lg border border-border bg-surface p-3">
        <Link href={`/product/${id}`} className="no-underline">
            <p className="font-semibold text-foreground">{title}</p>
            <p className="text-sm text-muted">{category}</p>
            <p className="mt-1 text-sm text-foreground">{formatRp(priceIdr)}</p>
        </Link>
    </article>
  );
}