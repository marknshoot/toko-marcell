import { render, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import CopilotChat from "../CopilotChat";
import { CartProvider } from "../CartProvider";

describe("CopilotChat image upload validation", () => {
  beforeEach(() => {
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
  });

  it("alerts user when uploaded image exceeds 5MB limit", () => {
    const alertMock = vi.fn();
    window.alert = alertMock;

    const { container } = render(
      <CartProvider>
        <CopilotChat />
      </CartProvider>
    );

    // Open chat drawer
    const openButton = container.querySelector('button[aria-label="Buka Chat AI Copilot Toko Marcell"]');
    if (openButton) {
      fireEvent.click(openButton);
    }

    const fileInput = container.querySelector('input[type="file"]');
    expect(fileInput).toBeTruthy();

    const oversizedFile = new File(["a".repeat(10)], "large.png", { type: "image/png" });
    Object.defineProperty(oversizedFile, "size", { value: 6 * 1024 * 1024 });

    fireEvent.change(fileInput, { target: { files: [oversizedFile] } });

    expect(alertMock).toHaveBeenCalledWith("Ukuran foto maksimal 5 MB.");
  });
});
