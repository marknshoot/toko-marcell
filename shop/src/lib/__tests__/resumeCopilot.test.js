import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { resumeCopilot } from "../api";

describe("resumeCopilot", () => {
  beforeEach(() => {
    sessionStorage.clear();
    localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("posts only the thread, the session and a yes/no", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ reply: "ok" }) });
    vi.stubGlobal("fetch", fetchMock);

    const result = await resumeCopilot({ threadId: "t1", approve: true });

    expect(result).toEqual({ reply: "ok" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toMatch(/\/copilot\/chat\/resume$/);
    expect(init.method).toBe("POST");
    const body = JSON.parse(init.body);
    expect(Object.keys(body).sort()).toEqual(["approve", "session_id", "thread_id"]);
    expect(body).toMatchObject({ thread_id: "t1", approve: true });
  });

  it("coerces approve to a boolean so a truthy value can never carry anything else", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    vi.stubGlobal("fetch", fetchMock);

    await resumeCopilot({ threadId: "t1", approve: "yes please" });

    expect(JSON.parse(fetchMock.mock.calls[0][1].body).approve).toBe(true);
  });

  it("throws an error that carries the HTTP status and the server's message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 409, json: async () => ({ detail: "Tidak ada permintaan" }) })
    );

    await expect(resumeCopilot({ threadId: "t1", approve: false })).rejects.toMatchObject({
      status: 409,
      message: "Tidak ada permintaan",
    });
  });

  it("still reports the status when the error body is not JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 502,
        json: async () => {
          throw new Error("not json");
        },
      })
    );

    await expect(resumeCopilot({ threadId: "t1", approve: true })).rejects.toMatchObject({ status: 502 });
  });
});
