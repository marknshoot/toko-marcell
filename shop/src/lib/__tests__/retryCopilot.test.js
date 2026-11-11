import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { retryCopilotTurn, streamCopilotMessage } from "../api";

function sseResponse(text) {
  const bytes = new TextEncoder().encode(text);
  let sent = false;
  return {
    ok: true,
    headers: { get: () => null },
    body: {
      getReader: () => ({
        read: async () => (sent ? { done: true } : ((sent = true), { done: false, value: bytes })),
      }),
    },
  };
}

const event = (obj) => `data: ${JSON.stringify(obj)}\n\n`;

describe("retryCopilotTurn", () => {
  beforeEach(() => {
    sessionStorage.clear();
    localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("resumes a turn without sending the message again", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      sseResponse(
        event({ type: "token", content: "Halo" }) +
          event({ type: "done", reply: "Halo kak!", products: [], ui_actions: [], error: false })
      )
    );
    vi.stubGlobal("fetch", fetchMock);
    const onToken = vi.fn();

    const result = await retryCopilotTurn({ threadId: "t1", onToken });

    expect(result.reply).toBe("Halo kak!");
    expect(onToken).toHaveBeenCalledWith("Halo", "Halo");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toMatch(/\/copilot\/chat\/retry\/stream$/);
    const body = JSON.parse(init.body);
    expect(body.thread_id).toBe("t1");
    expect(body).not.toHaveProperty("message");
  });

  it("turns a 429 into a retryable error with the server's Retry-After", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 429,
        headers: { get: (name) => (name === "Retry-After" ? "9" : null) },
        json: async () => ({ detail: "Terlalu banyak permintaan" }),
      })
    );

    await expect(retryCopilotTurn({ threadId: "t1" })).rejects.toMatchObject({
      message: "Terlalu banyak permintaan",
      status: 429,
      code: "client_rate_limited",
      retryable: true,
      retryAfter: 9,
    });
  });

  it("keeps a structured 503 detail and marks it non-retryable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 503,
        headers: { get: () => null },
        json: async () => ({
          detail: { message: "Asisten AI sedang tidak tersedia.", code: "llm_unavailable", retryable: false },
        }),
      })
    );

    await expect(retryCopilotTurn({ threadId: "t1" })).rejects.toMatchObject({
      message: "Asisten AI sedang tidak tersedia.",
      status: 503,
      code: "llm_unavailable",
      retryable: false,
    });
  });
});

describe("streamCopilotMessage interruption", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("throws a retryable error with the partial text when the stream ends without done", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse(event({ type: "token", content: "Ini " }))));

    await expect(streamCopilotMessage({ message: "x", threadId: "t1" })).rejects.toMatchObject({
      code: "stream_interrupted",
      retryable: true,
      partialText: "Ini ",
    });
  });
});
