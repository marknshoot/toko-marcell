// URL contract for the catalog: ?department=, ?page=, ?q=.
// Kept framework-free so the server page, the client search box and the tests
// all normalise a URL the exact same way.

export const PAGE_SIZE = 24;

// /search rejects q longer than 100 characters with a 422, so clamp before the
// request instead of turning a long URL into an error state.
export const MAX_QUERY_LENGTH = 100;

/** searchParams values may be string | string[] | undefined. */
export function firstParam(value) {
  if (Array.isArray(value)) {
    return typeof value[0] === "string" ? value[0] : "";
  }
  return typeof value === "string" ? value : "";
}

/** searchParams -> { department, page, q } with every value safe to use. */
export function parseCatalogParams(searchParams = {}) {
  const department = firstParam(searchParams.department).trim() || "All";
  const page = Math.max(1, Number.parseInt(firstParam(searchParams.page), 10) || 1);
  const q = firstParam(searchParams.q).trim().slice(0, MAX_QUERY_LENGTH);

  return { department, page, q };
}

/** { department, page, q } -> "/?department=Women&page=2&q=levis" (defaults omitted). */
export function buildCatalogUrl({ department = "All", page = 1, q = "" } = {}) {
  const params = new URLSearchParams();

  if (department && department !== "All") {
    params.set("department", department);
  }
  if (page && page > 1) {
    params.set("page", String(page));
  }
  const query = typeof q === "string" ? q.trim() : "";
  if (query) {
    params.set("q", query);
  }

  const search = params.toString();
  return search ? `/?${search}` : "/";
}

export function getTotalPages(total, pageSize = PAGE_SIZE) {
  return Math.max(1, Math.ceil((Number(total) || 0) / pageSize));
}
