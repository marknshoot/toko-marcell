import './App.css'
import { formatRp } from "./lib/formatRp";
import { useState } from 'react';

const MOCK_PRODUCTS = [
  {
    id: "1",
    title: "Cotton Tee",
    priceIdr: 129000,
    category: "Tops",
  },
  {
    id: "2",
    title: "Linen Shirt",
    priceIdr: 249000,
    category: "Tops",
  },
  {
    id: "3",
    title: "Chino Pants",
    priceIdr: 299000,
    category: "Bottoms",
  },
  {
    id: "4",
    title: "Canvas Tote",
    priceIdr: 99000,
    category: "Accessories",
  },
];

const CATEGORIES = [
  "All",
  ...new Set(MOCK_PRODUCTS.map((p) => p.category)),
];

function App() {

  const [selectedCategory, setSelectedCategory] = useState("All");

  const visibleProducts = 
    selectedCategory == "All" 
      ? MOCK_PRODUCTS 
      : MOCK_PRODUCTS.filter(
        (product) => product.category === selectedCategory
      )

  return (
    <div className="app">
      <header className="site-header">
        <a href="/" className="logo"> Toko Marcell </a>
        <nav className="nav">
          <a href="#catalog">Shop</a>
          <a href="#cart" className="cart-link">Cart (0)</a>
        </nav>
      </header>

      <main>
        <section className="hero">
          <p className="eyebrow">Single-brand shop</p>
          <h1>Toko Marcell</h1>
          <p className="hero-text"> Short pitch about everyday pieces and demo checkout.</p>
          <div className="hero-actions">
            <a className="button" href="#catalog">Shop collection</a>
          </div>
        </section>

        <section id="catalog" className="catalog">
          <h2>Shop</h2>

          <div className="search-row">
            <input
              type="search"
              placeholder="Search products…"
              disabled
              readOnly
            />
            <button type="button" disabled> Search </button>
          </div>

          <div className="chips">
            {CATEGORIES.map((cat) => (
              <button
                key={cat}
                type="button"
                className={selectedCategory === cat ? "chip chip-on" : "chip"}
                onClick={() => setSelectedCategory(cat)}
              >
                {cat}
              </button>
            ))}
          </div>

          <div className="product-grid">
            {visibleProducts.map((product) => (
              <article key={product.id} className="product-card">
                <div className="product-img" aria-hidden="true" />
                <p className="product-title">{product.title}</p>
                <p className="product-price">{formatRp(product.priceIdr)}</p>
              </article>
            ))}
          </div>

          <div className="pager">
            <button type="button" disabled>{"< Prev"}</button>
            <span>Page 1 of N</span>
            <button type="button" disabled>{"Next>"}</button>
          </div>
        </section>
      </main>

      <footer className="site-footer">
        <p>Made by Marcell Hermawan Kristianto</p>
      </footer>
    </div>
  )
}

export default App
