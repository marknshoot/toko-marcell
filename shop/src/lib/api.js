import { getSessionId } from "./session";
const API_URL =
  typeof window === "undefined"
    ? process.env.INTERNAL_API_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001"
    : process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";

export async function getProducts({ department, limit = 24, offset = 0 } = {}) {
  const params = new URLSearchParams();
  params.set("limit", String(limit));
  params.set("offset", String(offset));
  if (department && department !== "All") {
    params.set("department", department);
  }

  const response = await fetch(`${API_URL}/products?${params.toString()}`);
  if (!response.ok) {
    throw new Error("Could not load products");
  }
  return response.json();
}

export async function getProduct(id) {
  const response = await fetch(`${API_URL}/products/${id}`);

  if (response.status === 404 || response.status === 422) {
    return null;
  }

  if (!response.ok) {
    throw new Error("Could not load product");
  }

  return response.json();
}

export async function getCategories() {
  const response = await fetch(`${API_URL}/categories`);
  if (!response.ok) {
    throw new Error("Could not load categories");
  }
  return response.json();
}

export async function searchProducts({ q, department, limit = 24, offset = 0 } = {}) {
  const params = new URLSearchParams();
  params.set("q", q);
  params.set("limit", String(limit));
  params.set("offset", String(offset));
  if (department && department !== "All") {
    params.set("department", department);
  }

  const response = await fetch(`${API_URL}/search?${params.toString()}`);
  if (!response.ok) {
    throw new Error("Could not search products");
  }
  return response.json();
}

export async function confirmCheckout(items) {
  const response = await fetch(`${API_URL}/checkout/confirm`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items, session_id: getSessionId() }),
  });

  if (!response.ok) {
    throw new Error("Could not confirm the order");
  }

  return response.json();
}

export async function getOrder(token) {
  const response = await fetch(`${API_URL}/orders/${encodeURIComponent(token)}`);

  if (response.status === 404) {
    return null;
  }

  if (!response.ok) {
    throw new Error("Could not load order");
  }

  return response.json();
}

export async function postEvent({ eventType, asin, query, resultsCount, qty, priceIdr }) {
  const sessionId = getSessionId();
  if (!sessionId) {
    return;
  }

  try {
    await fetch(`${API_URL}/events`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      keepalive: true,
      body: JSON.stringify({
        event_type: eventType,
        session_id: sessionId,
        asin: asin ?? null,
        query: query ?? null,
        results_count: resultsCount ?? null,
        qty: qty ?? null,
        price_idr: priceIdr ?? null,
      }),
    });
  } catch {
  }
}

// ── Recommendations (M7 / M8 / M9) ───────────────────────────────────────────

export async function getPopularRecs({ department, limit = 10 } = {}) {
  const params = new URLSearchParams();
  params.set("limit", String(limit));
  if (department && department !== "All") {
    params.set("department", department);
  }
  const response = await fetch(`${API_URL}/recs/popular?${params.toString()}`);
  if (!response.ok) {
    throw new Error("Could not load popular recommendations");
  }
  return response.json();
}

export async function getItemRecs(asin, limit = 4) {
  try {
    const response = await fetch(`${API_URL}/recs/item/${encodeURIComponent(asin)}?limit=${limit}`);
    if (!response.ok) {
      return { items: [], count: 0 };
    }
    return response.json();
  } catch {
    return { items: [], count: 0 };
  }
}

export async function getSessionRecs(limit = 4) {
  const sessionId = getSessionId();
  if (!sessionId) {
    return getPopularRecs({ limit });
  }
  try {
    const response = await fetch(
      `${API_URL}/recs/session?session_id=${encodeURIComponent(sessionId)}&limit=${limit}`
    );
    if (!response.ok) {
      return getPopularRecs({ limit });
    }
    return response.json();
  } catch {
    return getPopularRecs({ limit });
  }
}

// ── Reviews ───────────────────────────────────────────────────────────────────

export async function getProductReviews(productId, limit = 10) {
  try {
    const response = await fetch(`${API_URL}/products/${productId}/reviews?limit=${limit}`);
    if (!response.ok) {
      return null;
    }
    return response.json();
  } catch {
    return null;
  }
}

// ── Visual / Image Search ─────────────────────────────────────────────────────

export async function searchByImage({ file, department, limit = 24 }) {
  const formData = new FormData();
  formData.append("file", file);

  const params = new URLSearchParams();
  params.set("limit", String(limit));
  if (department && department !== "All") {
    params.set("department", department);
  }

  const response = await fetch(`${API_URL}/search/image?${params.toString()}`, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || "Could not complete image search");
  }

  return response.json();
}
// ── AI Copilot (Admin Toko Marcell) ──────────────────────────────────────────

export async function sendCopilotMessage({ messages, imageUrl = null }) {
  const sessionId = getSessionId();
  const response = await fetch(`${API_URL}/copilot/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_id: sessionId,
      messages: messages.map((m) => ({
        role: m.role,
        content: m.content,
        imageUrl: m.imageUrl || null,
      })),
      image_url: imageUrl,
    }),
  });

  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || "Could not reach Toko Marcell Copilot");
  }

  return response.json();
}

export async function getCopilotTools() {
  const response = await fetch(`${API_URL}/copilot/tools`);
  if (!response.ok) {
    throw new Error("Could not load copilot tools schema");
  }
  return response.json();
}
