import { notFound } from "next/navigation";
import { formatRp } from "../../../lib/formatRp";
import { MOCK_PRODUCTS } from "../../../lib/mockProducts";
import AddToCartButton from "@/components/AddToCartButton";

export default async function ProductPage({ params }) {
    const { id } = await params;
    const product = MOCK_PRODUCTS.find((p) => p.id === id);

    if (!product) {
        notFound();
    }

    return (
        <main className="py-12">
            <div className="mx-auto max-w-2xl px-7">
                <p className="text-xs font-medium uppercase tracking-[0.12em] text-muted">
                    {product.category}
                </p>
                <h1 className="mt-2 text-3xl font-semibold tracking-tight text-foreground">
                    {product.title}
                </h1>
                <p className="mt-4 text-lg text-foreground">
                    {formatRp(product.priceIdr)}
                </p>
                <AddToCartButton product={product}/>
            </div>
        </main>
    );
}