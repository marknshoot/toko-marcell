"use client";

import { useState, useRef, useEffect, useContext, useCallback } from "react";
import Link from "next/link";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { CartContext } from "./CartProvider";
import {
  streamCopilotMessage,
  retryCopilotTurn,
  deleteCopilotThread,
  searchProducts,
  getProducts,
  postEvent,
  postCopilotFeedback,
  resumeCopilot,
} from "../lib/api";
import {
  getCopilotThreadId,
  resetCopilotThread,
  loadCopilotMessages,
  saveCopilotMessages,
} from "../lib/session";
import { formatRp } from "../lib/formatRp";
import ProductCard from "./ProductCard";
import ProductImage from "./ProductImage";

const HOME_CHIPS = [
  { label: "Ukuran saya TB 170 BB 65", prompt: "Ukuran saya TB 170 BB 65, cocoknya ukuran apa?" },
  {
    label: "Info QRIS, ongkir & retur",
    prompt: "Gimana cara pembayaran QRIS, pengiriman, dan kebijakan retur toko?",
  },
  { label: "Outfit kondangan < Rp500rb", prompt: "Buatkan outfit kondangan pria di bawah Rp500.000 dong" },
];

const PRODUCT_CHIPS = [
  {
    label: "Ukuran saya pas yang mana?",
    prompt: "Untuk produk ini, ukuran saya pas yang mana? TB 170 BB 65",
  },
  {
    label: "Info QRIS, ongkir & retur",
    prompt: "Gimana cara pembayaran QRIS, pengiriman, dan kebijakan retur toko?",
  },
  {
    label: "Outfit dengan produk ini < Rp1jt",
    prompt: "Buatkan outfit dengan produk ini di bawah Rp1.000.000",
  },
];

const OUTFIT_SLOT_ORDER = ["top", "bottom", "shoes"];
const OUTFIT_SLOT_LABELS = {
  top: "Atasan",
  bottom: "Bawahan",
  shoes: "Sepatu",
  atasan: "Atasan",
  bawahan: "Bawahan",
  sepatu: "Sepatu",
};

const MAX_RETRIES = 3;

const RETRY_COPY = {
  provider_rate_limited: "Asisten AI lagi ramai. Coba lagi sebentar ya kak.",
  provider_timeout: "Jawaban kepotong karena koneksi ke AI lambat.",
  provider_unavailable: "Asisten AI lagi nggak bisa dihubungi.",
  provider_error: "Asisten AI sedang bermasalah.",
  client_rate_limited: "Terlalu banyak permintaan. Tunggu sebentar ya.",
  network: "Koneksi ke asisten terputus.",
  stream_interrupted: "Jawaban terputus di tengah jalan.",
  retry_in_progress: "Permintaan sebelumnya masih diproses.",
  internal_error: "Jawaban ini belum selesai.",
  llm_unavailable: "Asisten AI sedang tidak tersedia. Coba beberapa saat lagi.",
  thread_not_found: "Percakapan ini sudah kedaluwarsa. Silakan mulai percakapan baru.",
  approval_pending: "Konfirmasi keranjang masih menunggu jawaban.",
  checkpoint_unavailable: "Percakapan tersimpan sedang tidak tersedia.",
  default: "Jawaban ini belum selesai.",
};

function retryCopy(code) {
  return RETRY_COPY[code] || RETRY_COPY.default;
}

function retryMeta(failure, attempts = 0) {
  const retryAfter = failure.retryAfter ?? null;
  return {
    code: failure.code || "internal_error",
    retryable: Boolean(failure.retryable),
    retryAfter,
    retryAt: retryAfter ? Date.now() + retryAfter * 1000 : null,
    attempts,
  };
}

function errorAssistantMessage(content, failure, retryContext) {
  return {
    role: "assistant",
    content: content || retryCopy(failure.code),
    products: [],
    outfit: null,
    uiActions: [],
    guardrail: null,
    feedback: null,
    error: retryMeta(failure),
    retryContext,
  };
}

function failureFromDone(res) {
  return {
    code: res.error_code || "internal_error",
    retryable: res.retryable !== false,
    retryAfter: res.retry_after ?? null,
  };
}

function failureFromError(err) {
  const status = err?.status;
  const fallbackCode =
    status === 429
      ? "client_rate_limited"
      : status === 503
        ? "llm_unavailable"
        : status === 404
          ? "thread_not_found"
          : status >= 500
            ? "provider_error"
            : "network";
  return {
    code: err?.code || fallbackCode,
    retryable: err?.retryable ?? !(status === 400 || status === 404 || status === 409 || status === 422),
    retryAfter: err?.retryAfter ?? null,
  };
}

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

function outfitSlots(outfit) {
  if (!outfit || typeof outfit !== "object") return [];
  const slots = outfit.slots;
  if (!slots || typeof slots !== "object") return [];

  const orderedKeys = [
    ...OUTFIT_SLOT_ORDER.filter((key) => slots[key]),
    ...Object.keys(slots).filter((key) => !OUTFIT_SLOT_ORDER.includes(key)),
  ];

  return orderedKeys
    .filter((key) => slots[key]?.asin)
    .map((key) => ({
      key,
      label: OUTFIT_SLOT_LABELS[key] || key,
      product: slots[key],
    }));
}

export default function CopilotChat({ embedded = false, pageAsin = null, pageTitle = null, autoOpen = false }) {
  const [isOpen, setIsOpen] = useState(embedded || autoOpen);
  const [messages, setMessages] = useState(() => loadCopilotMessages());
  const [inputValue, setInputValue] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [statusLabel, setStatusLabel] = useState(null);
  const [isThinking, setIsThinking] = useState(false);
  const [retryingIndex, setRetryingIndex] = useState(null);
  const [threadId, setThreadId] = useState(() => getCopilotThreadId());
  const [attachedProducts, setAttachedProducts] = useState(() =>
    pageAsin && pageTitle ? [{ asin: pageAsin, title: pageTitle, priceIdr: null }] : []
  );
  const [showAttachMenu, setShowAttachMenu] = useState(false);
  const [attachQuery, setAttachQuery] = useState("");
  const [attachResults, setAttachResults] = useState([]);
  const [attachSearching, setAttachSearching] = useState(false);
  const [undoItem, setUndoItem] = useState(null);
  const [toast, setToast] = useState(null);

  const { addToCart, removeItem } = useContext(CartContext) || {};
  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);
  const panelRef = useRef(null);
  const toastTimerRef = useRef(null);

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

  useEffect(() => {
    return () => {
      if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
    };
  }, []);

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
    const retryContext = buildContext();
    setMessages((prev) => [...prev.map(expireRetry).map(expirePendingApproval), userMsg]);
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
        context: retryContext,
        onStatus: (label) => {
          // Progress from the server ("Mencari produk…"). It replaces the generic text and stays
          // until the next token arrives, even if a first part of the reply is already on screen.
          setStatusLabel(label);
          setIsThinking(true);
        },
        onToken: (_chunk, fullText) => {
          setStatusLabel(null);
          setIsThinking(false);
          if (!streamingInserted) {
            streamingInserted = true;
            setMessages((prev) => [
              ...prev,
              { role: "assistant", content: fullText, products: [], outfit: null, uiActions: [], feedback: null },
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

      if (res.error) {
        // The turn failed but the stream still finished cleanly: keep the bubble in place and
        // attach a retry action instead of showing a dead end.
        const failure = failureFromDone(res);
        const content = res.partial ? res.reply : retryCopy(failure.code);
        setMessages((prev) => {
          const copy = [...prev];
          if (streamingInserted && copy[copy.length - 1]?.role === "assistant") {
            copy[copy.length - 1] = errorAssistantMessage(content, failure, retryContext);
            return copy;
          }
          return [...prev, errorAssistantMessage(content, failure, retryContext)];
        });
        return;
      }

      const finalMsg = {
        role: "assistant",
        content: res.reply,
        products: res.products || [],
        outfit: res.outfit || null,
        uiActions: res.ui_actions || [],
        guardrail: res.guardrail || null,
        feedback: null,
        ...approvalFields(res),
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
    } catch (err) {
      const failure = failureFromError(err);
      setMessages((prev) => {
        const copy = [...prev];
        const last = copy[copy.length - 1];
        const content =
          err?.partialText ||
          (streamingInserted && last?.role === "assistant" ? last.content : "");
        const message = errorAssistantMessage(content, failure, retryContext);
        if (streamingInserted && last?.role === "assistant") {
          copy[copy.length - 1] = message;
        } else {
          copy.push(message);
        }
        return copy;
      });
    } finally {
      setStatusLabel(null);
      setIsThinking(false);
      setIsLoading(false);
    }
  }

  async function handleRetry(index) {
    const target = messages[index];
    if (
      !target ||
      target.role !== "assistant" ||
      !target.error?.retryable ||
      isLoading ||
      !threadId
    ) {
      return;
    }
    if (target.error.retryAt && target.error.retryAt > Date.now()) return;

    setRetryingIndex(index);
    setIsLoading(true);
    setIsThinking(true);
    setStatusLabel(null);

    let receivedToken = false;

    try {
      const res = await retryCopilotTurn({
        threadId,
        context: target.retryContext || null,
        onStatus: (label) => {
          setStatusLabel(label);
          setIsThinking(true);
        },
        onToken: (_chunk, fullText) => {
          receivedToken = true;
          setStatusLabel(null);
          setIsThinking(false);
          setMessages((prev) =>
            prev.map((m, i) =>
              i === index
                ? { ...m, content: fullText, error: null, products: [], outfit: null, uiActions: [] }
                : m
            )
          );
        },
      });

      if (res.error) {
        const failure = failureFromDone(res);
        throw Object.assign(new Error(res.reply || retryCopy(failure.code)), failure);
      }

      setMessages((prev) =>
        prev.map((m, i) =>
          i === index
            ? {
                role: "assistant",
                content: res.reply,
                products: res.products || [],
                outfit: res.outfit || null,
                uiActions: res.ui_actions || [],
                guardrail: res.guardrail || null,
                feedback: null,
                ...approvalFields(res),
              }
            : m
        )
      );
      executeUiActions(res.ui_actions || []);
    } catch (err) {
      const failure = failureFromError(err);
      setMessages((prev) =>
        prev.map((m, i) => {
          if (i !== index) return m;
          const attempts = (m.error?.attempts || 0) + 1;
          const retryable = Boolean(failure.retryable) && attempts < MAX_RETRIES;
          return {
            ...m,
            content: receivedToken ? m.content : retryCopy(failure.code),
            error: { ...retryMeta(failure, attempts), retryable },
          };
        })
      );
    } finally {
      setStatusLabel(null);
      setIsThinking(false);
      setIsLoading(false);
      setRetryingIndex(null);
    }
  }

  async function handleApproval(index, approve) {
    const target = messages[index];
    if (!target || target.approvalState !== "pending" || isLoading || !threadId) return;

    setMessages((prev) => prev.map((m, i) => (i === index ? { ...m, approvalState: "answering", approvalError: null } : m)));
    setIsLoading(true);
    setIsThinking(true);
    try {
      const res = await resumeCopilot({ threadId, approve });
      setMessages((prev) => [
        ...prev.map((m, i) => (i === index ? { ...m, approvalState: approve ? "approved" : "declined" } : m)),
        {
          role: "assistant",
          content: res.reply,
          products: res.products || [],
          outfit: res.outfit || null,
          uiActions: res.ui_actions || [],
          guardrail: null,
          feedback: null,
          ...approvalFields(res),
        },
      ]);
      executeUiActions(res.ui_actions || []);
    } catch (err) {
      // 404 / 409: the request is gone (answered elsewhere, thread cleaned up). Anything else is
      // a network problem and the shopper may try again.
      const gone = err?.status === 404 || err?.status === 409;
      setMessages((prev) =>
        prev.map((m, i) =>
          i === index
            ? {
                ...m,
                approvalState: gone ? "expired" : "pending",
                approvalError: gone ? null : "Belum tersambung. Coba lagi ya kak.",
              }
            : m
        )
      );
    } finally {
      setIsThinking(false);
      setIsLoading(false);
    }
  }

  async function handleFeedback(index, rating) {
    const message = messages[index];
    if (!message || message.role !== "assistant" || message.feedback === rating) {
      return;
    }

    // Optimistic: the shopper sees the vote immediately; the POST is best-effort.
    setMessages((prev) => {
      const copy = [...prev];
      if (copy[index]?.role !== "assistant") return prev;
      copy[index] = { ...copy[index], feedback: rating };
      return copy;
    });

    try {
      await postCopilotFeedback({ threadId, rating });
    } catch {
      // Keep the local selection; a failed feedback write must not interrupt chat.
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

  function showToast(message) {
    setToast(message);
    if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
    toastTimerRef.current = setTimeout(() => setToast(null), 4000);
  }

  function handleAddOutfit(outfit) {
    const slots = outfitSlots(outfit);
    if (slots.length === 0 || !addToCart) return;

    slots.forEach(({ product }) => handleAddToCart(product, 1));
    // A set-level undo is not offered; clear the per-item undo banner.
    setUndoItem(null);
    showToast(`✓ 1 set outfit (${slots.length} item) masuk keranjang`);
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

                      {msg.error && (
                        <RetryNotice
                          error={msg.error}
                          retrying={retryingIndex === index}
                          disabled={isLoading}
                          onRetry={() => handleRetry(index)}
                          onReset={handleReset}
                        />
                      )}

                      {msg.pendingApproval?.items?.length > 0 && (
                        <ApprovalCard
                          items={msg.pendingApproval.items}
                          state={msg.approvalState}
                          error={msg.approvalError}
                          disabled={isLoading}
                          onApprove={() => handleApproval(index, true)}
                          onDecline={() => handleApproval(index, false)}
                        />
                      )}

                      {!msg.error && (
                        <div className="flex items-center gap-1 pl-0.5">
                        <button
                          type="button"
                          aria-label="Jawaban membantu"
                          title="Jawaban membantu"
                          aria-pressed={msg.feedback === 1}
                          onClick={() => handleFeedback(index, 1)}
                          className={`rounded-md px-1.5 py-0.5 text-xs transition ${
                            msg.feedback === 1
                              ? "bg-emerald-100 text-emerald-800"
                              : "text-muted hover:bg-muted/15 hover:text-foreground"
                          }`}
                        >
                          👍
                        </button>
                        <button
                          type="button"
                          aria-label="Jawaban tidak membantu"
                          title="Jawaban tidak membantu"
                          aria-pressed={msg.feedback === -1}
                          onClick={() => handleFeedback(index, -1)}
                          className={`rounded-md px-1.5 py-0.5 text-xs transition ${
                            msg.feedback === -1
                              ? "bg-rose-100 text-rose-800"
                              : "text-muted hover:bg-muted/15 hover:text-foreground"
                          }`}
                        >
                          👎
                        </button>
                        </div>
                      )}

                      {outfitSlots(msg.outfit).length > 0 ? (
                        <OutfitCard outfit={msg.outfit} onAddAll={handleAddOutfit} />
                      ) : msg.products?.length > 0 ? (
                        <div className="space-y-2 pt-1">
                          {msg.products.map((p) => (
                            <ProductCard
                              key={p.asin}
                              variant="chat"
                              id={p.id}
                              title={p.title}
                              priceIdr={p.priceIdr}
                              brand={p.brand}
                              imageUrl={p.imageUrl}
                              refNumber={p.ref}
                              onNavigate={() =>
                                postEvent({ eventType: "copilot_product_click", asin: p.asin })
                              }
                            >
                              <CardBtn
                                onClick={() => toggleAttachProduct(p)}
                                active={attachedProducts.some((a) => a.asin === p.asin)}
                              >
                                {attachedProducts.some((a) => a.asin === p.asin)
                                  ? "✓ Terlampir"
                                  : "📎 Lampirkan"}
                              </CardBtn>
                              <CardBtn onClick={() => handleAddToCart(p)} primary>
                                + Keranjang
                              </CardBtn>
                            </ProductCard>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  </div>
                )}
              </div>
            ))}

            {isThinking && (
              <div className="flex items-start gap-2">
                <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-foreground text-background text-[11px] font-bold">TM</div>
                <div className="flex items-center rounded-2xl rounded-tl-xs border border-border bg-muted/10 px-3.5 py-2.5 text-xs">
                  <span className="animate-pulse font-medium text-muted">{statusLabel || "Sedang berpikir..."}</span>
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>

          {/* Outfit set toast */}
          {toast && (
            <div
              role="status"
              data-testid="copilot-toast"
              className="mx-3 mb-1 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-[11px] font-medium text-emerald-900"
            >
              {toast}
            </div>
          )}

          {/* Undo add-to-cart */}
          {!toast && undoItem && (
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

function OutfitCard({ outfit, onAddAll }) {
  const slots = outfitSlots(outfit);
  if (slots.length === 0) return null;

  const totalIdr =
    Number(outfit.total_idr) ||
    slots.reduce((sum, { product }) => sum + (product.priceIdr || 0), 0);
  const budgetIdr = Number(outfit.budget_idr) || 0;

  return (
    <div
      data-testid="outfit-card"
      className="rounded-xl border border-border bg-surface p-3 shadow-xs"
    >
      <div className="flex flex-wrap items-center justify-between gap-1.5 border-b border-border pb-2">
        <div>
          <p className="text-xs font-semibold text-foreground">Set Outfit Pilihan</p>
          <p className="text-[10px] text-muted">{slots.length} item dipadukan sesuai budget</p>
        </div>
        <div className="flex flex-wrap items-center gap-1">
          <span className="rounded-full bg-foreground px-2 py-0.5 text-[10px] font-semibold text-background">
            Total: {formatRp(totalIdr)}
          </span>
          {budgetIdr > 0 && (
            <span className="rounded-full border border-border bg-muted/10 px-2 py-0.5 text-[10px] font-medium text-muted">
              Budget: {formatRp(budgetIdr)}
            </span>
          )}
        </div>
      </div>

      <div className="mt-2 space-y-1.5">
        {slots.map(({ key, label, product }) => (
          <div
            key={`${key}-${product.asin}`}
            className="flex items-center gap-2 rounded-lg border border-border/70 bg-background/50 p-1.5"
          >
            <span className="shrink-0 rounded-md bg-muted/20 px-1.5 py-0.5 text-[10px] font-semibold text-foreground">
              {label}
            </span>
            <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-foreground text-[10px] font-bold text-background">
              {product.ref}
            </span>
            <div className="h-10 w-10 shrink-0 overflow-hidden rounded-lg border border-border/80 bg-white">
              <ProductImage src={product.imageUrl} alt={product.title} padding="p-0.5" />
            </div>
            <div className="min-w-0 flex-1">
              <Link
                href={product.id ? `/product/${product.id}` : "#"}
                className="line-clamp-1 text-xs font-medium text-foreground hover:underline"
                onClick={() => postEvent({ eventType: "copilot_product_click", asin: product.asin })}
              >
                {product.title}
              </Link>
              <p className="text-[11px] font-semibold text-foreground">
                {formatRp(product.priceIdr || 0)}
              </p>
            </div>
          </div>
        ))}
      </div>

      <button
        type="button"
        onClick={() => onAddAll(outfit)}
        className="mt-2.5 w-full rounded-lg bg-cta px-3 py-2 text-[11px] font-semibold text-white transition hover:bg-cta-hover active:scale-[0.99]"
      >
        🛍️ Masukkan 1 Set ke Keranjang
      </button>
    </div>
  );
}

// A card the server asks us to show before an item goes into the cart. The item, price and
// quantity come from the server; the only thing the shopper sends back is yes or no.
const APPROVAL_STATUS = {
  approved: "✓ Dimasukkan ke keranjang",
  declined: "Dibatalkan, tidak ada yang ditambahkan",
  expired: "Permintaan ini sudah tidak berlaku. Minta lagi ya kak.",
};

function ApprovalCard({ items, state, error, disabled, onApprove, onDecline }) {
  const open = state === "pending" || state === "answering";
  const total = items.reduce((sum, item) => sum + (item.priceIdr || 0) * (item.qty || 1), 0);
  return (
    <div className="rounded-xl border border-border bg-surface p-2.5 shadow-xs" role="group" aria-label="Konfirmasi keranjang">
      <p className="mb-2 text-[11px] font-semibold text-foreground">Masukkan ke keranjang?</p>
      <div className="space-y-2">
        {items.map((item) => (
          <div key={item.asin} className="flex items-start gap-2.5">
            <div className="h-12 w-12 shrink-0 overflow-hidden rounded-lg border border-border/80 bg-white">
              <ProductImage src={item.imageUrl} alt={item.title} padding="p-1" />
            </div>
            <div className="min-w-0 flex-1">
              <p className="line-clamp-2 text-xs font-medium text-foreground">{item.title}</p>
              <p className="text-[11px] text-muted">
                {formatRp(item.priceIdr || 0)} × {item.qty || 1}
                {item.size ? ` · Ukuran ${item.size}` : ""}
              </p>
            </div>
          </div>
        ))}
      </div>
      {items.length > 1 && (
        <p className="mt-2 text-[11px] font-semibold text-foreground">Total {formatRp(total)}</p>
      )}
      {open ? (
        <div className="mt-2.5 flex flex-wrap gap-1.5">
          <button
            type="button"
            onClick={onApprove}
            disabled={disabled}
            className="rounded-lg bg-cta px-2.5 py-1.5 text-[11px] font-semibold text-white transition hover:bg-cta-hover active:scale-95 disabled:opacity-60"
          >
            Setuju, masukkan
          </button>
          <button
            type="button"
            onClick={onDecline}
            disabled={disabled}
            className="rounded-lg border border-border px-2.5 py-1.5 text-[11px] font-medium text-foreground transition hover:bg-muted/15 active:scale-95 disabled:opacity-60"
          >
            Batal
          </button>
        </div>
      ) : (
        <p className="mt-2.5 text-[11px] font-medium text-muted">{APPROVAL_STATUS[state] || ""}</p>
      )}
      {error && <p className="mt-1.5 text-[11px] text-rose-700">{error}</p>}
    </div>
  );
}

// Fields a chat message carries when the server paused to ask for confirmation.
function approvalFields(res) {
  const pending = res?.pending_approval;
  return pending?.items?.length
    ? { pendingApproval: pending, approvalState: "pending", approvalError: null }
    : {};
}

// A card still waiting for an answer is dropped when the shopper moves on (the server does the same).
function expirePendingApproval(message) {
  return message.approvalState === "pending" ? { ...message, approvalState: "expired" } : message;
}

function RetryNotice({ error, retrying, disabled, onRetry, onReset }) {
  const [, setTick] = useState(0);

  useEffect(() => {
    if (!error.retryAt || Date.now() >= error.retryAt) return undefined;
    const timer = setInterval(() => {
      setTick((n) => n + 1);
      if (Date.now() >= error.retryAt) clearInterval(timer);
    }, 250);
    return () => clearInterval(timer);
  }, [error.retryAt]);

  // Derived on every render (the tick above only forces one while the countdown runs).
  const secondsLeft = secondsUntil(error.retryAt);

  if (!error.retryable) {
    return (
      <div className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-900">
        <p>{retryCopy(error.code)}</p>
        <button
          type="button"
          onClick={onReset}
          className="mt-1.5 font-semibold underline underline-offset-2"
        >
          Mulai percakapan baru
        </button>
      </div>
    );
  }

  const exhausted = error.attempts >= MAX_RETRIES;
  const waiting = secondsLeft > 0;

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-xl border border-border bg-muted/10 px-3 py-2 text-[11px] text-muted">
      <span>
        {exhausted
          ? "Sudah 3 kali dicoba. Mulai percakapan baru biar makin lancar."
          : retryCopy(error.code)}
      </span>
      {!exhausted && (
        <button
          type="button"
          onClick={onRetry}
          disabled={disabled || retrying || waiting}
          className="rounded-lg border border-border px-2 py-0.5 font-semibold text-foreground transition hover:bg-muted/15 disabled:opacity-60"
        >
          {retrying
            ? "Mencoba…"
            : waiting
              ? `Coba lagi (${secondsLeft}s)`
              : `Coba lagi${error.attempts ? ` (${error.attempts + 1}/${MAX_RETRIES})` : ""}`}
        </button>
      )}
      {exhausted && (
        <button
          type="button"
          onClick={onReset}
          className="rounded-lg border border-border px-2 py-0.5 font-semibold text-foreground transition hover:bg-muted/15"
        >
          Mulai percakapan baru
        </button>
      )}
    </div>
  );
}

function secondsUntil(retryAt) {
  if (!retryAt) return 0;
  return Math.max(0, Math.ceil((retryAt - Date.now()) / 1000));
}

function expireRetry(message) {
  return message.error?.retryable
    ? { ...message, error: { ...message.error, retryable: false } }
    : message;
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
