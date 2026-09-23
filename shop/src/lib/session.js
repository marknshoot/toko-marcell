// Anonymous visit id — no account, no cookie, no personal data.
//
// sessionStorage (not localStorage) means a new tab is a new session, which is
// honest: a "session" is a visit, not a person. Nothing here identifies anyone,
// it only lets the funnel group events that belong to the same visit.
const STORAGE_KEY = "toko-session";

export function getSessionId() {
  if (typeof window === "undefined") {
    return null; // server render: there is no browser to read from
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
