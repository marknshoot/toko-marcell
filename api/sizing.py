"""Store sizing guidance — the 3-layer recommender (design §3).

Layers, applied in order and always surfaced together, **never** as a certainty:
  a. Store baseline chart (``api/knowledge/size_chart.json``, "panduan umum toko")
     maps TB/BB → a tops size and a bottoms waist size.
  b. Product-level review fit signal (``product_fit``) nudges the baseline.
  c. Brand fallback (``brand_fit`` / size_chart brand offsets) when the product
     has too few mentions.

The output is advisory text + a structured breakdown. It must read like a
helpful "mimin" suggestion, not "you must buy size X".
"""

import json
import logging
import os
from functools import lru_cache

logger = logging.getLogger(__name__)

_CHART_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "knowledge", "size_chart.json")

_SIZE_ORDER = ["S", "M", "L", "XL", "XXL"]


@lru_cache(maxsize=1)
def load_size_chart() -> dict:
    """Load and cache the structured size chart JSON."""
    with open(_CHART_PATH) as f:
        return json.load(f)


# ── Baseline mapping ─────────────────────────────────────────────────────────

def tops_size_from_tb_bb(height_cm: float, weight_kg: float) -> dict:
    """Map height (TB) + weight (BB) to a baseline tops size.

    When height and weight disagree we return both candidates and the chart's
    rule (prioritise chest/shoulder) rather than silently picking one.
    """
    chart = load_size_chart()
    sizes = chart["tops"]["sizes"]

    by_height = None
    by_weight = None
    for s in sizes:
        if s["height_min"] <= height_cm <= s["height_max"]:
            by_height = s["size"]
        if s["weight_min"] <= weight_kg <= s["weight_max"]:
            by_weight = s["size"]

    # Out-of-range handling: clamp to the nearest end.
    if by_height is None:
        by_height = sizes[0]["size"] if height_cm < sizes[0]["height_min"] else sizes[-1]["size"]
    if by_weight is None:
        by_weight = sizes[0]["size"] if weight_kg < sizes[0]["weight_min"] else sizes[-1]["size"]

    primary = by_weight  # weight is usually the better single predictor of drape
    return {
        "primary": primary,
        "by_height": by_height,
        "by_weight": by_weight,
        "agree": by_height == by_weight,
        "rule": chart["tops"].get("rule", ""),
    }


def bottoms_waist_from_weight(weight_kg: float) -> dict:
    """Map weight to a baseline bottoms waist size (inches)."""
    chart = load_size_chart()
    sizes = chart["bottoms"]["sizes"]
    chosen = None
    for s in sizes:
        if s["weight_min"] <= weight_kg <= s["weight_max"]:
            chosen = s
            break
    if chosen is None:
        chosen = sizes[0] if weight_kg < sizes[0]["weight_min"] else sizes[-1]
    return {
        "waist_size": chosen["waist_size"],
        "waist_cm": f"{chosen['waist_min']}-{chosen['waist_max']}",
        "asian_size": chosen["asian_size"],
    }


def _shift_size(size: str, steps: int) -> str:
    """Shift a tops size up (+) or down (-) within S..XXL, clamped."""
    if size not in _SIZE_ORDER:
        return size
    idx = max(0, min(len(_SIZE_ORDER) - 1, _SIZE_ORDER.index(size) + steps))
    return _SIZE_ORDER[idx]


# ── Brand offset lookup ──────────────────────────────────────────────────────

def brand_offset(brand: str | None) -> dict | None:
    """Return the data-driven brand offset entry, or None."""
    if not brand:
        return None
    chart = load_size_chart()
    brands = chart.get("brand_offsets", {}).get("brands", {})
    # case-insensitive match
    for name, entry in brands.items():
        if name.lower() == brand.lower():
            return {"brand": name, **entry}
    return None


# ── The 3-layer recommender ──────────────────────────────────────────────────

def recommend_size_core(
    height_cm: float,
    weight_kg: float,
    *,
    garment_kind: str = "tops",
    brand: str | None = None,
    product_fit: dict | None = None,
) -> dict:
    """Pure logic (no DB): combine baseline + brand offset + product fit signal.

    ``product_fit`` is the serving fit object from ``fit.get_product_fit`` (or
    None). Returns a structured dict with ``recommendation`` text that is always
    phrased as guidance, never a guarantee.
    """
    chart = load_size_chart()
    label = chart["_meta"]["label"]  # "panduan umum toko"
    notes: list[str] = []

    if garment_kind == "bottoms":
        baseline = bottoms_waist_from_weight(weight_kg)
        baseline_text = (
            f"Berdasarkan {label}, untuk BB {weight_kg:.0f} kg ukuran celana "
            f"sekitar waist {baseline['waist_size']} ({baseline['waist_cm']} cm, "
            f"≈ {baseline['asian_size']})."
        )
        suggested = {"waist_size": baseline["waist_size"]}
    else:
        baseline = tops_size_from_tb_bb(height_cm, weight_kg)
        suggested_size = baseline["primary"]
        if baseline["agree"]:
            baseline_text = (
                f"Berdasarkan {label}, TB {height_cm:.0f} cm & BB {weight_kg:.0f} kg "
                f"mengarah ke ukuran {suggested_size}."
            )
        else:
            baseline_text = (
                f"Berdasarkan {label}, tinggi mengarah ke {baseline['by_height']} "
                f"dan berat ke {baseline['by_weight']}. {baseline['rule']}"
            )
        suggested = {"size": suggested_size}

    # ── Layer b/c: brand offset (data-driven) ────────────────────────────────
    offset = brand_offset(brand)
    if offset:
        notes.append(f"Catatan brand {offset['brand']}: {offset['offset_note']} {offset['action']}")
        if garment_kind == "tops" and "size" in suggested:
            if offset["fit_label"] == "runs_small":
                suggested["size"] = _shift_size(suggested["size"], +1)
                notes.append(f"Karena {offset['brand']} cenderung kekecilan, mimin geser saran ke {suggested['size']}.")
            elif offset["fit_label"] == "runs_large":
                suggested["size"] = _shift_size(suggested["size"], -1)
                notes.append(f"Karena {offset['brand']} cenderung kebesaran, mimin geser saran ke {suggested['size']}.")

    # ── Layer b: product-level review signal ─────────────────────────────────
    if product_fit:
        notes.append(product_fit.get("phrasing", ""))

    # ── Compose a non-certain recommendation ─────────────────────────────────
    if garment_kind == "bottoms":
        headline = f"Mimin sarankan coba waist {suggested['waist_size']} dulu kak"
    else:
        headline = f"Mimin sarankan coba ukuran {suggested['size']} dulu kak"

    recommendation = (
        f"{headline} — tapi ini perkiraan ya, bukan patokan pasti. "
        f"{baseline_text}"
    )

    return {
        "recommendation": recommendation,
        "suggested": suggested,
        "garment_kind": garment_kind,
        "baseline": baseline,
        "brand": offset["brand"] if offset else brand,
        "brand_offset": offset,
        "product_fit": product_fit,
        "source_label": label,
        "notes": [n for n in notes if n],
        "disclaimer": (
            "Panduan ukuran ini estimasi umum toko dari standar Asia + ulasan "
            "pelanggan, bukan chart resmi brand. Hasil bisa beda tiap orang."
        ),
    }
