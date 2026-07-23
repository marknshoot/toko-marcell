import { formatRp } from "../lib/formatRp";

function ProductCard({ title, priceIdr, category, onAdd}) {
    return (
        <article className="product-card">
            <div className="product-img" />
            <p className="product-title">{title}</p>
            <p className="product-category">{category}</p>
            <p className="product-price">{formatRp(priceIdr)}</p>
            <button type="button" className="button" onClick={onAdd}>
                Add
            </button>
        </article>
    );
}

export default ProductCard;