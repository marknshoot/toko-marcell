import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const router = vi.hoisted(() => ({ replace: vi.fn() }));

vi.mock("next/navigation", () => ({ useRouter: () => router }));

import CatalogSearch from "../CatalogSearch";

function typeQuery(value) {
  fireEvent.change(screen.getByRole("searchbox"), {
    target: { value },
  });
}

describe("CatalogSearch", () => {
  beforeEach(() => {
    router.replace.mockClear();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("debounces typing into a single replace navigation", () => {
    render(<CatalogSearch q="" department="All" />);

    typeQuery("levis");
    expect(router.replace).not.toHaveBeenCalled();

    act(() => {
      vi.advanceTimersByTime(250);
    });

    expect(router.replace).toHaveBeenCalledTimes(1);
    expect(router.replace).toHaveBeenCalledWith("/?q=levis", { scroll: false });
  });

  it("preserves the department and resets to page 1", () => {
    render(<CatalogSearch q="" department="Women" />);

    typeQuery("levis");
    act(() => {
      vi.advanceTimersByTime(250);
    });

    expect(router.replace).toHaveBeenCalledWith(
      "/?department=Women&q=levis",
      { scroll: false }
    );
  });

  it("does not navigate when the trimmed query already matches the URL", () => {
    render(<CatalogSearch q="levis" department="All" />);

    typeQuery("  levis  ");
    act(() => {
      vi.advanceTimersByTime(500);
    });

    expect(router.replace).not.toHaveBeenCalled();
  });

  it("adopts an external q change (back/forward or shared link)", () => {
    const { rerender } = render(<CatalogSearch q="shoes" department="All" />);
    const input = screen.getByRole("searchbox");
    expect(input.value).toBe("shoes");

    rerender(<CatalogSearch q="levis" department="All" />);
    expect(input.value).toBe("levis");
  });

  it("does not clobber text typed while an older query was in flight", () => {
    const { rerender } = render(<CatalogSearch q="" department="All" />);

    typeQuery("lev");
    act(() => {
      vi.advanceTimersByTime(250);
    });

    // The shopper kept typing while ?q=lev was still being rendered.
    typeQuery("levis");
    rerender(<CatalogSearch q="lev" department="All" />);

    expect(screen.getByRole("searchbox").value).toBe("levis");

    // A genuinely external change still wins.
    rerender(<CatalogSearch q="shoes" department="All" />);
    expect(screen.getByRole("searchbox").value).toBe("shoes");
  });

  it("clears immediately and keeps the department", () => {
    render(<CatalogSearch q="levis" department="Men" />);

    fireEvent.click(screen.getByRole("button", { name: "Clear" }));

    expect(screen.getByRole("searchbox").value).toBe("");
    expect(router.replace).toHaveBeenCalledWith("/?department=Men", {
      scroll: false,
    });
  });

  it("submits the GET form immediately as a fallback path", () => {
    render(<CatalogSearch q="" department="All" />);

    typeQuery("levis");
    fireEvent.submit(screen.getByRole("search"));

    expect(router.replace).toHaveBeenCalledWith("/?q=levis", { scroll: false });
  });

  it("renders a hidden department field so the no-JS form keeps the filter", () => {
    const { container } = render(
      <CatalogSearch q="levis" department="Women" />
    );

    expect(
      container.querySelector('input[type="hidden"][name="department"]').value
    ).toBe("Women");
  });
});
