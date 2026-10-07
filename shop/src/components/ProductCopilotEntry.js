"use client";

// Opens the global floating copilot (mounted once in app/layout.js) with this
// product pinned, instead of rendering a second chat instance on the page.
export default function ProductCopilotEntry({ asin, title }) {
  function openCopilot() {
    window.dispatchEvent(
      new CustomEvent("copilot:open", { detail: { asin, title } })
    );
  }

  return (
    <button
      type="button"
      onClick={openCopilot}
      className="rounded-full border border-foreground bg-background px-6 py-3 text-sm font-medium text-foreground transition hover:bg-foreground hover:text-background"
    >
      Tanya soal produk ini
    </button>
  );
}
