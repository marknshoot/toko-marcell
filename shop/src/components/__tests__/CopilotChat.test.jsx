import { render, fireEvent, waitFor, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import CopilotChat from "../CopilotChat";
import { CartProvider } from "../CartProvider";
import * as api from "../../lib/api";

describe("CopilotChat v2", () => {
  beforeEach(() => {
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
    localStorage.clear();
    sessionStorage.clear();
    vi.restoreAllMocks();
  });

  function openPanel(container) {
    const btn = container.querySelector('button[aria-label="Tanya Admin Toko Marcell"]');
    if (btn) fireEvent.click(btn);
  }

  it("has no image upload control and no TB/BB size form UI", () => {
    const { container, queryByLabelText } = render(
      <CartProvider>
        <CopilotChat />
      </CartProvider>
    );
    openPanel(container);
    expect(container.querySelector('input[type="file"]')).toBeNull();
    expect(queryByLabelText("Tinggi badan dalam cm")).toBeNull();
    expect(queryByLabelText("Berat badan dalam kg")).toBeNull();
  });

  it("shows starter chips only when chat is empty and hides them after first message", async () => {
    vi.spyOn(api, "streamCopilotMessage").mockResolvedValue({
      reply: "Halo kak!",
      thread_id: "t1",
      products: [],
      citations: [],
      suggestions: [{ label: "Should not render", prompt: "ignored" }],
      ui_actions: [],
      guardrail: "greeting",
      took_ms: 10,
    });

    const { container, getByText, queryByText, getByLabelText } = render(
      <CartProvider>
        <CopilotChat />
      </CartProvider>
    );
    openPanel(container);
    expect(getByText("Ukuran saya TB 170 BB 65")).toBeTruthy();
    expect(getByText("Info bayar QRIS & retur")).toBeTruthy();

    const input = getByLabelText("Ketik pesan untuk Admin Toko Marcell");
    fireEvent.change(input, { target: { value: "Halo" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => expect(getByText("Halo kak!")).toBeTruthy());
    // Starter chips disappear after first message, and follow-up suggestions are not rendered
    expect(queryByText("Coba tanya:")).toBeNull();
    expect(queryByText("Should not render")).toBeNull();
  });

  it("shows product-page chips when pageAsin is set", () => {
    const { getByText } = render(
      <CartProvider>
        <CopilotChat embedded pageAsin="B01" pageTitle="Chino Pant" />
      </CartProvider>
    );
    expect(getByText("Bahannya panas nggak?")).toBeTruthy();
  });

  it("opens the floating panel with product attached on a copilot:open event", async () => {
    const { container, findByText } = render(
      <CartProvider>
        <CopilotChat />
      </CartProvider>
    );
    expect(container.querySelector('[role="dialog"]')).toBeNull();

    act(() => {
      window.dispatchEvent(
        new CustomEvent("copilot:open", { detail: { asin: "B09", title: "Dickies 874" } })
      );
    });

    expect(await findByText(/Dickies 874/)).toBeTruthy();
    expect(container.querySelector('[role="dialog"]')).toBeTruthy();
    expect(await findByText("Bahannya panas nggak?")).toBeTruthy();
  });

  it("allows searching and attaching products via the paperclip menu", async () => {
    vi.spyOn(api, "getProducts").mockResolvedValue({
      items: [
        { id: 1, asin: "B01", title: "Levi's 501 Original", priceIdr: 899000 },
        { id: 2, asin: "B02", title: "Dickies 874 Work Pant", priceIdr: 599000 },
      ],
      total: 2,
    });
    const streamSpy = vi.spyOn(api, "streamCopilotMessage").mockResolvedValue({
      reply: "Keduanya bagus kak!",
      thread_id: "t1",
      products: [],
      ui_actions: [],
      guardrail: null,
      took_ms: 15,
    });

    const { container, getByLabelText, findByText, getByText } = render(
      <CartProvider>
        <CopilotChat />
      </CartProvider>
    );
    openPanel(container);

    fireEvent.click(getByLabelText("Lampirkan produk"));
    expect(await findByText("Levi's 501 Original")).toBeTruthy();

    fireEvent.click(getByText("Levi's 501 Original"));
    fireEvent.click(getByText("Dickies 874 Work Pant"));

    // Both products attached -> comparison starter chip appears when chat is empty
    expect(await findByText("Bandingkan produk yang dilampirkan")).toBeTruthy();

    const input = getByLabelText("Ketik pesan untuk Admin Toko Marcell");
    fireEvent.change(input, { target: { value: "Bandingkan dua celana ini" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => expect(streamSpy).toHaveBeenCalled());
    const callArg = streamSpy.mock.calls[0][0];
    expect(callArg.context.referencedAsins).toEqual(["B01", "B02"]);
  });

  it("shows blinking 'Sedang berpikir...' while waiting and streams markdown reply", async () => {
    let resolveStream;
    let triggerToken;
    vi.spyOn(api, "streamCopilotMessage").mockImplementation(({ onToken }) => {
      triggerToken = onToken;
      return new Promise((resolve) => {
        resolveStream = resolve;
      });
    });

    const { container, getByText, queryByText, getByLabelText } = render(
      <CartProvider>
        <CopilotChat />
      </CartProvider>
    );
    openPanel(container);

    const input = getByLabelText("Ketik pesan untuk Admin Toko Marcell");
    fireEvent.change(input, { target: { value: "Rekomendasi jaket" } });
    fireEvent.keyDown(input, { key: "Enter" });

    // Thinking text appears with animate-pulse before any token arrives
    const thinkingEl = getByText("Sedang berpikir...");
    expect(thinkingEl).toBeTruthy();
    expect(thinkingEl.className).toContain("animate-pulse");

    // First token arrives -> thinking indicator disappears and markdown renders
    act(() => {
      triggerToken("Ini **Pilihan Terbaik**", "Ini **Pilihan Terbaik**");
    });

    expect(queryByText("Sedang berpikir...")).toBeNull();
    const boldEl = getByText("Pilihan Terbaik");
    expect(boldEl.tagName).toBe("STRONG");

    await act(async () => {
      resolveStream({
        reply: "Ini **Pilihan Terbaik** untuk kakak!",
        thread_id: "t1",
        products: [],
        ui_actions: [],
        guardrail: null,
        took_ms: 25,
      });
    });

    expect(getByText("untuk kakak!", { exact: false })).toBeTruthy();
  });

  it("executes an add_to_cart ui_action against the local cart", async () => {
    vi.spyOn(api, "streamCopilotMessage").mockResolvedValue({
      reply: "Sudah mimin masukin ke keranjang ya kak",
      thread_id: "t1",
      products: [],
      suggestions: [],
      ui_actions: [
        {
          type: "add_to_cart",
          id: 5,
          asin: "B01",
          qty: 2,
          size: "M",
          title: "Volcom Chino",
          priceIdr: 495800,
          imageUrl: "https://example.com/chino.jpg",
        },
      ],
      guardrail: null,
      took_ms: 20,
    });

    const { container, getByLabelText } = render(
      <CartProvider>
        <CopilotChat />
      </CartProvider>
    );
    openPanel(container);
    const input = getByLabelText("Ketik pesan untuk Admin Toko Marcell");
    fireEvent.change(input, { target: { value: "masukin celana chinonya dong" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => {
      const stored = JSON.parse(localStorage.getItem("toko-cart-v2") || "[]");
      expect(stored).toHaveLength(1);
      expect(stored[0]).toMatchObject({
        id: 5,
        asin: "B01",
        title: "Volcom Chino",
        priceIdr: 495800,
        imageUrl: "https://example.com/chino.jpg",
        qty: 2,
      });
    });
  });

  it("renders numbered product cards with attach button and no compare button", async () => {
    vi.spyOn(api, "streamCopilotMessage").mockResolvedValue({
      reply: "Ini pilihannya kak",
      thread_id: "t1",
      products: [
        {
          ref: 1,
          asin: "B01",
          id: 5,
          title: "Volcom Chino",
          priceIdr: 495800,
          brand: "Volcom",
        },
      ],
      suggestions: [],
      ui_actions: [],
      guardrail: null,
      took_ms: 20,
    });

    const { container, getByText, queryByText, getByLabelText } = render(
      <CartProvider>
        <CopilotChat />
      </CartProvider>
    );
    openPanel(container);
    const input = getByLabelText("Ketik pesan untuk Admin Toko Marcell");
    fireEvent.change(input, { target: { value: "celana chino" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => expect(getByText("Volcom Chino")).toBeTruthy());
    expect(getByText("1")).toBeTruthy();
    expect(getByText("📎 Lampirkan")).toBeTruthy();
    expect(queryByText("Bandingkan")).toBeNull();
    expect(queryByText("Ukuran?")).toBeNull();
  });

  it("reset calls deleteCopilotThread and clears messages", async () => {
    vi.spyOn(api, "streamCopilotMessage").mockResolvedValue({
      reply: "hi",
      thread_id: "t1",
      products: [],
      suggestions: [],
      ui_actions: [],
      guardrail: "greeting",
      took_ms: 1,
    });
    const delSpy = vi.spyOn(api, "deleteCopilotThread").mockResolvedValue();

    const { container, getByLabelText, queryByText } = render(
      <CartProvider>
        <CopilotChat />
      </CartProvider>
    );
    openPanel(container);
    const input = getByLabelText("Ketik pesan untuk Admin Toko Marcell");
    fireEvent.change(input, { target: { value: "Halo" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(queryByText("hi")).toBeTruthy());

    fireEvent.click(container.querySelector('button[aria-label="Reset percakapan"]'));
    await waitFor(() => expect(delSpy).toHaveBeenCalled());
    expect(queryByText("hi")).toBeNull();
  });
});
