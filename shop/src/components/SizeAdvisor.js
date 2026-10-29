"use client";

import { useState } from "react";
import { postSizingRecommend } from "../lib/api";

const FIELDS = {
  height_cm: {
    key: "heightCm",
    label: "Tinggi badan (cm)",
    placeholder: "contoh: 170",
    min: 50,
    max: 250,
  },
  weight_kg: {
    key: "weightKg",
    label: "Berat badan (kg)",
    placeholder: "contoh: 65",
    min: 10,
    max: 400,
  },
  foot_length_cm: {
    key: "footLengthCm",
    label: "Panjang kaki (cm)",
    placeholder: "contoh: 26.5",
    min: 10,
    max: 40,
  },
  waist_cm: {
    key: "waistCm",
    label: "Lingkar pinggang (cm)",
    placeholder: "contoh: 80",
    min: 30,
    max: 200,
  },
  head_circumference_cm: {
    key: "headCircumferenceCm",
    label: "Lingkar kepala (cm)",
    placeholder: "contoh: 57",
    min: 30,
    max: 100,
  },
};

const SOURCE_LABELS = {
  standard: "Standar ukuran",
  store_estimate: "Estimasi toko",
  demo: "Data demo",
};

const CONFIDENCE_LABELS = {
  high: "Keyakinan tinggi",
  medium: "Keyakinan sedang",
  low: "Keyakinan rendah",
};

function InputNote({ kind }) {
  if (kind === "dimensions") {
    return (
      <p className="text-sm leading-relaxed text-muted">
        Ini bukan pertanyaan ukuran badan. Baca dimensi dan kapasitas produk di
        bagian detail sebelum membeli.
      </p>
    );
  }
  if (kind === "one_size") {
    return (
      <p className="text-sm leading-relaxed text-muted">
        Produk ini dijual dalam ukuran tunggal (one size), jadi tidak perlu
        mengisi ukuran tubuh.
      </p>
    );
  }
  if (kind === "unsupported") {
    return (
      <p className="text-sm leading-relaxed text-muted">
        Kategori ini belum punya dasar ukuran yang bisa dipertanggungjawabkan.
        Cek detail produk atau tanyakan lewat chat; mimin tidak akan menebak
        ukuran S/M/L.
      </p>
    );
  }
  return null;
}

export default function SizeAdvisor({ asin, info }) {
  const [values, setValues] = useState({});
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  if (!info) return null;

  const required = info.required_inputs || [];
  const fields = required.map((name) => FIELDS[name]).filter(Boolean);
  const showForm = info.applicable && fields.length > 0;

  function update(name, value) {
    setValues((current) => ({ ...current, [name]: value }));
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setError("");
    setResult(null);
    setLoading(true);

    const payload = { asin };
    for (const name of required) {
      const field = FIELDS[name];
      const raw = values[field.key];
      if (raw !== undefined && raw !== "") {
        payload[field.key] = Number(raw);
      }
    }

    try {
      const data = await postSizingRecommend(payload);
      setResult(data);
    } catch (err) {
      setError(err.message || "Gagal menghitung saran ukuran.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <section
      className="mt-6 rounded-2xl border border-border bg-background p-5"
      aria-labelledby="size-advisor-heading"
    >
      <div className="flex items-baseline justify-between gap-3">
        <h2
          id="size-advisor-heading"
          className="text-xs font-semibold uppercase tracking-[0.12em] text-foreground"
        >
          Panduan ukuran
        </h2>
        <span className="text-xs text-muted">Tanpa AI · dihitung di server</span>
      </div>

      {!showForm ? (
        <div className="mt-3">
          {info.reason ? (
            <p className="text-sm leading-relaxed text-muted">{info.reason}</p>
          ) : (
            <InputNote kind={info.size_kind} />
          )}
        </div>
      ) : (
        <form className="mt-3 space-y-3" onSubmit={handleSubmit}>
          <p className="text-sm leading-relaxed text-muted">
            Isi ukuran yang diminta. Kalau ada yang kosong, mimin tidak akan
            menebak ukuran.
          </p>
          <div className="grid gap-3 sm:grid-cols-2">
            {fields.map((field) => (
              <label key={field.key} className="block text-sm text-foreground">
                <span className="mb-1 block font-medium">{field.label}</span>
                <input
                  type="number"
                  inputMode="decimal"
                  min={field.min}
                  max={field.max}
                  step="0.1"
                  value={values[field.key] || ""}
                  onChange={(event) => update(field.key, event.target.value)}
                  placeholder={field.placeholder}
                  className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus:border-foreground"
                />
              </label>
            ))}
          </div>
          <button
            type="submit"
            disabled={loading}
            className="rounded-full bg-foreground px-5 py-2.5 text-sm font-medium text-background transition disabled:opacity-50"
          >
            {loading ? "Menghitung..." : "Lihat saran ukuran"}
          </button>
        </form>
      )}

      {error ? (
        <p role="alert" className="mt-3 text-sm text-red-600">
          {error}
        </p>
      ) : null}

      {result ? (
        <div className="mt-4 rounded-xl border border-border bg-muted/30 p-4">
          <p className="text-sm leading-relaxed text-foreground">
            {result.recommendation}
          </p>

          {result.applicable ? (
            <div className="mt-2 flex flex-wrap gap-2 text-xs">
              {result.source ? (
                <span className="rounded-full border border-border px-2.5 py-1 text-foreground">
                  Sumber: {SOURCE_LABELS[result.source] || result.source}
                </span>
              ) : null}
              {result.confidence ? (
                <span className="rounded-full border border-border px-2.5 py-1 text-foreground">
                  {CONFIDENCE_LABELS[result.confidence] || result.confidence}
                </span>
              ) : null}
            </div>
          ) : null}

          {result.disclaimer ? (
            <p className="mt-3 text-xs leading-relaxed text-muted">
              {result.disclaimer}
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
