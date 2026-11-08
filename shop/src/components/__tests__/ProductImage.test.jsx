import { fireEvent, render, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import ProductImage from "../ProductImage";

describe("ProductImage", () => {
  it("shows a shimmer until the image has loaded", async () => {
    const { container } = render(
      <ProductImage src="https://example.com/a.jpg" alt="A" />
    );

    expect(container.querySelector(".animate-pulse")).not.toBeNull();

    fireEvent.load(container.querySelector("img"));

    // next/image resolves onLoad after img.decode() settles.
    await waitFor(() =>
      expect(container.querySelector(".animate-pulse")).toBeNull()
    );
    expect(container.querySelector("img").getAttribute("alt")).toBe("A");
  });

  it("falls back to a placeholder when the image fails", async () => {
    const { container, findByText } = render(
      <ProductImage src="https://example.com/missing.jpg" alt="Missing" />
    );

    fireEvent.error(container.querySelector("img"));

    expect(await findByText("No image")).toBeTruthy();
    expect(container.querySelector(".animate-pulse")).toBeNull();
  });

  it("renders the placeholder when there is no src at all", () => {
    const { getByText } = render(<ProductImage src={null} alt="Nothing" />);

    expect(getByText("No image")).toBeTruthy();
  });
});
