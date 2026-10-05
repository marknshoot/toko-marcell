"use client";

import { useState } from "react";
import dynamic from "next/dynamic";

const CopilotChat = dynamic(() => import("./CopilotChat"), { ssr: false });

const CHIPS = [
  { label: "Ukuran saya pas yang mana?", prompt: "Untuk produk ini, ukuran saya pas yang mana?" },
  { label: "Bahannya panas nggak?", prompt: "Bahan produk ini panas nggak kalau dipakai siang?" },
  { label: "Yang mirip tapi lebih murah?", prompt: "Ada yang mirip produk ini tapi lebih murah?" },
];

const FIT_BADGE = {
  runs_small: { label: "Cenderung kecil", cls: "bg-amber-100 text-amber-800 border-amber-200" },
  runs_large: { label: "Cenderung besar", cls: "bg-sky-100 text-sky-800 border-sky-200" },
  true_to_size: { label: "Sesuai ukuran", cls: "bg-emerald-100 text-emerald-800 border-emerald-200" },
};

export default function ProductCopilotEntry({ asin, title, fit = null }) {
  const [open, setOpen] = useState(false);
  const badge = fit?.label ? FIT_BADGE[fit.label] : null;

  return (
    <div className="rounded-xl border border-border bg-muted/5 p-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm font-medium text-foreground">💬 Tanya soal produk ini</p>
        {badge && (
          <span className={`rounded-full border px-2 py-0.5 text-[10px] font-medium ${badge.cls}`} title={fit.phrasing || ""}>
            {badge.label}
          </span>
        )}
      </div>
      {fit?.phrasing && <p className="mt-1 text-[11px] text-muted">{fit.phrasing}</p>}
      <div className="mt-2 flex flex-wrap gap-1.5">
        {CHIPS.map((c, i) => (
          <button
            key={i}
            type="button"
            onClick={() => setOpen(true)}
            className="rounded-full border border-border bg-surface px-2.5 py-1 text-[11px] text-foreground transition hover:bg-muted/15"
          >
            {c.label}
          </button>
        ))}
      </div>

      {open && (
        <div className="fixed inset-0 z-50 flex items-end justify-center sm:items-center sm:justify-end sm:pr-6 sm:pb-6">
          <div className="absolute inset-0 bg-black/20" onClick={() => setOpen(false)} aria-hidden="true" />
          <div className="relative w-full sm:w-[420px]">
            <CopilotChat embedded pageAsin={asin} pageTitle={title} autoOpen />
          </div>
        </div>
      )}
    </div>
  );
}
