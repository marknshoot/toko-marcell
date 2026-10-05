const STORAGE_KEY = "toko-session";
const COPILOT_THREAD_KEY = "tm_copilot_thread";
const COPILOT_MESSAGES_KEY = "tm_copilot_messages";

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

// ── Copilot thread (v2) ──────────────────────────────────────────────────────
// A browser-generated UUID kept in localStorage keys the server-side LangGraph
// Postgres checkpointer, so the conversation survives refresh across sessions.

function _uuid() {
  return typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `t-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

export function getCopilotThreadId() {
  if (typeof window === "undefined") {
    return null;
  }
  const existing = window.localStorage.getItem(COPILOT_THREAD_KEY);
  if (existing) {
    return existing;
  }
  const id = _uuid();
  window.localStorage.setItem(COPILOT_THREAD_KEY, id);
  return id;
}

export function resetCopilotThread() {
  if (typeof window === "undefined") {
    return null;
  }
  const id = _uuid();
  window.localStorage.setItem(COPILOT_THREAD_KEY, id);
  window.localStorage.removeItem(COPILOT_MESSAGES_KEY);
  return id;
}

export function loadCopilotMessages() {
  if (typeof window === "undefined") {
    return [];
  }
  try {
    const raw = window.localStorage.getItem(COPILOT_MESSAGES_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

export function saveCopilotMessages(messages) {
  if (typeof window === "undefined") {
    return;
  }
  try {
    // keep a bounded display copy
    const trimmed = messages.slice(-40);
    window.localStorage.setItem(COPILOT_MESSAGES_KEY, JSON.stringify(trimmed));
  } catch {
    // ignore quota / serialization errors
  }
}
