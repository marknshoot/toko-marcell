const STORAGE_KEY = "toko-session";

export function getSessionId() {
  if (typeof window === "undefined") {
    return null;
  }

  const existing = window.sessionStorage.getItem(STORAGE_KEY);
  if (existing) {
    return existing;
  }

  // crypto.randomUUID needs a secure context (https or localhost). Fall back to
  // a random string so the funnel still works over plain http on a LAN address.
  const id =
    typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : `s-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;

  window.sessionStorage.setItem(STORAGE_KEY, id);
  return id;
}
