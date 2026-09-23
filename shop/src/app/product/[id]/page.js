import { notFound } from "next/navigation";
import { formatRp } from "../../../lib/formatRp";
import { getProduct } from "../../../lib/api";
import AddToCartButton from "@/components/AddToCartButton";

export default async function ProductPage({ params }) {
    const { id } = await params;

    // Server component: this fetch runs on the Next server, not in the browser,
    // so it talks to FastAPI directly (no CORS involved).
    const product = await getProduct(id);

    if (!product) {
        notFound();
    }

    return (
        <main className="py-12">
            <div className="mx-auto max-w-6xl px-7">
                <p className="text-xs font-medium uppercase tracking-[0.12em] text-muted">
                    {product.category}
                </p>
                <h1 className="mt-2 text-3xl font-semibold tracking-tight text-foreground">
                    {product.title}
                </h1>
                <p className="mt-4 text-lg text-foreground">
                    {formatRp(product.priceIdr)}
                </p>
                {product.description ? (
                    <p className="mt-4 text-sm text-muted">{product.description}</p>
                ) : null}
                <AddToCartButton product={product} />
            </div>
        </main>
    );
}