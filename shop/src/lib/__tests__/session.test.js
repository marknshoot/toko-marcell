import { describe, it, expect, beforeEach } from "vitest";
import { getSessionId } from "../session";

describe("session utility", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
  });

  it("generates a new session ID and persists it in sessionStorage", () => {
    const id = getSessionId();
    expect(id).toBeDefined();
    expect(typeof id).toBe("string");
    expect(id.length).toBeGreaterThan(10);
    expect(window.sessionStorage.getItem("toko-session")).toBe(id);
  });

  it("returns the exact same session ID on subsequent calls in the same session", () => {
    const firstId = getSessionId();
    const secondId = getSessionId();
    expect(secondId).toBe(firstId);
  });
});
