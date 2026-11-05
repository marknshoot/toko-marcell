import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { streamCopilotMessage } from "../api";

// A fetch response whose body delivers the given SSE text in one read.
function sseResponse(text) {
  const bytes = new TextEncoder().encode(text);
  let sent = false;
  return {
    ok: true,
    body: {
      getReader: () => ({
        read: async () => (sent ? { done: true } : ((sent = true), { done: false, value: bytes })),
      }),
    },
  };
}

const event = (obj) => `data: ${JSON.stringify(obj)}\n\n`;

describe("streamCopilotMessage progress events", () => {
  beforeEach(() => {
    sessionStorage.clear();
    localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reports status labels, then tokens, and returns the done payload", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        sseResponse(
          event({ type: "status", label: "Mencari produk…", tool: "search_catalog" }) +
            event({ type: "status", label: "Menyusun jawaban…" }) +
            event({ type: "token", content: "Ini " }) +
            event({ type: "token", content: "pilihannya" }) +
            event({ type: "done", reply: "Ini pilihannya", products: [], ui_actions: [] })
        )
      )
    );
    const order = [];

    const result = await streamCopilotMessage({
      message: "ada celana?",
      threadId: "t1",
      onStatus: (label) => order.push(`status:${label}`),
      onToken: (_chunk, full) => order.push(`token:${full}`),
    });

    expect(order).toEqual([
      "status:Mencari produk…",
      "status:Menyusun jawaban…",
      "token:Ini ",
      "token:Ini pilihannya",
    ]);
    expect(result.reply).toBe("Ini pilihannya");
  });

  it("ignores event types it does not know, so a newer server never breaks this client", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        sseResponse(
          event({ type: "approval_required", thread_id: "t1", items: [{ asin: "B01" }] }) +
            event({ type: "something_from_the_future", payload: 1 }) +
            event({ type: "token", content: "Halo" }) +
            event({ type: "done", reply: "Halo", pending_approval: { items: [{ asin: "B01" }] } })
        )
      )
    );
    const onToken = vi.fn();
    const onStatus = vi.fn();

    const result = await streamCopilotMessage({ message: "x", threadId: "t1", onToken, onStatus });

    expect(onStatus).not.toHaveBeenCalled();
    expect(onToken).toHaveBeenCalledTimes(1);
    expect(result.pending_approval.items[0].asin).toBe("B01");
  });

  it("works without an onStatus callback and skips malformed lines and empty labels", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        sseResponse(
          event({ type: "status", label: "Mencari produk…" }) +
            event({ type: "status", label: "" }) +
            "data: {not json\n\n" +
            event({ type: "done", reply: "ok" })
        )
      )
    );

    await expect(streamCopilotMessage({ message: "x", threadId: "t1" })).resolves.toMatchObject({ reply: "ok" });
  });

  it("does not call onStatus for an empty label", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(sseResponse(event({ type: "status", label: "" }) + event({ type: "done", reply: "ok" })))
    );
    const onStatus = vi.fn();
    await streamCopilotMessage({ message: "x", threadId: "t1", onStatus });
    expect(onStatus).not.toHaveBeenCalled();
  });
});
