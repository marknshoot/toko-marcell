# `shop/` — Toko Marcell storefront (Next.js 16 App Router)

[![Next.js 16](https://img.shields.io/badge/Next.js-16%20App%20Router-black?style=for-the-badge&logo=next.js&logoColor=white)](https://nextjs.org)
[![React 19](https://img.shields.io/badge/React-19-61DAFB?style=for-the-badge&logo=react&logoColor=black)](https://react.dev)
[![Tailwind CSS v4](https://img.shields.io/badge/Tailwind-v4-06B6D4?style=for-the-badge&logo=tailwindcss&logoColor=white)](https://tailwindcss.com)
[![Vitest](https://img.shields.io/badge/Vitest-Unit%20Tests-FCC72B?style=for-the-badge&logo=vitest&logoColor=black)](https://vitest.dev)
[![Vercel](https://img.shields.io/badge/Deployed-Vercel%20Edge-000000?style=for-the-badge&logo=vercel&logoColor=white)](https://toko-marcell.vercel.app)

The client-facing storefront. It renders the catalog, runs client-side search/filter
UX, keeps the cart in `localStorage`, and talks to the FastAPI backend over HTTP. It never touches a
database and never decides a price.

🌐 **Live:** [https://toko-marcell.vercel.app](https://toko-marcell.vercel.app)

---

## Why it is built this way

A recruiter opening this link decides in seconds whether it works. That drove four deliberate choices:

1. **Server-render first paint, hydrate interaction.** Home and product pages are React Server
   Components; only the parts that need state (`Catalog`, cart, copilot, wakeup) are client
   components. No JS bundle is needed to see the page.
2. **Cache aggressively, revalidate in the background.** Product detail pages are ISR-cached at the
   edge for 5 minutes.
3. **Never show a broken-looking page.** The backend is on a free tier that sleeps; the UI detects
   that and explains it (see the wakeup overlay).
4. **The client cannot lie about money.** The cart sends `{asin, qty}` only; the server computes the
   order total.

---

## Feature tour

### 1. Catalog (`components/Catalog.js`)

- Product grid with **24-per-page** pagination and skeleton placeholders while loading.
- **Department filter chips** from the live `/categories` facets.
- **Debounced text search** (250 ms) that calls the backend `/search` endpoint, so ranking is
  server-side (BM25 + dense vectors + RRF) rather than a client filter.
- URL-driven state (`?department=`, `?page=`, `?q=`), so filtered/search views are shareable and the
  back button behaves.
- A visible "taking longer than usual" state after 1 s so a cold backend never looks frozen.

### 2. Cold-start wakeup overlay (`components/ServerWakeup.js`)

Render's free tier spins the API down after ~15 minutes idle; the next request can take 30–50 s. The
storefront makes this a designed state instead of a failure:

- Debounced (600 ms) `GET /health` on mount — fast servers never see the overlay.
- If slow: a **fullscreen frosted-glass overlay** with a spinner and an honest message
  (*"Membangunkan server… Render free tier cold start (~30–50 dtk)"*).
- On success: a green *"Server siap & aktif!"* badge for 2.5 s, then unmount.
- On failure: **retry** and **dismiss** controls, so the user is never trapped.

`checkHealth()` in `lib/api.js` falls back to `/categories` if `/health` is blocked by a browser
shield, because a false "down" is worse than a heavier probe.

### 3. Floating AI copilot (`components/CopilotChat.js` + `FloatingCopilot.js`)

- A chat panel mounted globally in `layout.js`, loaded with `dynamic(..., { ssr: false })` so it never
  costs first paint. A floating **"Tanya Admin"** button opens it; **Escape** closes it, and controls
  carry `aria-label`s.
- **Text-only & streaming (v2).** The chat streams a single `message` plus a browser `thread_id` to
  `POST /copilot/chat/stream` (SSE `token` + `done` events), showing a pulsing `"Sedang berpikir..."`
  indicator while waiting for the first token and rendering GitHub Flavored Markdown (including tables)
  via `react-markdown` and `remark-gfm`. Conversation history lives server-side in the LangGraph
  checkpointer, not in the request. **There is no image upload** — photo search is a separate storefront
  feature (see Catalog, below).
- **Starter chips only on a new conversation** (`messages.length === 0`), dynamically adapting between
  store-wide prompts, single-product prompts, and multi-product comparison prompts.
- **Paperclip product search & attach (`📎`)** lets shoppers search the catalog inside the chat bar and
  attach up to 3 products (`context.referenced_asins`), replacing the old separate compare tray.
- Replies render **numbered product cards** (`[1]`, `[2]`), each exposing
  **📎 Lampirkan** and **+ Keranjang** actions, plus an **add-to-cart confirmation with a "Batalkan" undo**.
- Agent `add_to_cart` ui_actions are executed through `CartProvider`, so a chat-driven add updates
  `localStorage`, the header count and the cart page — the server never tries to write the cart itself.
- **Attached product chips** show which products the conversation is anchored to, and a **reset button**
  clears the thread (`DELETE /copilot/threads/{id}` + wipes the local copy).

### 3b. Product-page copilot entry (`components/ProductCopilotEntry.js`)

On a product page, a **"Tanya soal produk ini"** button opens the **same floating copilot** that
`app/layout.js` mounts on every page (bottom-right), **pinned to that product**, so follow-ups resolve to
the right ASIN and the product starter chips appear. It renders no chat of its own: it dispatches a
`copilot:open` window event (`{asin, title}`) that the single `CopilotChat` instance listens for.

### 4. Cart without accounts (`components/CartProvider.js`)

- React Context + `localStorage` under the key **`toko-cart-v2`**, persisted across tabs and refresh.
- Supports add / increase / decrease / set-qty (clamped 1–99) / remove / clear, with `subtotal` and a
  derived `cartCount`.
- Each line stores `{id, asin, title, priceIdr, imageUrl, qty}`. The `asin` is required for the server
  to price the cart, which is why the storage key was bumped from v1 — an old v1 line has no asin and
  cannot be priced, and a silent migration would have produced unpriceable lines.
- `add_to_cart` funnel events are emitted for **every** entry point (card, PDP, copilot), so the funnel
  cannot miss a path.

### 5. Product detail with ISR (`app/product/[id]/page.js`)

- `export const revalidate = 300` → stale-while-revalidate at Vercel's edge.
- Breadcrumb, brand, rating, formatted IDR price, feature bullets, description, real customer reviews,
  and a **"Customers also viewed"** rail fed by `/recs/item/{asin}`.
- `generateMetadata` produces a per-product `<title>`/description for sharing.
- `TrackView` fires a `view_product` event on mount (deduplicated per ASIN).

### 6. Demo checkout (`app/checkout`, `checkout/qris`, `checkout/confirm/[token]`)

- `/checkout` shows the order summary and fires `checkout_start` once per visit.
- `/checkout/qris` renders a clearly-labelled **QRIS simulation**; "I have paid" calls
  `POST /checkout/confirm`, which creates the authoritative order server-side and returns a secret
  token.
- `/checkout/confirm/[token]` **fetches** the order (it is a server component) and renders it. An
  invalid token renders a 404, never a fake receipt. `ClearCart` empties the local cart on success.
- The client sends only `{asin, qty}`; the server prices it. A tampered total cannot get through.

### 7. Recommendations & reviews

- `RecommendationsRail` on the home page calls `/recs/session` (falls back to global popularity when
  the visit is cold) and exposes a "load more" control.
- `ProductReviews` renders the star breakdown and real review text from `/products/{id}/reviews`.

### 8. Executive KPI dashboard (`app/admin/kpi/page.js`)

The operations surface at **`/admin/kpi`** is a client page that reads `GET /admin/kpi` and renders
the v2 executive dashboard. It is gated end to end: no KPI request is made until an admin session
exists, and a `401`/`403` tears the session down and returns to the login gate.

**What the v2 dashboard shows**

- A **7 / 30 / 90-day** window switcher, a manual **Refresh** control with an `aria-busy` spinner, and
  a **Sign Out** button next to the signed-in username.
- **Overview** — revenue, paid-order count, sessions, average order value, and session → order
  conversion with its "roughly 1 in N" reading.
- **Conversion funnel** — view → add-to-cart → checkout → paid order, with the biggest stage-to-stage
  drop called out. Bars and rates use session counts while revenue uses order counts (one session can
  place more than one order); the UI states that distinction instead of hiding it.
- **Cohort lift & unit economics** — copilot versus non-copilot cohorts, explicitly labelled
  *observational — not a randomized trial*, beside cost per turn / conversation / resolved
  conversation / assisted order.
- **Assistant & demand** — copilot quality (total turns, unanswered and deflection rates, thumbs
  up/down, latency) and search health (searches, zero-result rate, top zero-result queries).
- A permanent **“Demo data”** banner with a `<details>` caveat, because the numbers are derived from
  seeded/simulated data, not audited revenue.
- Published **chart rules**: one quantity per encoding from a zero baseline, labelled scales, rates as
  text rather than bars, colour only for categories, and every bar duplicated by a real number for
  screen readers.

`getAdminKpi()` never caches (`cache: "no-store"`) and prefers the current session's access token, so
an access token rotated by a silent refresh is not replayed.

### 8b. Admin login (`components/AdminLoginModal.js`)

- `TokenGate` renders the modal as a **non-dismissible gate** (`dismissible={false}`) when no access
  token is available; there is no read-only or blurred-data fallback.
- **Demo login** fills and submits `admin` / `marcell-admin-2026`; the manual form validates that both
  fields are non-empty before calling `adminLogin()`.
- Accessibility: `role="dialog"`, `aria-modal="true"`, labelled title and description via `useId`,
  focus moved to the username field on open, **Escape** closes it when dismissible, and the submit
  button is disabled while the request is in flight.
- Errors from the API (`401` → "Invalid username or password") are rendered inline without leaking
  internals. A legacy `?token=` link is stored as a one-shot admin token and stripped from the URL, so
  scripts keep working without leaving the secret in browser history.

### 8c. Dual-JWT + HttpOnly rotating refresh-token lifecycle

The dashboard uses two credentials with different lifetimes and different exposure:

| Credential | Lifetime | Storage | Purpose |
|---|---|---|---|
| **Access JWT** (`HS256`) | 15 minutes (`ACCESS_TOKEN_EXPIRE_MINUTES`) | In-memory + `sessionStorage` (`toko_admin_access_token`) | `Authorization: Bearer …` on `/admin/kpi` |
| **Refresh token** | 7 days (`REFRESH_TOKEN_EXPIRE_DAYS`) | **HttpOnly** cookie `refresh_token`, `Path=/auth`, `SameSite=Lax`, `Secure` on HTTPS | Redeeming a fresh access JWT |

The refresh token is a 48-byte `secrets.token_urlsafe` value. The database stores **only its SHA-256
digest** in `admin_refresh_tokens.token_hash`, so neither JavaScript nor a database dump can replay it.

```mermaid
sequenceDiagram
    participant B as Browser (shop)
    participant A as FastAPI /auth
    participant P as Postgres

    B->>A: POST /auth/login {username, password}
    A->>P: SELECT admin_users (Argon2id verify)
    A->>P: INSERT admin_refresh_tokens (sha256, expires_at)
    A-->>B: 200 {access_token} + Set-Cookie: refresh_token (HttpOnly)
    B->>A: GET /admin/kpi (Authorization: Bearer …)
    A-->>B: 401 when the 15-minute access JWT expires
    B->>A: POST /auth/refresh (cookie sent automatically)
    A->>P: SELECT … FOR UPDATE, revoke old row, INSERT new row
    A-->>B: 200 {new access_token} + rotated Set-Cookie
    B->>A: GET /admin/kpi (fresh Bearer token)
    A-->>B: 200 KPI payload
    B->>A: POST /auth/logout
    A->>P: UPDATE admin_refresh_tokens SET revoked = TRUE
    A-->>B: 200 {ok} + expired refresh cookie
```

Lifecycle rules enforced server-side:

1. **Login** verifies Argon2id credentials, inserts a refresh-token hash, and returns a 15-minute
   access JWT. The refresh cookie is set with `HttpOnly`, `SameSite=Lax`, `Path=/auth`, and `Secure`
   whenever the request arrived over HTTPS.
2. **Every admin request** sends the access JWT as a Bearer token. A `401` triggers exactly one
   silent `POST /auth/refresh` (with `credentials: "include"`); if that fails, the stored session is
   cleared and the login gate returns.
3. **Rotation on every use.** A successful refresh revokes the presented row and inserts a new one
   inside `SELECT … FOR UPDATE`, issuing both a new access JWT and a new cookie. Refresh tokens are
   single-use.
4. **Reuse detection.** Presenting an already-revoked token is treated as theft: every active refresh
   token for that user is revoked, the reason is logged, and `401` is returned — the whole family of
   sessions is shut down, not just the replayed token.
5. **Expiry and logout.** Expired tokens are marked revoked and rejected; logout revokes the presented
   token server-side and clears the cookie. `adminLogout()` clears in-memory and `sessionStorage`
   state in a `finally` block, so signing out still works when the API is unreachable.
6. **Legacy fallback.** `X-Admin-Token` (or `?token=`) and the static `ADMIN_TOKEN` environment
   variable remain accepted for scripts; the dashboard prefers JWT auth whenever it is available.

---

## Tech stack

| Layer | Choice |
|---|---|
| Framework | **Next.js 16** (App Router, RSC, Turbopack) |
| Runtime UI | **React 19** |
| Styling | **Tailwind CSS v4** + `Geist` variable font (`next/font`, `display: swap`) |
| State | React Context + `localStorage` (no state library) |
| Icons | Inline SVG (no icon dependency) |
| Tests | **Vitest 5** + Testing Library + jsdom 30 |
| Hosting | Vercel (edge ISR + security headers) |

---

## Getting started

```bash
cd manual/shop
npm install
```

Create `shop/.env.local`:

```env
NEXT_PUBLIC_API_URL=http://localhost:8001
```

```bash
npm run dev      # http://localhost:3000
npm test         # 38 tests across 7 suites
npm run lint
npm run build
```

The API URL is resolved per environment in `lib/api.js`: on the server it prefers
`INTERNAL_API_URL` (container networking, e.g. `http://api:8000`), on the client it uses
`NEXT_PUBLIC_API_URL`. The built-in fallback is `http://localhost:8001`, so a **production deploy must
set `NEXT_PUBLIC_API_URL`** (and ideally `INTERNAL_API_URL` for server-side fetches) or the browser will
look for a backend on localhost.

---

## Environment variables

| Variable | Used | Default | Purpose |
|---|---|---|---|
| `NEXT_PUBLIC_API_URL` | client + server | `http://localhost:8001` | Public API base URL (**required** in production) |
| `INTERNAL_API_URL` | server only | falls back to `NEXT_PUBLIC_API_URL` | Container-internal API URL (avoids a round trip through the public host) |

---

## Component & route map

```text
src/
├── app/
│   ├── layout.js                 # header, footer, CartProvider, ServerWakeup, FloatingCopilot
│   ├── page.js                   # home: hero, RecommendationsRail, Catalog (Suspense)
│   ├── globals.css               # Tailwind v4 theme + design tokens
│   ├── product/[id]/page.js      # PDP with ISR (revalidate = 300), metadata, recs, reviews
│   ├── admin/kpi/page.js         # executive KPI dashboard v2 + login gate + refresh/sign-out
│   ├── cart/page.js              # cart overview
│   └── checkout/
│       ├── page.js               # order summary (fires checkout_start)
│       ├── qris/page.js          # QRIS simulation → server-confirmed order
│       └── confirm/[token]/page.js   # server-fetched receipt; 404 on bad token
├── components/
│   ├── Catalog.js                # grid, search (debounced), department filter, paging
│   ├── CatalogSkeleton.js        # home Suspense fallback
│   ├── ProductCard.js            # card used in grid + rec rails
│   ├── ProductCardSkeleton.js    # per-card placeholder
│   ├── ProductImage.js           # next/image wrapper for Amazon CDN hostnames
│   ├── AddToCartButton.js        # PDP quantity + add
│   ├── CartLink.js               # header cart badge/count
│   ├── CartProvider.js           # cart context + localStorage (toko-cart-v2)
│   ├── ClearCart.js              # clears the cart on the success page
│   ├── ProductReviews.js         # rating breakdown + review list
│   ├── RecommendationsRail.js    # session recommendations + load more
│   ├── TrackView.js              # fires view_product once per ASIN
│   ├── ServerWakeup.js           # cold-start health ping + overlay
│   ├── CopilotChat.js            # v2 copilot panel (streaming SSE, react-markdown, paperclip product search/attach, undo)
│   ├── ProductCopilotEntry.js    # "Tanya soal produk ini" button on the PDP; opens the floating copilot pinned to the product (copilot:open event)
│   ├── AdminLoginModal.js        # accessible admin login dialog (demo + manual credentials)
│   └── FloatingCopilot.js        # lazy, client-only mount for the copilot
└── lib/
    ├── api.js                    # the ONLY place that knows the API URL; all fetches
    ├── formatRp.js               # IDR currency formatting
    └── session.js                # anonymous session id + copilot thread id/messages (local storage)
```

### Data flow

```mermaid
sequenceDiagram
    participant B as Browser
    participant N as Next.js (server)
    participant A as FastAPI
    participant P as Postgres/pgvector
    B->>N: GET /product/1
    N->>A: GET /products/1 (+ recs, reviews)
    A->>P: SQL
    P-->>A: rows
    A-->>N: JSON
    N-->>B: HTML (edge-cached 300s)
    B->>A: fire view_product / add_to_cart events (fire-and-forget)
    B->>A: POST /checkout/confirm {asin, qty}
    A->>P: price lookup + order insert
    A-->>B: {token, total_idr}
    B->>N: GET /checkout/confirm/<token>
    N->>A: GET /orders/<token>
    A-->>N: order
    N-->>B: receipt HTML
```

---

## Testing

```bash
npm test
```

**38 tests across 7 suites**:

| Suite | Tests | Covers |
|---|:---:|---|
| `components/__tests__/CartProvider.test.jsx` | 7 | empty cart, add, duplicate merge, quantity, remove-at-zero, clear, multi-item subtotal |
| `components/__tests__/AddToCartButton.test.jsx` | 3 | add with quantity, navigate home, existing-quantity display |
| `components/__tests__/ServerWakeup.test.jsx` | 2 | ready indicator on healthy check, spinner while waiting |
| `components/__tests__/CopilotChat.test.jsx` | 10 | no image-upload or TB/BB form, starter chips only when empty, product-page chips, `copilot:open` event attaches product, paperclip search & attach, pulsing `"Sedang berpikir..."` + streaming markdown, `add_to_cart` ui_action, numbered product cards, reset deletes thread |
| `components/__tests__/SizeAdvisor.test.jsx` | 7 | height/weight for tops, foot length for footwear, waist for belts, head circumference for hats, dimensions note, unsupported category without a guess, one-size note |
| `app/admin/kpi/__tests__/page.test.jsx` | 7 | demo login renders KPIs, manual login, window switch and refresh refetch, sign out, 401 returns to the login gate, tab-restored session, legacy `?token=` consumption |
| `lib/__tests__/session.test.js` | 2 | id persists in `sessionStorage`, stable across calls |

CI also runs `npm run lint` and `npm run build` so the Next build cannot regress silently.

---

## Design decisions

- **`lib/api.js` is the single HTTP boundary.** Every fetch, error message and fallback lives there,
  which is why the components stay readable and why the "friendly error, never leak internals" rule
  holds everywhere.
- **URL as state.** Filters/search/pagination live in the query string, not only in React state, so
  views are shareable and navigation works.
- **Fire-and-forget analytics.** `postEvent` swallows errors and uses `keepalive: true`. A dropped
  event is cheaper than a broken purchase.
- **Anonymous identity.** `session.js` generates an id in `sessionStorage` (with a non-secure-context
  fallback for plain-http LAN addresses) — enough to attribute a funnel, not enough to identify a
  person.
- **No UI kit.** Small components with plain Tailwind keep the bundle and the dependency surface small.

---

## Known limitations

- Department filtering is applied server-side on the catalog; the search `results_count` event still
  reflects the loaded page, not the full catalog (tracked as a backend gap).
- The QRIS screen is an explicit simulation and charges nothing; it exists to exercise the
  server-authoritative checkout path.
- The copilot's inline product cards depend on the backend's response schema; if a tool returns no
  products the panel simply shows text.
- Cold starts are handled, not eliminated — the live API is on a free tier.

---

### Related docs

- [`../README.md`](../README.md) — project overview
- [`../api/README.md`](../api/README.md) — endpoints, search internals, checkout guarantees
- [`../pipelines/README.md`](../pipelines/README.md) — where the data comes from
