import { getSessionId } from "./session";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";

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

  // 404 = id has no product. 422 = FastAPI rejected the id (not an integer).
  // Both mean "this product does not exist", so the page can show its 404.
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

// Fire-and-forget telemetry: the funnel events in PLAN §4.
//
// It never throws and never returns anything the UI waits on. A failed event must
// not break shopping — losing a data point is cheaper than losing a sale.
export async function postEvent({ eventType, asin, query, resultsCount, qty, priceIdr }) {
  const sessionId = getSessionId();
  if (!sessionId) {
    return; // no browser (server render) — there is nothing to record
  }

  try {
    await fetch(`${API_URL}/events`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // keepalive lets the request finish even when the page navigates away in
      // the same tick (add_to_cart then redirect, or "I have paid" -> success).
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
  } catch (err) {
    // best effort by design
  }
}
