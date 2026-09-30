"use client";

import { useState, useRef, useEffect, useContext } from "react";
import Link from "next/link";
import Image from "next/image";
import { CartContext } from "./CartProvider";
import { sendCopilotMessage } from "../lib/api";
import { formatRp } from "../lib/formatRp";
import ProductImage from "./ProductImage";

const QUICK_PROMPTS = [
  { label: "👗 Outfit pesta under 300rb", text: "Rekomendasikan baju pesta wanita di bawah Rp 300.000" },
  { label: "📏 Cek ukuran (TB 170cm, BB 65kg)", text: "Tinggi badan 170 cm berat 65 kg, rekomendasi ukuran kemeja atau kaos yang pas?" },
  { label: "👖 Beda Levis 501 vs 511", text: "Apa perbedaan potongan Levis 501 vs 511?" },
  { label: "🚚 Info pengiriman & QRIS", text: "Bagaimana kebijakan pengiriman dan pembayaran QRIS di Toko Marcell?" },
];

const MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024;

export default function CopilotChat({ embedded = false }) {
  const [isOpen, setIsOpen] = useState(embedded ? true : false);
  const [messages, setMessages] = useState([]);
  const [inputValue, setInputValue] = useState("");
  const [selectedImage, setSelectedImage] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [addedItemNotice, setAddedItemNotice] = useState(null);

  const { addToCart } = useContext(CartContext) || { addToCart: () => {} };
  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);
  const fileInputRef = useRef(null);

  useEffect(() => {
    if (isOpen) {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, isLoading, isOpen]);

  useEffect(() => {
    if (isOpen) {
      setTimeout(() => inputRef.current?.focus(), 150);
    }
  }, [isOpen]);

  function handleImageSelect(e) {
    const file = e.target.files?.[0];
    if (!file) return;

    if (file.size > MAX_IMAGE_SIZE_BYTES) {
      alert("Ukuran foto maksimal 5 MB.");
      return;
    }

    const reader = new FileReader();
    reader.onload = () => {
      setSelectedImage({
        file,
        preview: URL.createObjectURL(file),
        base64: reader.result,
      });
    };
    reader.readAsDataURL(file);
    e.target.value = "";
  }

  function handleRemoveImage() {
    if (selectedImage?.preview) {
      URL.revokeObjectURL(selectedImage.preview);
    }
    setSelectedImage(null);
  }

  function handleReset() {
    setMessages([]);
    handleRemoveImage();
    setInputValue("");
  }

  async function handleSend(textToSend = null) {
    const query = (textToSend !== null ? textToSend : inputValue).trim();
    const hasImage = Boolean(selectedImage?.base64);

    if (!query && !hasImage) return;

    const userMsg = {
      role: "user",
      content: query || "Tolong carikan produk fashion yang mirip dengan foto ini.",
      imageUrl: selectedImage?.preview || null,
    };

    const newMessages = [...messages, userMsg];
    setMessages(newMessages);
    setInputValue("");
    const imgBase64 = selectedImage?.base64 || null;
    handleRemoveImage();
    setIsLoading(true);

    try {
      const apiMessages = newMessages.map((m) => ({
        role: m.role,
        content: m.content,
        imageUrl: m.imageUrl || null,
      }));

      const res = await sendCopilotMessage({
        messages: apiMessages,
        imageUrl: imgBase64,
      });

      const assistantMsg = {
        role: "assistant",
        content: res.reply,
        products: res.products || [],
        citations: res.citations || [],
        toolsUsed: res.tool_calls || [],
      };

      setMessages((prev) => [...prev, assistantMsg]);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: "Maaf kak, saat ini asisten AI sedang mengalami sedikit kendala koneksi. Boleh coba kirim pesan kembali ya!",
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

  function handleAddProductToCart(product) {
    addToCart(
      {
        id: product.id || product.asin,
        asin: product.asin,
        title: product.title,
        priceIdr: product.priceIdr,
        imageUrl: product.imageUrl,
      },
      1
    );

    setAddedItemNotice(product.title);
    setTimeout(() => setAddedItemNotice(null), 2500);
  }

  function renderFormattedText(text) {
    if (!text) return null;

    const lines = text.split("\n");
    return (
      <div className="space-y-1.5 leading-relaxed">
        {lines.map((line, idx) => {
          if (!line.trim()) {
            return <div key={idx} className="h-1" />;
          }

          const parts = line.split(/(\*\*.*?\*\*)/g);
          const parsed = parts.map((part, pIdx) => {
            if (part.startsWith("**") && part.endsWith("**")) {
              return (
                <strong key={pIdx} className="font-semibold text-foreground">
                  {part.slice(2, -2)}
                </strong>
              );
            }
            return part;
          });

          if (line.trim().startsWith("- ") || line.trim().startsWith("• ") || line.trim().startsWith("* ")) {
            const bulletContent = line.trim().replace(/^[-•*]\s+/, "");
            const bulletParts = bulletContent.split(/(\*\*.*?\*\*)/g);
            const parsedBullet = bulletParts.map((part, pIdx) => {
              if (part.startsWith("**") && part.endsWith("**")) {
                return (
                  <strong key={pIdx} className="font-semibold text-foreground">
                    {part.slice(2, -2)}
                  </strong>
                );
              }
              return part;
            });
            return (
              <div key={idx} className="flex items-start gap-1.5 pl-1">
                <span className="text-muted">•</span>
                <span className="flex-1">{parsedBullet}</span>
              </div>
            );
          }

          const matchNum = line.trim().match(/^(\d+)\.\s+(.*)$/);
          if (matchNum) {
            const numContent = matchNum[2];
            const numParts = numContent.split(/(\*\*.*?\*\*)/g);
            const parsedNum = numParts.map((part, pIdx) => {
              if (part.startsWith("**") && part.endsWith("**")) {
                return (
                  <strong key={pIdx} className="font-semibold text-foreground">
                    {part.slice(2, -2)}
                  </strong>
                );
              }
              return part;
            });
            return (
              <div key={idx} className="flex items-start gap-1.5 pl-1">
                <span className="font-medium text-muted">{matchNum[1]}.</span>
                <span className="flex-1">{parsedNum}</span>
              </div>
            );
          }

          return <p key={idx}>{parsed}</p>;
        })}
      </div>
    );
  }

  return (
    <>
      {addedItemNotice && (
        <div className="fixed top-5 right-5 z-50 flex items-center gap-2 rounded-lg bg-foreground px-4 py-2.5 text-xs font-medium text-background shadow-lg animate-in fade-in slide-in-from-top-2 duration-150">
          <span>✓ Berhasil ditambah ke keranjang:</span>
          <span className="max-w-[200px] truncate underline">{addedItemNotice}</span>
        </div>
      )}

      {!embedded && !isOpen && (
        <button
          type="button"
          onClick={() => setIsOpen(true)}
          className="fixed bottom-6 right-6 z-40 flex items-center gap-2.5 rounded-full border border-border bg-foreground px-4 py-3 text-background shadow-xl transition-transform hover:scale-105 active:scale-95 sm:px-5"
          aria-label="Buka Chat AI Copilot Toko Marcell"
        >
          <div className="relative flex h-7 w-7 items-center justify-center rounded-full bg-background/20 font-bold text-xs">
            TM
            <span className="absolute -top-0.5 -right-0.5 h-2.5 w-2.5 rounded-full bg-emerald-500 ring-2 ring-foreground" />
          </div>
          <div className="flex flex-col text-left">
            <span className="text-xs font-bold leading-tight tracking-wide">Tanya Admin Marcell</span>
            <span className="text-[10px] text-background/80 leading-none">AI Shopping Copilot</span>
          </div>
        </button>
      )}

      {(embedded || isOpen) && (
        <div
          className={
            embedded
              ? "flex h-full min-h-[640px] max-h-[820px] w-full flex-col overflow-hidden rounded-2xl border border-border bg-surface shadow-sm"
              : "fixed inset-x-3 bottom-3 sm:inset-x-auto sm:right-6 sm:bottom-6 z-50 flex h-[85vh] max-h-[640px] w-auto sm:w-[420px] flex-col overflow-hidden rounded-2xl border border-border bg-surface shadow-2xl transition-all"
          }
        >
          <div className="flex items-center justify-between border-b border-border bg-surface px-4 py-3">
            <div className="flex items-center gap-3">
              <div className="relative flex h-9 w-9 items-center justify-center rounded-full bg-foreground text-background font-bold text-sm">
                TM
                <span className="absolute -top-0.5 -right-0.5 h-2.5 w-2.5 rounded-full bg-emerald-500 ring-2 ring-surface" />
              </div>
              <div>
                <h3 className="text-sm font-semibold leading-tight text-foreground">
                  Admin Toko Marcell
                </h3>
                <p className="text-[11px] text-muted">Online • AI Personal Stylist</p>
              </div>
            </div>

            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={handleReset}
                title="Mulai Percakapan Baru"
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
                  title="Tutup Chat"
                  className="rounded-lg p-1.5 text-muted hover:bg-muted/10 hover:text-foreground"
                >
                  <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              )}
            </div>
          </div>

          <div className="flex-1 overflow-y-auto p-4 space-y-4 text-xs sm:text-sm">
            <div className="flex flex-col gap-2">
              <div className="flex items-start gap-2">
                <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-foreground text-background text-[11px] font-bold">
                  TM
                </div>
                <div className="max-w-[85%] rounded-2xl rounded-tl-xs border border-border bg-muted/10 px-3.5 py-2.5 text-foreground">
                  <p className="font-medium text-foreground">Halo kak! Selamat datang di Toko Marcell 👋</p>
                  <p className="mt-1 text-muted leading-relaxed">
                    Mimin siap bantu rekomendasi baju pesta, padu padan outfit sesuai budget, konsultasi ukuran (TB/BB), info bahan, hingga cek status pesanan kakak.
                  </p>
                </div>
              </div>

              {messages.length === 0 && (
                <div className="mt-2 pl-9 space-y-1.5">
                  <p className="text-[11px] font-medium text-muted">Contoh pertanyaan cepat:</p>
                  <div className="flex flex-wrap gap-1.5">
                    {QUICK_PROMPTS.map((item, idx) => (
                      <button
                        key={idx}
                        type="button"
                        onClick={() => handleSend(item.text)}
                        className="rounded-full border border-border bg-surface px-2.5 py-1 text-[11px] text-foreground transition hover:bg-muted/15 text-left"
                      >
                        {item.label}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>

            {messages.map((msg, index) => (
              <div key={index} className="space-y-2">
                {msg.role === "user" ? (
                  <div className="flex flex-col items-end gap-1">
                    {msg.imageUrl && (
                      <Image
                        src={msg.imageUrl}
                        alt="Foto pencarian"
                        width={112}
                        height={112}
                        unoptimized
                        className="h-28 w-28 rounded-xl object-cover border border-border"
                      />
                    )}
                    <div className="max-w-[85%] rounded-2xl rounded-tr-xs bg-foreground px-3.5 py-2.5 text-background text-sm leading-relaxed">
                      {msg.content}
                    </div>
                  </div>
                ) : (
                  <div className="flex items-start gap-2">
                    <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-foreground text-background text-[11px] font-bold">
                      TM
                    </div>
                    <div className="max-w-[88%] space-y-2.5">
                      <div className="rounded-2xl rounded-tl-xs border border-border bg-muted/10 px-3.5 py-2.5 text-foreground">
                        {renderFormattedText(msg.content)}
                      </div>

                      {msg.products && msg.products.length > 0 && (
                        <div className="space-y-2 pt-1">
                          <p className="text-[11px] font-semibold text-foreground">
                            Rekomendasi Produk Terkait ({msg.products.length}):
                          </p>
                          <div className="grid grid-cols-1 gap-2">
                            {msg.products.map((p, pIdx) => (
                              <div
                                key={pIdx}
                                className="flex items-center justify-between gap-3 rounded-xl border border-border bg-surface p-2.5 shadow-xs transition hover:border-foreground/30"
                              >
                                <div className="flex items-center gap-2.5 overflow-hidden">
                                  <div className="h-12 w-12 shrink-0 overflow-hidden rounded-lg border border-border/80 bg-white">
                                    <ProductImage src={p.imageUrl} alt={p.title} padding="p-1" />
                                  </div>
                                  <div className="overflow-hidden">
                                    <Link
                                      href={p.id ? `/product/${p.id}` : "#"}
                                      className="line-clamp-1 text-xs font-medium text-foreground hover:underline"
                                    >
                                      {p.title}
                                    </Link>
                                    <div className="flex items-center gap-1.5 text-[11px]">
                                      <span className="font-semibold text-foreground">
                                        {formatRp(p.priceIdr || 0)}
                                      </span>
                                      {p.brand && <span className="text-muted">· {p.brand}</span>}
                                    </div>
                                  </div>
                                </div>

                                <button
                                  type="button"
                                  onClick={() => handleAddProductToCart(p)}
                                  className="shrink-0 rounded-lg bg-cta px-2.5 py-1.5 text-[11px] font-medium text-white transition hover:bg-cta-hover active:scale-95"
                                >
                                  + Keranjang
                                </button>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            ))}

            {isLoading && (
              <div className="flex items-start gap-2">
                <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-foreground text-background text-[11px] font-bold">
                  TM
                </div>
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

          <div className="border-t border-border bg-surface p-3">
            {selectedImage && (
              <div className="mb-2 flex items-center gap-2 rounded-lg border border-border bg-muted/10 p-1.5 w-fit">
                <Image
                  src={selectedImage.preview}
                  alt="Upload preview"
                  width={32}
                  height={32}
                  unoptimized
                  className="h-8 w-8 rounded-md object-cover"
                />
                <span className="text-[11px] text-muted truncate max-w-[140px]">
                  {selectedImage.file.name}
                </span>
                <button
                  type="button"
                  onClick={handleRemoveImage}
                  className="rounded-full p-0.5 text-muted hover:bg-muted/20"
                >
                  <svg className="h-3.5 w-3.5" viewBox="0 0 20 20" fill="currentColor">
                    <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
                  </svg>
                </button>
              </div>
            )}

            <div className="flex items-center gap-2">
              <input
                ref={fileInputRef}
                type="file"
                accept="image/*"
                onChange={handleImageSelect}
                className="hidden"
              />
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                title="Kirim Foto / Visual Search"
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-border text-muted transition hover:bg-muted/10 hover:text-foreground"
              >
                <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3 9a2 2 0 012-2h.93a2 2 0 001.664-.89l.812-1.22A2 2 0 0110.07 4h3.86a2 2 0 011.664.89l.812 1.22A2 2 0 0018.07 7H19a2 2 0 012 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V9z" />
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M15 13a3 3 0 11-6 0 3 3 0 016 0z" />
                </svg>
              </button>

              <input
                ref={inputRef}
                type="text"
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Tanya style, ukuran, atau kebijakan toko..."
                disabled={isLoading}
                className="flex-1 rounded-xl border border-border bg-background px-3 py-2 text-xs sm:text-sm text-foreground placeholder:text-muted focus:border-foreground focus:outline-hidden"
              />

              <button
                type="button"
                onClick={() => handleSend()}
                disabled={isLoading || (!inputValue.trim() && !selectedImage)}
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-foreground text-background transition disabled:opacity-40 hover:opacity-90 active:scale-95"
              >
                <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M14 5l7 7m0 0l-7 7m7-7H3" />
                </svg>
              </button>
            </div>

            <div className="mt-2 text-center text-[10px] text-muted">
              Admin Toko Marcell AI • Berdasarkan 6.000 katalog riil &amp; kebijakan toko
            </div>
          </div>
        </div>
      )}
    </>
  );
}
