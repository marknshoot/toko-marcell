# Toko Marcell — Frontend Storefront (`manual/shop`)

[![Next.js 16](https://img.shields.io/badge/Next.js-16%20App%20Router-black?style=for-the-badge&logo=next.js&logoColor=white)](https://nextjs.org)
[![Tailwind CSS v4](https://img.shields.io/badge/Tailwind-v4-06B6D4?style=for-the-badge&logo=tailwindcss&logoColor=white)](https://tailwindcss.com)
[![Vitest](https://img.shields.io/badge/Vitest-Unit%20Tests-FCC72B?style=for-the-badge&logo=vitest&logoColor=black)](https://vitest.dev)
[![Vercel Deployment](https://img.shields.io/badge/Deployed-Vercel%20Edge-000000?style=for-the-badge&logo=vercel&logoColor=white)](https://toko-marcell.vercel.app)

The client-facing e-commerce storefront for **Toko Marcell**, engineered with Next.js 16 App Router. Designed for sub-second page loads, resilient cloud free-tier behavior, and seamless interaction with the multimodal backend.

🌐 **Live URL:** [https://toko-marcell.vercel.app](https://toko-marcell.vercel.app)

---

## ✨ Key Features & Architecture

### 1. ⏱️ Cold-Start Server Wakeup Overlay (`ServerWakeup.js`)
* **Problem:** Cloud backends deployed on Render free tiers spin down after 15 minutes of inactivity, causing initial API requests to stall or fail silently for 30–50 seconds.
* **Solution:** A lightweight global client component pings `/health` upon page load with a 600ms debounce. If the server is cold, an elegant frosted-glass modal appears:
  > *"Membangunkan server... Render free tier cold start (~30-50 dtk)"*
* Once responsive, the modal updates to a green checkmark (*"Server siap & aktif!"*) for 2.5s and smoothly unmounts. If the ping fails or times out, a clean retry action is provided.

### 2. ⚡ Edge ISR Caching
* Dynamic product pages (`src/app/products/[id]/page.js`) export `revalidate = 300` (5 minutes).
* Subsequent visits are served directly from Vercel's global Edge CDN with **sub-40ms Time to First Byte (TTFB)**.

### 3. 🤖 Floating Tri-Modal AI Copilot
* Mounted via `FloatingCopilot.js` and `CopilotChat.js` in `layout.js`.
* Direct streaming chat interface with the LangChain orchestrator on `/copilot/chat`.
* Contextual features:
  * Natural language hybrid search & product card rendering.
  * Body measurement (TB/BB) sizing and fit guidance.
  * Budget-constrained complete outfit generator.
  * Instant store policy lookup via RAG.

### 4. 🛒 LocalStorage Cart & Demo Checkout
* Fully decoupled cart state managed via `CartProvider.js` (`src/components/CartProvider.js`).
* Persists items across tabs and page refreshes without requiring user authentication.
* Simulated QRIS payment gateway modal for interactive checkout testing.

---

## 🛠️ Tech Stack

* **Framework:** Next.js 16 (App Router)
* **Styling:** Tailwind CSS v4 + Geist variable font
* **State Management:** React Context + HTML5 `localStorage`
* **Icons:** Lucide React
* **Unit Testing:** Vitest 5.0 + React Testing Library + jsdom

---

## 🚀 Getting Started

### Prerequisites
* Node.js 18+
* Backend API running locally or on Render

### Installation
```bash
# Navigate to shop folder
cd manual/shop

# Install dependencies
npm install
```

### Environment Configuration
Create a `.env.local` file:
```env
NEXT_PUBLIC_API_URL=http://localhost:8001
```
*(In production, defaults to the deployed Render API if unset).*

### Development Server
```bash
npm run dev
```
Open [http://localhost:3000](http://localhost:3000) in your browser.

### Automated Testing
Run the complete component, session, and wakeup test suites:
```bash
npm test
```
*Current test suite: **15 passed across 5 test files** (`AddToCartButton`, `CartProvider`, `CopilotChat`, `ServerWakeup`, and `session`).*

---

## 📁 Component Structure

```text
src/
├── app/
│   ├── layout.js              # Global layout, Geist font, ServerWakeup, FloatingCopilot
│   ├── page.js                # Storefront home & product catalog
│   ├── products/[id]/page.js  # Product detail with Edge ISR (revalidate = 300)
│   ├── cart/page.js           # Shopping cart overview
│   └── checkout/page.js       # QRIS demo payment flow
├── components/
│   ├── ServerWakeup.js        # Cold-start server health ping & status overlay
│   ├── Catalog.js             # Filterable product grid & search bar
│   ├── CopilotChat.js         # Interactive AI shopping copilot modal
│   ├── FloatingCopilot.js     # Floating chat trigger button
│   ├── CartProvider.js        # Global cart state & localStorage syncing
│   └── VisualSearchModal.js   # Multimodal image upload & visual search
└── lib/
    ├── api.js                 # HTTP client, error sanitization, health checks
    └── session.js             # Session ID generation & storage utilities
```
