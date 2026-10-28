"use client";

import { useState, useRef, useEffect, useContext, useCallback } from "react";
import Link from "next/link";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { CartContext } from "./CartProvider";
import {
  streamCopilotMessage,
  deleteCopilotThread,
  searchProducts,
  getProducts,
  postEvent,
} from "../lib/api";
import {
  getCopilotThreadId,
  resetCopilotThread,
  loadCopilotMessages,
  saveCopilotMessages,
} from "../lib/session";
import { formatRp } from "../lib/formatRp";
import ProductImage from "./ProductImage";

const HOME_CHIPS = [
  { label: "Outfit kondangan < Rp500rb", prompt: "Outfit kondangan pria di bawah Rp500.000 dong" },
  { label: "Ukuran saya TB 170 BB 65", prompt: "Ukuran saya pas yang mana? TB 170 BB 65" },
  { label: "Jaket buat hujan", prompt: "Ada jaket yang bagus dan tahan air buat hujan?" },
  { label: "Info bayar QRIS & retur", prompt: "Gimana cara pembayaran QRIS dan kebijakan retur toko?" },
];

const PRODUCT_CHIPS = [
  { label: "Ukuran saya pas yang mana?", prompt: "Untuk produk ini, ukuran saya pas yang mana?" },
  { label: "Bahannya panas nggak?", prompt: "Bahan produk ini panas nggak kalau dipakai siang?" },
  { label: "Yang mirip tapi lebih murah?", prompt: "Ada yang mirip produk ini tapi lebih murah?" },
  { label: "Cocok dipadukan dengan apa?", prompt: "Produk ini cocok dipadukan dengan apa?" },
];

const markdownComponents = {
  p: ({ children }) => <p className="mb-2 last:mb-0 leading-relaxed">{children}</p>,
  ul: ({ children }) => <ul className="mb-2 list-disc space-y-1 pl-4 last:mb-0">{children}</ul>,
  ol: ({ children }) => <ol className="mb-2 list-decimal space-y-1 pl-4 last:mb-0">{children}</ol>,
  li: ({ children }) => <li className="leading-relaxed">{children}</li>,
  strong: ({ children }) => <strong className="font-semibold text-foreground">{children}</strong>,
  em: ({ children }) => <em className="italic">{children}</em>,
  code: ({ children }) => (
    <code className="rounded bg-muted/20 px-1 py-0.5 font-mono text-[11px]">{children}</code>
  ),
  table: ({ children }) => (
    <div className="my-2 overflow-x-auto rounded-lg border border-border bg-surface">
      <table className="w-full border-collapse text-left text-[11px]">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="border-b border-border bg-muted/15 text-foreground">{children}</thead>,
  tbody: ({ children }) => <tbody className="divide-y divide-border">{children}</tbody>,
  tr: ({ children }) => <tr className="align-top">{children}</tr>,
  th: ({ children }) => <th className="px-2 py-1.5 font-semibold">{children}</th>,
  td: ({ children }) => <td className="px-2 py-1.5 leading-snug">{children}</td>,
};

function normalizeMarkdown(text) {
  if (!text) return "";
  return text
    .split("\n")
    .map((line) =>
      /\|[\s:-]{2,}\|/.test(line) && /\|[ \t]+\|/.test(line)
        ? line.replace(/\|[ \t]+\|/g, "|\n|")
        : line
    )
    .join("\n");
}

export default function CopilotChat({ embedded = false, pageAsin = null, pageTitle = null, autoOpen = false }) {
  const [isOpen, setIsOpen] = useState(embedded || autoOpen);
  const [messages, setMessages] = useState(() => loadCopilotMessages());
  const [inputValue, setInputValue] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [isThinking, setIsThinking] = useState(false);
  const [threadId, setThreadId] = useState(() => getCopilotThreadId());
  const [attachedProducts, setAttachedProducts] = useState(() =>
    pageAsin && pageTitle ? [{ asin: pageAsin, title: pageTitle, priceIdr: null }] : []
  );
  const [showAttachMenu, setShowAttachMenu] = useState(false);
  const [attachQuery, setAttachQuery] = useState("");
  const [attachResults, setAttachResults] = useState([]);
  const [attachSearching, setAttachSearching] = useState(false);
  const [undoItem, setUndoItem] = useState(null);

  const { addToCart, removeItem } = useContext(CartContext) || {};
  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);
  const panelRef = useRef(null);

  // persist display copy
  useEffect(() => {
    if (messages.length) saveCopilotMessages(messages);
  }, [messages]);

  useEffect(() => {
    if (isOpen) messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isLoading, isThinking, isOpen]);

  useEffect(() => {
    if (isOpen) setTimeout(() => inputRef.current?.focus(), 150);
  }, [isOpen]);

  // Escape closes the attach menu first, then the floating panel
  const handleEsc = useCallback(
    (e) => {
      if (e.key !== "Escape") return;
      if (showAttachMenu) {
        setShowAttachMenu(false);
        return;
      }
      if (!embedded && isOpen) setIsOpen(false);
    },
    [embedded, isOpen, showAttachMenu]
  );
  useEffect(() => {
    document.addEventListener("keydown", handleEsc);
    return () => document.removeEventListener("keydown", handleEsc);
  }, [handleEsc]);

  // Other components (e.g. the product-page button) open this chat with a
  // product attached by dispatching a "copilot:open" window event.
  useEffect(() => {
    function onOpen(e) {
      const { asin, title, priceIdr = null } = e.detail || {};
      if (asin) {
        setAttachedProducts((prev) => {
          if (prev.some((p) => p.asin === asin)) return prev;
          return [...prev.slice(-2), { asin, title, priceIdr }];
        });
      }
      setIsOpen(true);
    }
    window.addEventListener("copilot:open", onOpen);
    return () => window.removeEventListener("copilot:open", onOpen);
  }, []);

  // Debounced product search for the paperclip attach menu
  useEffect(() => {
    if (!showAttachMenu) return;
    let cancelled = false;
    const q = attachQuery.trim();
    const timer = setTimeout(async () => {
      setAttachSearching(true);
      try {
        const res = q
          ? await searchProducts({ q, limit: 6 })
          : await getProducts({ limit: 6 });
        if (!cancelled) setAttachResults(res?.items || []);
      } catch {
        if (!cancelled) setAttachResults([]);
      } finally {
        if (!cancelled) setAttachSearching(false);
      }
    }, q ? 200 : 0);

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [showAttachMenu, attachQuery]);

  async function handleReset() {
    const newTid = resetCopilotThread();
    setThreadId(newTid);
    if (threadId) deleteCopilotThread(threadId);
    setMessages([]);
    setInputValue("");
    setAttachedProducts([]);
    setShowAttachMenu(false);
    setAttachQuery("");
  }

  function buildContext() {
    const referencedAsins = attachedProducts.map((p) => p.asin).slice(0, 3);
    return {
      pageAsin: attachedProducts[0]?.asin || pageAsin || null,
      referencedAsins,
    };
  }

  function toggleAttachProduct(product) {
    setAttachedProducts((prev) => {
      const exists = prev.some((p) => p.asin === product.asin);
      if (exists) return prev.filter((p) => p.asin !== product.asin);
      if (prev.length >= 3) return prev;
      return [
        ...prev,
        {
          id: product.id,
          asin: product.asin,
          title: product.title,
          priceIdr: product.priceIdr,
          imageUrl: product.imageUrl,
        },
      ];
    });
  }

  function removeAttachedProduct(asin) {
    setAttachedProducts((prev) => prev.filter((p) => p.asin !== asin));
  }

  async function handleSend(textToSend = null) {
    const query = (textToSend !== null ? textToSend : inputValue).trim();
    if (!query || isLoading || !threadId) return;

    const userMsg = {
      role: "user",
      content: query,
      attached: attachedProducts.map((p) => ({ asin: p.asin, title: p.title })),
    };
    setMessages((prev) => [...prev, userMsg]);
    setInputValue("");
    setShowAttachMenu(false);
    setIsLoading(true);
    setIsThinking(true);

    postEvent({ eventType: "copilot_message", query });

    let streamingInserted = false;

    try {
      const res = await streamCopilotMessage({
        message: query,
        threadId,
        context: buildContext(),
        onToken: (_chunk, fullText) => {
          setIsThinking(false);
          if (!streamingInserted) {
            streamingInserted = true;
            setMessages((prev) => [
              ...prev,
              { role: "assistant", content: fullText, products: [], citations: [], uiActions: [] },
            ]);
          } else {
            setMessages((prev) => {
              const copy = [...prev];
              const lastIdx = copy.length - 1;
              if (lastIdx >= 0 && copy[lastIdx].role === "assistant") {
                copy[lastIdx] = { ...copy[lastIdx], content: fullText };
              }
              return copy;
            });
          }
        },
      });

      const finalMsg = {
        role: "assistant",
        content: res.reply,
        products: res.products || [],
        citations: res.citations || [],
        uiActions: res.ui_actions || [],
        guardrail: res.guardrail || null,
      };

      setMessages((prev) => {
        if (streamingInserted) {
          const copy = [...prev];
          copy[copy.length - 1] = finalMsg;
          return copy;
        }
        return [...prev, finalMsg];
      });

      executeUiActions(res.ui_actions || []);
    } catch {
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content:
            "Maaf kak, asisten AI lagi ada kendala koneksi. Boleh coba kirim lagi ya!",
          products: [],
        },
      ]);
    } finally {
      setIsThinking(false);
      setIsLoading(false);
    }
  }

  function handleKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  function handleAddToCart(product, qty = 1, size = null) {
    if (!addToCart) return;
    addToCart(
      {
        id: product.id || product.asin,
        asin: product.asin,
        title: product.title,
        priceIdr: product.priceIdr,
        imageUrl: product.imageUrl,
        size,
      },
      qty
    );
    postEvent({ eventType: "copilot_add_to_cart", asin: product.asin, qty, priceIdr: product.priceIdr });
    setUndoItem({ ...product, qty, size });
    setTimeout(() => setUndoItem((cur) => (cur && cur.asin === product.asin ? null : cur)), 6000);
  }

  function executeUiActions(actions) {
    for (const action of actions) {
      if (action.type === "add_to_cart" && action.asin) {
        handleAddToCart(
          {
            id: action.id ?? action.asin,
            asin: action.asin,
            title: action.title,
            priceIdr: action.priceIdr,
            imageUrl: action.imageUrl,
            size: action.size,
          },
          action.qty || 1
        );
      }
    }
  }

  function handleUndo() {
    if (undoItem && removeItem) {
      removeItem(undoItem.id || undoItem.asin);
    }
    setUndoItem(null);
  }

  const starterChips =
    attachedProducts.length >= 2
      ? [
          {
            label: "Bandingkan produk yang dilampirkan",
            prompt: `Tolong bandingkan produk: ${attachedProducts.map((p) => p.title).join(" vs ")}`,
          },
          ...PRODUCT_CHIPS.slice(0, 3),
        ]
      : attachedProducts.length === 1
      ? PRODUCT_CHIPS
      : HOME_CHIPS;

  return (
    <>
      {!embedded && !isOpen && (
        <button
          type="button"
          onClick={() => setIsOpen(true)}
          className="fixed bottom-6 right-6 z-40 flex items-center gap-2.5 rounded-full border border-border bg-foreground px-4 py-3 text-background shadow-xl transition-transform hover:scale-105 active:scale-95 sm:px-5"
          aria-label="Tanya Admin Toko Marcell"
        >
          <div className="relative flex h-7 w-7 items-center justify-center rounded-full bg-background/20 font-bold text-xs">
            TM
            <span className="absolute -top-0.5 -right-0.5 h-2.5 w-2.5 rounded-full bg-emerald-500 ring-2 ring-foreground" />
          </div>
          <span className="text-xs font-bold leading-tight tracking-wide">Tanya Admin</span>
        </button>
      )}

      {(embedded || isOpen) && (
        <div
          ref={panelRef}
          role="dialog"
          aria-label="Chat Admin Toko Marcell"
          className={
            embedded
              ? "flex h-full min-h-[640px] max-h-[820px] w-full flex-col overflow-hidden rounded-2xl border border-border bg-surface shadow-sm"
              : "fixed inset-x-3 bottom-3 sm:inset-x-auto sm:right-6 sm:bottom-6 z-50 flex h-[85vh] max-h-[640px] w-auto sm:w-[420px] flex-col overflow-hidden rounded-2xl border border-border bg-surface shadow-2xl"
          }
        >
          {/* Header */}
          <div className="flex items-center justify-between border-b border-border bg-surface px-4 py-3">
            <div className="flex items-center gap-3">
              <div className="relative flex h-9 w-9 items-center justify-center rounded-full bg-foreground text-background font-bold text-sm">
                TM
                <span className="absolute -top-0.5 -right-0.5 h-2.5 w-2.5 rounded-full bg-emerald-500 ring-2 ring-surface" />
              </div>
              <div>
                <h3 className="text-sm font-semibold leading-tight text-foreground">Admin Toko Marcell</h3>
                <p className="text-[11px] text-muted">AI Personal Stylist</p>
              </div>
            </div>
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={handleReset}
                title="Mulai percakapan baru"
                aria-label="Reset percakapan"
                className="rounded-lg p-1.5 text-muted hover:bg-muted/10 hover:text-foreground"
              >
                <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                </svg>
              </button>
              {!embedded && (
                <button
                  type="button"
                  onClick={() => setIsOpen(false)}
                  title="Tutup"
                  aria-label="Tutup chat"
                  className="rounded-lg p-1.5 text-muted hover:bg-muted/10 hover:text-foreground"
                >
                  <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              )}
            </div>
          </div>

          {/* Messages */}
          <div className="flex-1 overflow-y-auto p-4 space-y-4 text-xs sm:text-sm">
            <div className="flex items-start gap-2">
              <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-foreground text-background text-[11px] font-bold">TM</div>
              <div className="max-w-[85%] rounded-2xl rounded-tl-xs border border-border bg-muted/10 px-3.5 py-2.5 text-foreground">
                <p className="font-medium">Halo kak! 👋</p>
                <p className="mt-1 text-muted leading-relaxed">
                  Mimin bantu cari produk, padu padan outfit sesuai budget, panduan ukuran (TB/BB), sampai info toko. Lagi cari apa nih?
                </p>
              </div>
            </div>

            {messages.length === 0 && (
              <div className="mt-2 pl-9 space-y-1.5">
                <p className="text-[11px] font-medium text-muted">Coba tanya:</p>
                <div className="flex flex-wrap gap-1.5">
                  {starterChips.map((item, idx) => (
                    <button
                      key={idx}
                      type="button"
                      onClick={() => handleSend(item.prompt)}
                      className="rounded-full border border-border bg-surface px-2.5 py-1 text-[11px] text-foreground transition hover:bg-muted/15 text-left"
                    >
                      {item.label}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((msg, index) => (
              <div key={index} className="space-y-2">
                {msg.role === "user" ? (
                  <div className="flex flex-col items-end gap-1">
                    {msg.attached?.length > 0 && (
                      <div className="flex flex-wrap justify-end gap-1 max-w-[85%]">
                        {msg.attached.map((att) => (
                          <span
                            key={att.asin}
                            className="inline-flex items-center gap-1 rounded-full border border-border bg-muted/15 px-2 py-0.5 text-[10px] text-muted"
                          >
                            📎 <span className="truncate max-w-[140px]">{att.title}</span>
                          </span>
                        ))}
                      </div>
                    )}
                    <div className="max-w-[85%] rounded-2xl rounded-tr-xs bg-foreground px-3.5 py-2.5 text-background text-sm leading-relaxed">
                      {msg.content}
                    </div>
                  </div>
                ) : (
                  <div className="flex items-start gap-2">
                    <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-foreground text-background text-[11px] font-bold">TM</div>
                    <div className="max-w-[88%] space-y-2.5">
                      <div className="rounded-2xl rounded-tl-xs border border-border bg-muted/10 px-3.5 py-2.5 text-foreground leading-relaxed">
                        <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
                          {normalizeMarkdown(msg.content)}
                        </ReactMarkdown>
                      </div>

                      {msg.products?.length > 0 && (
                        <div className="space-y-2 pt-1">
                          {msg.products.map((p) => (
                            <ProductCard
                              key={p.asin}
                              product={p}
                              isAttached={attachedProducts.some((a) => a.asin === p.asin)}
                              onAttach={() => toggleAttachProduct(p)}
                              onAdd={() => handleAddToCart(p)}
                            />
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            ))}

            {isThinking && (
              <div className="flex items-start gap-2">
                <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-foreground text-background text-[11px] font-bold">TM</div>
                <div className="flex items-center rounded-2xl rounded-tl-xs border border-border bg-muted/10 px-3.5 py-2.5 text-xs">
                  <span className="animate-pulse font-medium text-muted">Sedang berpikir...</span>
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>

          {/* Undo add-to-cart */}
          {undoItem && (
            <div className="mx-3 mb-1 flex items-center justify-between gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-[11px] text-emerald-900">
              <span className="truncate">✓ Masuk keranjang: {undoItem.title}</span>
              <button type="button" onClick={handleUndo} className="shrink-0 font-semibold underline">
                Batalkan
              </button>
            </div>
          )}

          {/* Paperclip search & attach popover */}
          {showAttachMenu && (
            <div className="mx-3 mb-1.5 rounded-xl border border-border bg-surface p-2.5 shadow-lg">
              <div className="flex items-center justify-between gap-2 pb-2">
                <span className="text-[11px] font-semibold text-foreground">
                  Lampirkan Produk ({attachedProducts.length}/3)
                </span>
                <button
                  type="button"
                  onClick={() => setShowAttachMenu(false)}
                  aria-label="Tutup pencarian produk"
                  className="text-xs text-muted hover:text-foreground"
                >
                  ✕
                </button>
              </div>
              <input
                type="search"
                value={attachQuery}
                onChange={(e) => setAttachQuery(e.target.value)}
                placeholder="Cari nama produk atau brand..."
                aria-label="Cari produk untuk dilampirkan"
                className="w-full rounded-lg border border-border bg-background px-2.5 py-1.5 text-xs text-foreground placeholder:text-muted focus:border-foreground focus:outline-hidden"
              />
              <div className="mt-2 max-h-44 overflow-y-auto space-y-1">
                {attachSearching ? (
                  <p className="py-2 text-center text-[11px] text-muted animate-pulse">Mencari produk...</p>
                ) : attachResults.length === 0 ? (
                  <p className="py-2 text-center text-[11px] text-muted">Produk tidak ditemukan.</p>
                ) : (
                  attachResults.map((item) => {
                    const selected = attachedProducts.some((p) => p.asin === item.asin);
                    const disabled = !selected && attachedProducts.length >= 3;
                    return (
                      <button
                        key={item.asin}
                        type="button"
                        disabled={disabled}
                        onClick={() => toggleAttachProduct(item)}
                        className={`flex w-full items-center justify-between gap-2 rounded-lg px-2 py-1.5 text-left text-xs transition ${
                          selected
                            ? "bg-foreground/10 font-medium text-foreground"
                            : "hover:bg-muted/15 text-foreground disabled:opacity-40"
                        }`}
                      >
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-[11px] font-medium">{item.title}</p>
                          <p className="text-[10px] text-muted">{formatRp(item.priceIdr || 0)}</p>
                        </div>
                        <span className="shrink-0 text-[10px] font-semibold">
                          {selected ? "✓ Terlampir" : "+ Lampirkan"}
                        </span>
                      </button>
                    );
                  })
                )}
              </div>
            </div>
          )}

          {/* Attached product chips */}
          {attachedProducts.length > 0 && (
            <div className="mx-3 mb-1 flex flex-wrap gap-1.5">
              {attachedProducts.map((item) => (
                <div
                  key={item.asin}
                  className="flex max-w-full items-center gap-1.5 rounded-lg border border-border bg-muted/10 px-2.5 py-1 text-[11px]"
                >
                  <span className="truncate text-foreground">
                    📎 {item.title}
                    {item.priceIdr ? ` — ${formatRp(item.priceIdr)}` : ""}
                  </span>
                  <button
                    type="button"
                    onClick={() => removeAttachedProduct(item.asin)}
                    aria-label="Lepas produk"
                    className="shrink-0 text-muted hover:text-foreground"
                  >
                    ✕
                  </button>
                </div>
              ))}
            </div>
          )}

          {/* Input */}
          <div className="border-t border-border bg-surface p-3">
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setShowAttachMenu((v) => !v)}
                title="Cari & lampirkan produk"
                aria-label="Lampirkan produk"
                className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border transition ${
                  showAttachMenu || attachedProducts.length > 0
                    ? "border-foreground bg-foreground/10 text-foreground"
                    : "border-border bg-background text-muted hover:text-foreground"
                }`}
              >
                <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth="2"
                    d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13"
                  />
                </svg>
              </button>
              <input
                ref={inputRef}
                type="text"
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Tanya style, ukuran (TB/BB), atau kebijakan toko..."
                disabled={isLoading}
                aria-label="Ketik pesan untuk Admin Toko Marcell"
                className="flex-1 rounded-xl border border-border bg-background px-3 py-2 text-xs sm:text-sm text-foreground placeholder:text-muted focus:border-foreground focus:outline-hidden"
              />
              <button
                type="button"
                onClick={() => handleSend()}
                disabled={isLoading || !inputValue.trim()}
                aria-label="Kirim pesan"
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-foreground text-background transition disabled:opacity-40 hover:opacity-90 active:scale-95"
              >
                <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M14 5l7 7m0 0l-7 7m7-7H3" />
                </svg>
              </button>
            </div>
            <div className="mt-2 text-center text-[10px] text-muted">
              Admin Toko Marcell AI • Katalog riil &amp; kebijakan toko
            </div>
          </div>
        </div>
      )}
    </>
  );
}

function ProductCard({ product, isAttached, onAttach, onAdd }) {
  return (
    <div className="rounded-xl border border-border bg-surface p-2.5 shadow-xs">
      <div className="flex items-start gap-2.5">
        <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-foreground text-[10px] font-bold text-background">
          {product.ref}
        </span>
        <div className="h-12 w-12 shrink-0 overflow-hidden rounded-lg border border-border/80 bg-white">
          <ProductImage src={product.imageUrl} alt={product.title} padding="p-1" />
        </div>
        <div className="min-w-0 flex-1">
          <Link
            href={product.id ? `/product/${product.id}` : "#"}
            className="line-clamp-1 text-xs font-medium text-foreground hover:underline"
            onClick={() => postEvent({ eventType: "copilot_product_click", asin: product.asin })}
          >
            {product.title}
          </Link>
          <div className="flex items-center gap-1.5 text-[11px]">
            <span className="font-semibold text-foreground">{formatRp(product.priceIdr || 0)}</span>
            {product.brand && <span className="text-muted">· {product.brand}</span>}
          </div>
        </div>
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        <CardBtn onClick={onAttach} active={isAttached}>
          {isAttached ? "✓ Terlampir" : "📎 Lampirkan"}
        </CardBtn>
        <CardBtn onClick={onAdd} primary>+ Keranjang</CardBtn>
      </div>
    </div>
  );
}

function CardBtn({ children, onClick, primary = false, active = false }) {
  const base = "rounded-lg px-2 py-1 text-[10px] font-medium transition active:scale-95";
  const cls = primary
    ? "bg-cta text-white hover:bg-cta-hover"
    : active
    ? "border border-foreground bg-foreground/5 text-foreground"
    : "border border-border text-foreground hover:bg-muted/15";
  return (
    <button type="button" onClick={onClick} className={`${base} ${cls}`}>
      {children}
    </button>
  );
}
