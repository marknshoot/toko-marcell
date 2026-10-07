"use client";

import { useState, useRef, useEffect, useContext, useCallback } from "react";
import Link from "next/link";
import { CartContext } from "./CartProvider";
import {
  sendCopilotMessage,
  deleteCopilotThread,
  getCopilotThread,
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
  { label: "Ongkir ke Surabaya berapa?", prompt: "Ongkir ke Surabaya berapa dan berapa lama?" },
  { label: "Jaket buat hujan", prompt: "Ada jaket yang bagus buat hujan?" },
];

const PRODUCT_CHIPS = [
  { label: "Ukuran saya pas yang mana?", prompt: "Untuk produk ini, ukuran saya pas yang mana?" },
  { label: "Bahannya panas nggak?", prompt: "Bahan produk ini panas nggak kalau dipakai siang?" },
  { label: "Yang mirip tapi lebih murah?", prompt: "Ada yang mirip produk ini tapi lebih murah?" },
  { label: "Cocok dipadukan dengan apa?", prompt: "Produk ini cocok dipadukan dengan apa?" },
];

const FIT_BADGE = {
  runs_small: { label: "Cenderung kecil", cls: "bg-amber-100 text-amber-800 border-amber-200" },
  runs_large: { label: "Cenderung besar", cls: "bg-sky-100 text-sky-800 border-sky-200" },
  true_to_size: { label: "Sesuai ukuran", cls: "bg-emerald-100 text-emerald-800 border-emerald-200" },
};

export default function CopilotChat({ embedded = false, pageAsin = null, pageTitle = null, autoOpen = false }) {
  const [isOpen, setIsOpen] = useState(embedded || autoOpen);
  const [messages, setMessages] = useState(() => loadCopilotMessages());
  const [inputValue, setInputValue] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [threadId, setThreadId] = useState(() => getCopilotThreadId());
  const [pinned, setPinned] = useState(() =>
    pageAsin && pageTitle ? { asin: pageAsin, title: pageTitle, priceIdr: null } : null
  );
  const [showSizeForm, setShowSizeForm] = useState(false);
  const [sizeTB, setSizeTB] = useState("");
  const [sizeBB, setSizeBB] = useState("");
  const [compareTray, setCompareTray] = useState([]); // [{asin,title,priceIdr}]
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
  }, [messages, isLoading, isOpen]);

  useEffect(() => {
    if (isOpen) setTimeout(() => inputRef.current?.focus(), 150);
  }, [isOpen]);

  // Escape closes the floating panel
  const handleEsc = useCallback(
    (e) => {
      if (e.key === "Escape" && !embedded && isOpen) setIsOpen(false);
    },
    [embedded, isOpen]
  );
  useEffect(() => {
    document.addEventListener("keydown", handleEsc);
    return () => document.removeEventListener("keydown", handleEsc);
  }, [handleEsc]);

  // Other components (e.g. the product-page button) open this chat with a
  // product pinned by dispatching a "copilot:open" window event.
  useEffect(() => {
    function onOpen(e) {
      const { asin, title } = e.detail || {};
      if (asin) setPinned({ asin, title, priceIdr: null });
      setIsOpen(true);
    }
    window.addEventListener("copilot:open", onOpen);
    return () => window.removeEventListener("copilot:open", onOpen);
  }, []);

  async function handleReset() {
    const newTid = resetCopilotThread();
    setThreadId(newTid);
    if (threadId) deleteCopilotThread(threadId);
    setMessages([]);
    setInputValue("");
    setCompareTray([]);
    setShowSizeForm(false);
  }

  function buildContext() {
    const referencedAsins = compareTray.map((c) => c.asin).slice(0, 3);
    return {
      pageAsin: pinned?.asin || pageAsin || null,
      referencedAsins,
    };
  }

  async function handleSend(textToSend = null) {
    const query = (textToSend !== null ? textToSend : inputValue).trim();
    if (!query || isLoading || !threadId) return;

    const userMsg = { role: "user", content: query };
    setMessages((prev) => [...prev, userMsg]);
    setInputValue("");
    setIsLoading(true);

    postEvent({ eventType: "copilot_message", query });

    try {
      const res = await sendCopilotMessage({
        message: query,
        threadId,
        context: buildContext(),
      });

      const assistantMsg = {
        role: "assistant",
        content: res.reply,
        products: res.products || [],
        citations: res.citations || [],
        suggestions: res.suggestions || [],
        uiActions: res.ui_actions || [],
        guardrail: res.guardrail || null,
      };
      setMessages((prev) => [...prev, assistantMsg]);

      // auto-execute size_form ui_action
      if ((res.ui_actions || []).some((a) => a.type === "size_form")) {
        setShowSizeForm(true);
      }
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
      setIsLoading(false);
    }
  }

  function handleKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  function handlePin(product) {
    setPinned({ asin: product.asin, title: product.title, priceIdr: product.priceIdr });
    inputRef.current?.focus();
  }

  function handleCompareToggle(product) {
    setCompareTray((prev) => {
      const exists = prev.find((p) => p.asin === product.asin);
      if (exists) return prev.filter((p) => p.asin !== product.asin);
      if (prev.length >= 3) return prev;
      return [...prev, { asin: product.asin, title: product.title, priceIdr: product.priceIdr }];
    });
  }

  function handleSizeFor(product) {
    setPinned({ asin: product.asin, title: product.title, priceIdr: product.priceIdr });
    setShowSizeForm(true);
  }

  function submitSizeForm() {
    const tb = parseInt(sizeTB, 10);
    const bb = parseInt(sizeBB, 10);
    if (!tb || !bb) return;
    setShowSizeForm(false);
    const ref = pinned ? ` untuk ${pinned.title}` : "";
    handleSend(`Ukuran saya pas yang mana${ref}? TB ${tb} BB ${bb}`);
    setSizeTB("");
    setSizeBB("");
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

  function handleUndo() {
    if (undoItem && removeItem) {
      removeItem(undoItem.id || undoItem.asin);
    }
    setUndoItem(null);
  }

  const starterChips = pinned ? PRODUCT_CHIPS : HOME_CHIPS;

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
                  <div className="flex justify-end">
                    <div className="max-w-[85%] rounded-2xl rounded-tr-xs bg-foreground px-3.5 py-2.5 text-background text-sm leading-relaxed">
                      {msg.content}
                    </div>
                  </div>
                ) : (
                  <div className="flex items-start gap-2">
                    <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-foreground text-background text-[11px] font-bold">TM</div>
                    <div className="max-w-[88%] space-y-2.5">
                      <div className="rounded-2xl rounded-tl-xs border border-border bg-muted/10 px-3.5 py-2.5 text-foreground whitespace-pre-wrap leading-relaxed">
                        {msg.content}
                      </div>

                      {msg.products?.length > 0 && (
                        <div className="space-y-2 pt-1">
                          {msg.products.map((p) => (
                            <ProductCard
                              key={p.asin}
                              product={p}
                              inCompare={compareTray.some((c) => c.asin === p.asin)}
                              onPin={() => handlePin(p)}
                              onCompare={() => handleCompareToggle(p)}
                              onSize={() => handleSizeFor(p)}
                              onAdd={() => handleAddToCart(p)}
                            />
                          ))}
                        </div>
                      )}

                      {msg.suggestions?.length > 0 && (
                        <div className="flex flex-wrap gap-1.5 pt-0.5">
                          {msg.suggestions.map((s, sIdx) => (
                            <button
                              key={sIdx}
                              type="button"
                              onClick={() => handleSend(s.prompt)}
                              className="rounded-full border border-border bg-surface px-2.5 py-1 text-[11px] text-foreground transition hover:bg-muted/15"
                            >
                              {s.label}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            ))}

            {isLoading && (
              <div className="flex items-start gap-2">
                <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-foreground text-background text-[11px] font-bold">TM</div>
                <div className="flex items-center gap-2 rounded-2xl rounded-tl-xs border border-border bg-muted/10 px-3.5 py-2.5 text-xs text-muted">
                  <div className="flex gap-1">
                    <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-foreground" style={{ animationDelay: "0ms" }} />
                    <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-foreground" style={{ animationDelay: "150ms" }} />
                    <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-foreground" style={{ animationDelay: "300ms" }} />
                  </div>
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

          {/* Compare tray */}
          {compareTray.length > 0 && (
            <div className="mx-3 mb-1 flex items-center justify-between gap-2 rounded-lg border border-border bg-muted/10 px-3 py-2 text-[11px]">
              <span className="truncate text-muted">Bandingkan ({compareTray.length}/3): {compareTray.map((c) => c.title.slice(0, 14)).join(", ")}</span>
              <button
                type="button"
                onClick={() => handleSend(`Bandingkan produk: ${compareTray.map((c) => c.title).join(" vs ")}`)}
                className="shrink-0 rounded-md bg-foreground px-2 py-1 font-medium text-background"
              >
                Bandingkan sekarang
              </button>
            </div>
          )}

          {/* Size form */}
          {showSizeForm && (
            <div className="mx-3 mb-1 rounded-lg border border-border bg-surface px-3 py-2.5">
              <p className="mb-1.5 text-[11px] font-medium text-foreground">Isi tinggi & berat badan kakak:</p>
              <div className="flex items-center gap-2">
                <input
                  type="number"
                  inputMode="numeric"
                  value={sizeTB}
                  onChange={(e) => setSizeTB(e.target.value)}
                  placeholder="TB (cm)"
                  aria-label="Tinggi badan dalam cm"
                  className="w-20 rounded-lg border border-border bg-background px-2 py-1.5 text-xs"
                />
                <input
                  type="number"
                  inputMode="numeric"
                  value={sizeBB}
                  onChange={(e) => setSizeBB(e.target.value)}
                  placeholder="BB (kg)"
                  aria-label="Berat badan dalam kg"
                  className="w-20 rounded-lg border border-border bg-background px-2 py-1.5 text-xs"
                />
                <button type="button" onClick={submitSizeForm} className="rounded-lg bg-foreground px-3 py-1.5 text-xs font-medium text-background">
                  Cek
                </button>
                <button type="button" onClick={() => setShowSizeForm(false)} className="text-xs text-muted hover:underline">
                  Batal
                </button>
              </div>
            </div>
          )}

          {/* Pinned product chip */}
          {pinned && (
            <div className="mx-3 mb-1 flex items-center justify-between gap-2 rounded-lg border border-border bg-muted/10 px-3 py-1.5 text-[11px]">
              <span className="truncate text-foreground">🔖 Sedang dilihat: {pinned.title}{pinned.priceIdr ? ` — ${formatRp(pinned.priceIdr)}` : ""}</span>
              <button type="button" onClick={() => setPinned(null)} aria-label="Lepas produk" className="shrink-0 text-muted hover:text-foreground">✕</button>
            </div>
          )}

          {/* Input */}
          <div className="border-t border-border bg-surface p-3">
            <div className="flex items-center gap-2">
              <input
                ref={inputRef}
                type="text"
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Tanya style, ukuran, atau kebijakan toko..."
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

function ProductCard({ product, inCompare, onPin, onCompare, onSize, onAdd }) {
  const fit = product.fit;
  const badge = fit?.label ? FIT_BADGE[fit.label] : null;
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
          {badge && (
            <span className={`mt-1 inline-block rounded-full border px-1.5 py-0.5 text-[9px] font-medium ${badge.cls}`} title={fit.phrasing || ""}>
              {badge.label}
            </span>
          )}
        </div>
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        <CardBtn onClick={onPin}>Tanya</CardBtn>
        <CardBtn onClick={onCompare} active={inCompare}>{inCompare ? "✓ Bandingkan" : "Bandingkan"}</CardBtn>
        <CardBtn onClick={onSize}>Ukuran?</CardBtn>
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
