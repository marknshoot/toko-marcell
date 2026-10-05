import { render, fireEvent, waitFor } from "@testing-library/react";
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

  it("has no image upload control (text-only, §4)", () => {
    const { container } = render(
      <CartProvider>
        <CopilotChat />
      </CartProvider>
    );
    openPanel(container);
    expect(container.querySelector('input[type="file"]')).toBeNull();
  });

  it("shows home starter chips when empty", () => {
    const { container, getByText } = render(
      <CartProvider>
        <CopilotChat />
      </CartProvider>
    );
    openPanel(container);
    expect(getByText("Ukuran saya TB 170 BB 65")).toBeTruthy();
    expect(getByText("Ongkir ke Surabaya berapa?")).toBeTruthy();
  });

  it("shows product-page chips when pageAsin is set", () => {
    const { getByText } = render(
      <CartProvider>
        <CopilotChat embedded pageAsin="B01" pageTitle="Chino Pant" />
      </CartProvider>
    );
    expect(getByText("Bahannya panas nggak?")).toBeTruthy();
  });

  it("sends a v2 single-message turn and renders the reply + guardrail", async () => {
    const spy = vi.spyOn(api, "sendCopilotMessage").mockResolvedValue({
      reply: "Halo kak!",
      thread_id: "t1",
      products: [],
      citations: [],
      suggestions: [{ label: "Ukuran saya pas yang mana?", prompt: "TB 170 BB 65" }],
      ui_actions: [],
      guardrail: "greeting",
      took_ms: 10,
    });

    const { container, getByText, getByLabelText } = render(
      <CartProvider>
        <CopilotChat />
      </CartProvider>
    );
    openPanel(container);

    const input = getByLabelText("Ketik pesan untuk Admin Toko Marcell");
    fireEvent.change(input, { target: { value: "Halo" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => expect(spy).toHaveBeenCalled());
    const callArg = spy.mock.calls[0][0];
    expect(callArg.message).toBe("Halo");
    expect(typeof callArg.threadId).toBe("string");

    await waitFor(() => expect(getByText("Halo kak!")).toBeTruthy());
  });

  it("renders numbered product cards with a fit badge", async () => {
    vi.spyOn(api, "sendCopilotMessage").mockResolvedValue({
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
          fit: { label: "true_to_size", phrasing: "Sesuai ukuran menurut ulasan" },
        },
      ],
      suggestions: [],
      ui_actions: [],
      guardrail: null,
      took_ms: 20,
    });

    const { container, getByText, getByLabelText } = render(
      <CartProvider>
        <CopilotChat />
      </CartProvider>
    );
    openPanel(container);
    const input = getByLabelText("Ketik pesan untuk Admin Toko Marcell");
    fireEvent.change(input, { target: { value: "celana chino" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => expect(getByText("Volcom Chino")).toBeTruthy());
    expect(getByText("1")).toBeTruthy(); // ref badge
    expect(getByText("Sesuai ukuran")).toBeTruthy(); // fit badge
  });

  it("reset calls deleteCopilotThread and clears messages", async () => {
    vi.spyOn(api, "sendCopilotMessage").mockResolvedValue({
      reply: "hi", thread_id: "t1", products: [], suggestions: [], ui_actions: [], guardrail: "greeting", took_ms: 1,
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
