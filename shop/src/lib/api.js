import { getSessionId } from "./session";
const API_URL =
  typeof window === "undefined"
    ? process.env.INTERNAL_API_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001"
    : process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";

export async function checkHealth() {
  try {
    const response = await fetch(`${API_URL}/health`, { cache: "no-store" });
    return response.ok;
  } catch {
    try {
      const fallback = await fetch(`${API_URL}/categories`, { cache: "no-store" });
      return fallback.ok;
    } catch {
      return false;
    }
  }
}

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
// ── AI Copilot (Admin Toko Marcell) — v2 contract ────────────────────────────

export async function sendCopilotMessage({ message, threadId, context = null }) {
  const sessionId = getSessionId();
  const response = await fetch(`${API_URL}/copilot/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_id: sessionId,
      thread_id: threadId,
      message,
      context: context
        ? {
            page_asin: context.pageAsin ?? null,
            referenced_asins: context.referencedAsins ?? [],
          }
        : null,
    }),
  });

  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || "Could not reach Toko Marcell Copilot");
  }

  return response.json();
}

export async function streamCopilotMessage({ message, threadId, context = null, onToken }) {
  const sessionId = getSessionId();
  const response = await fetch(`${API_URL}/copilot/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_id: sessionId,
      thread_id: threadId,
      message,
      context: context
        ? {
            page_asin: context.pageAsin ?? null,
            referenced_asins: context.referencedAsins ?? [],
          }
        : null,
    }),
  });

  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || "Could not reach Toko Marcell Copilot");
  }

  if (!response.body) {
    return response.json();
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let donePayload = null;
  let fullReply = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed.startsWith("data:")) continue;
      const raw = trimmed.slice(5).trim();
      if (!raw) continue;
      try {
        const evt = JSON.parse(raw);
        if (evt.type === "token" && evt.content) {
          fullReply += evt.content;
          if (onToken) onToken(evt.content, fullReply);
        } else if (evt.type === "done") {
          donePayload = evt;
        }
      } catch {
        // ignore malformed chunk
      }
    }
  }

  return (
    donePayload || {
      reply: fullReply,
      thread_id: threadId,
      products: [],
      ui_actions: [],
      guardrail: null,
    }
  );
}

export async function postCopilotFeedback({ threadId, rating }) {
  const sessionId = getSessionId();
  const response = await fetch(`${API_URL}/copilot/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    keepalive: true,
    body: JSON.stringify({
      thread_id: threadId,
      session_id: sessionId,
      rating,
    }),
  });

  if (!response.ok) {
    throw new Error("Could not save copilot feedback");
  }

  return response.json();
}

export async function getCopilotThread(threadId) {
  try {
    const response = await fetch(`${API_URL}/copilot/threads/${encodeURIComponent(threadId)}`);
    if (!response.ok) {
      return { thread_id: threadId, messages: [] };
    }
    return response.json();
  } catch {
    return { thread_id: threadId, messages: [] };
  }
}

export async function deleteCopilotThread(threadId) {
  try {
    await fetch(`${API_URL}/copilot/threads/${encodeURIComponent(threadId)}`, {
      method: "DELETE",
      keepalive: true,
    });
  } catch {
    // best-effort; local reset still happens client-side
  }
}

export async function getCopilotTools() {
  const response = await fetch(`${API_URL}/copilot/tools`);
  if (!response.ok) {
    throw new Error("Could not load copilot tools schema");
  }
  return response.json();
}

// ── Sizing advisor (category-aware, no LLM) ──────────────────────────────────

export async function getSizingInfo(asin) {
  try {
    const response = await fetch(`${API_URL}/sizing/${encodeURIComponent(asin)}`);
    if (response.status === 404) {
      return null;
    }
    if (!response.ok) {
      return null;
    }
    return response.json();
  } catch {
    return null;
  }
}

export async function postSizingRecommend({ asin, heightCm, weightKg, footLengthCm, waistCm, headCircumferenceCm }) {
  const response = await fetch(`${API_URL}/sizing/recommend`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      asin,
      height_cm: heightCm ?? null,
      weight_kg: weightKg ?? null,
      foot_length_cm: footLengthCm ?? null,
      waist_cm: waistCm ?? null,
      head_circumference_cm: headCircumferenceCm ?? null,
    }),
  });

  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || "Could not get a size recommendation");
  }

  return response.json();
}

// ── Admin KPI dashboard ──────────────────────────────────────────────────────

export async function getAdminKpi(token, sinceDays = 30) {
  const params = new URLSearchParams();
  params.set("since_days", String(sinceDays));

  const response = await fetch(`${API_URL}/admin/kpi?${params.toString()}`, {
    headers: { "X-Admin-Token": token },
    cache: "no-store",
  });

  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    const error = new Error(err.detail || "Could not load the admin dashboard");
    error.status = response.status;
    throw error;
  }

  return response.json();
}
