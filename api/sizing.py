"""Category-aware store sizing guidance.

The catalog mixes apparel, footwear, accessories, luggage, and categories that
have no defensible sizing basis. This module never silently defaults to a tops
size:

  - ``size_kind_for`` maps the product's ``(department, category)`` to an
    explicit size kind, with a title hint only for ambiguous categories.
  - ``recommend_size_core`` dispatches to the correct chart layer and returns
    ``applicable=False`` plus ``required_inputs`` or an honest reason when it
    cannot answer.

Chart data lives in ``api/knowledge/size_chart.json``. Footwear, waist, belt,
and headwear use cited standard formulas; tops, bottoms, and dress are authored
store estimates; the six brand offsets are demo data.
"""

import json
import logging
import os
import re
from functools import lru_cache

logger = logging.getLogger(__name__)

_KNOWLEDGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "knowledge")
_CHART_PATH = os.path.join(_KNOWLEDGE_DIR, "size_chart.json")
_KIND_MAP_PATH = os.path.join(_KNOWLEDGE_DIR, "size_kind_map.json")

_SIZE_ORDER = ["S", "M", "L", "XL", "XXL"]
_SIZE_KINDS = {
    "tops",
    "bottoms",
    "dress",
    "footwear",
    "belt",
    "headwear",
    "one_size",
    "dimensions",
    "unsupported",
}
_KIDS_DEPARTMENTS = {"baby", "boys", "girls"}
_INPUT_LABELS = {
    "height_cm": "tinggi badan (cm)",
    "weight_kg": "berat badan (kg)",
    "foot_length_cm": "panjang kaki (cm)",
    "waist_cm": "lingkar pinggang (cm)",
    "head_circumference_cm": "lingkar kepala (cm)",
}
_REQUIRED_INPUTS = {
    "tops": ["height_cm", "weight_kg"],
    "bottoms": ["height_cm", "weight_kg"],
    "dress": ["height_cm", "weight_kg"],
    "footwear": ["foot_length_cm"],
    "belt": ["waist_cm"],
    "headwear": ["head_circumference_cm"],
    "one_size": [],
    "dimensions": [],
    "unsupported": [],
}

# Only applied when the category itself is ambiguous/unsupported and the
# shopper is an adult. Kids are always unsupported, even if a title says shoe.
_TITLE_HINTS = (
    (r"\b(?:swimsuits?|swimwear|bikinis?|swim|leotards?|lingerie|bra|panty|panties|thong|corset|bustier|garter|hosiery|stockings?|tights|socks?|briefs?|boxers?)\b", "unsupported"),
    (r"\b(?:dress|gown)\b", "dress"),
    (r"\b(?:jumpsuit|romper|overalls?)\b", "dress"),
    (r"\b(?:pants?|trousers?|jeans?|shorts?|leggings?|skirts?|kilt|chinos?|cargo)\b", "bottoms"),
    (r"\b(?:shirts?|t-?shirts?|tees?|blouses?|hoodies?|sweatshirts?|sweaters?|cardigans?|jackets?|coats?|vests?|tanks?|tunics?|polos?|pullovers?)\b", "tops"),
    (r"\b(?:shoes?|boots?|sneakers?|sandals?|slippers?|loafers?|oxfords?|pumps?|flats?|clogs?|mules?|heels?)\b", "footwear"),
    (r"\bbelts?\b", "belt"),
    (r"\b(?:hats?|caps?|beanies?|skullies?|headbands?)\b", "headwear"),
    (r"\b(?:luggage|suitcases?|backpacks?|bags?|totes?|wallets?|cases?|organizers?)\b", "dimensions"),
    (r"\b(?:scarves?|scarfs?|shawls?|wraps?|ties?|neckties?|bowties?)\b", "one_size"),
)


@lru_cache(maxsize=1)
def load_size_chart() -> dict:
    """Load and cache the structured size chart JSON."""
    with open(_CHART_PATH) as f:
        return json.load(f)


@lru_cache(maxsize=1)
def load_size_kind_map() -> dict:
    """Load and cache the catalog category → size-kind map."""
    with open(_KIND_MAP_PATH) as f:
        return json.load(f)


def _normalise_department(department: str | None) -> str:
    return (department or "").strip()


def _title_kind_hint(title: str | None) -> str | None:
    if not title:
        return None

    lowered = title.lower()
    for pattern, kind in _TITLE_HINTS:
        if re.search(pattern, lowered):
            return kind
    return None


def size_kind_for(
    department: str | None,
    category: str | None,
    title: str | None = None,
) -> str:
    """Return the explicit size kind for a catalog product.

    The mapping covers every ``(department, category)`` pair in
    ``catalog_categories.json``. Unknown pairs fall back to ``unsupported`` —
    never to tops. Ambiguous adult categories can be refined by a clear title
    hint (e.g. a novelty tee, an accessories belt), while kids stay unsupported.
    """
    dep = _normalise_department(department)
    cat = (category or "").strip()
    mapping = load_size_kind_map()
    categories = mapping.get("categories", {})
    base = categories.get(f"{dep}|{cat}")
    if base is None and not dep and cat:
        # The agent tool can receive a bare category. Use adult pairs only:
        # kid pairs stay unsupported and must not veto an adult answer.
        kinds = {
            kind
            for pair, kind in categories.items()
            if pair.split("|", 1)[1] == cat and pair.split("|", 1)[0].lower() not in _KIDS_DEPARTMENTS
        }
        if len(kinds) == 1:
            base = kinds.pop()
    if base is None:
        base = mapping.get("default", "unsupported")
    if base not in _SIZE_KINDS:
        base = "unsupported"

    if dep.lower() in _KIDS_DEPARTMENTS:
        return "unsupported"
    if base == "unsupported":
        hinted = _title_kind_hint(title)
        if hinted:
            return hinted
    return base


def _gender_for(department: str | None, gender: str | None) -> str:
    if gender and gender.lower() in {"men", "women"}:
        return gender.lower()
    dep = _normalise_department(department).lower()
    if dep == "women":
        return "women"
    return "men"


def _sizes_for(kind: str, gender: str) -> list[dict]:
    chart = load_size_chart()
    section = chart.get(kind, {})
    if "men" in section or "women" in section:
        return section[gender]["sizes"]
    return section.get("sizes", [])


def _find_by_range(rows: list[dict], value: float, lo_key: str, hi_key: str) -> dict | None:
    for row in rows:
        if row[lo_key] <= value <= row[hi_key]:
            return row
    return None


def tops_size_from_tb_bb(height_cm: float, weight_kg: float, gender: str = "men") -> dict:
    """Map height (TB) + weight (BB) to the store-estimate tops size."""
    chart = load_size_chart()
    sizes = chart["tops"][gender]["sizes"]

    by_height = _find_by_range(sizes, height_cm, "height_min", "height_max")
    by_weight = _find_by_range(sizes, weight_kg, "weight_min", "weight_max")
    height_size = by_height["size"] if by_height else None
    weight_size = by_weight["size"] if by_weight else None

    primary = weight_size or height_size
    return {
        "primary": primary,
        "by_height": height_size,
        "by_weight": weight_size,
        "agree": bool(height_size and weight_size and height_size == weight_size),
        "in_range": by_height is not None and by_weight is not None,
        "rule": chart["tops"].get("rule", ""),
        "gender": gender,
        "source": "store_estimate",
        "confidence": "medium",
    }


def bottoms_waist_from_weight(weight_kg: float, gender: str = "men", height_cm: float | None = None) -> dict:
    """Map weight (and optionally height) to the store-estimate bottoms waist."""
    chart = load_size_chart()
    sizes = chart["bottoms"][gender]["sizes"]
    row = _find_by_range(sizes, weight_kg, "weight_min", "weight_max")
    height_row = _find_by_range(sizes, height_cm, "height_min", "height_max") if height_cm is not None else row
    chosen = row or height_row
    return {
        "waist_size": chosen["waist_size"] if chosen else None,
        "waist_cm": f"{chosen['waist_min']}-{chosen['waist_max']}" if chosen else None,
        "asian_size": chosen["asian_size"] if chosen else None,
        "in_range": row is not None and height_row is not None,
        "gender": gender,
        "source": "store_estimate",
        "confidence": "medium",
    }


def _nearest(rows: list[dict], value: float, key: str) -> dict | None:
    if not rows:
        return None
    return min(rows, key=lambda row: abs(float(row[key]) - value))


def waist_size_from_cm(waist_cm: float) -> dict:
    """Nearest standard waist row, with the delta stated explicitly."""
    rows = load_size_chart()["waist"]["sizes"]
    row = _nearest(rows, waist_cm, "waist_cm")
    return {
        "waist_size": row["waist_size"],
        "waist_cm": row["waist_cm"],
        "delta_cm": round(waist_cm - float(row["waist_cm"]), 1),
        "source": row["source"],
        "confidence": row["confidence"],
    }


def belt_size_from_waist(waist_cm: float) -> dict:
    rows = load_size_chart()["belt"]["sizes"]
    row = _nearest(rows, waist_cm, "waist_cm")
    return {
        "waist_size": row["waist_size"],
        "waist_cm": row["waist_cm"],
        "belt_length_in": row["belt_length_in"],
        "belt_length_cm": row["belt_length_cm"],
        "delta_cm": round(waist_cm - float(row["waist_cm"]), 1),
        "source": row["source"],
        "confidence": row["confidence"],
    }


def headwear_size_from_circumference(head_circumference_cm: float) -> dict:
    rows = load_size_chart()["headwear"]["sizes"]
    row = _find_by_range(rows, head_circumference_cm, "head_circumference_min", "head_circumference_max")
    if row is None:
        row = _nearest(rows, head_circumference_cm, "head_circumference_min")
    return {
        "size": row["size"],
        "head_circumference_cm_range": [row["head_circumference_min"], row["head_circumference_max"]],
        "source": row["source"],
        "confidence": row["confidence"],
    }


def footwear_size_from_length(foot_length_cm: float, gender: str) -> dict:
    rows = load_size_chart()["footwear"][gender]["sizes"]
    row = _nearest(rows, foot_length_cm, "foot_length_cm")
    return {
        "foot_length_cm": row["foot_length_cm"],
        "us": row["us"],
        "uk": row["uk"],
        "eu": row["eu"],
        "delta_cm": round(foot_length_cm - float(row["foot_length_cm"]), 1),
        "gender": gender,
        "source": row["source"],
        "confidence": row["confidence"],
    }


def _shift_size(size: str, steps: int) -> str:
    """Shift a tops size up (+) or down (-) within S..XXL, clamped."""
    if size not in _SIZE_ORDER:
        return size
    idx = max(0, min(len(_SIZE_ORDER) - 1, _SIZE_ORDER.index(size) + steps))
    return _SIZE_ORDER[idx]


def brand_offset(brand: str | None) -> dict | None:
    """Return the demo brand offset entry, or None."""
    if not brand:
        return None
    chart = load_size_chart()
    brands = chart.get("brand_offsets", {}).get("brands", {})
    for name, entry in brands.items():
        if name.lower() == brand.lower():
            return {"brand": name, **entry}
    return None


def required_inputs_for(size_kind: str) -> list[str]:
    """Public helper for the API info endpoint and frontend forms."""
    return list(_REQUIRED_INPUTS.get(size_kind, []))


def sizing_info_for(
    department: str | None,
    category: str | None,
    title: str | None = None,
) -> dict:
    """Describe whether a product category supports sizing and what it needs.

    Unlike ``recommend_size_core`` this does not require measurements: it is the
    product-page "what should I ask for?" endpoint.
    """
    kind = size_kind_for(department, category, title)
    if kind == "unsupported":
        return {
            "size_kind": kind,
            "applicable": False,
            "required_inputs": [],
            "reason": _unsupported_reason(department, category, title),
        }
    if kind == "dimensions":
        return {
            "size_kind": kind,
            "applicable": False,
            "required_inputs": [],
            "reason": load_size_chart()["dimensions"]["message"],
        }
    return {
        "size_kind": kind,
        "applicable": True,
        "required_inputs": required_inputs_for(kind),
        "reason": load_size_chart()["one_size"]["message"] if kind == "one_size" else None,
    }


def _disclaimer() -> str:
    return load_size_chart()["_meta"]["disclaimer"]


def _labels(inputs: list[str]) -> str:
    return ", ".join(_INPUT_LABELS.get(item, item) for item in inputs)


def _not_applicable(kind: str, reason: str, *, required_inputs: list[str] | None = None) -> dict:
    required = required_inputs if required_inputs is not None else _REQUIRED_INPUTS.get(kind, [])
    return {
        "applicable": False,
        "size_kind": kind,
        "recommendation": reason,
        "suggested": None,
        "source": None,
        "confidence": None,
        "required_inputs": required,
        "disclaimer": _disclaimer(),
        "reason": reason,
    }


def _unsupported_reason(department: str | None, category: str | None, title: str | None) -> str:
    dep = _normalise_department(department).lower()
    blob = f"{category or ''} {title or ''}".lower()
    if dep in _KIDS_DEPARTMENTS:
        return "Ukuran anak (Baby/Boys/Girls) belum dicakup oleh panduan ini."
    if re.search(
        r"\b(?:lingerie|bra|panty|panties|underwear|briefs?|boxers?|hosiery|stockings?|socks?)\b",
        blob,
    ):
        return "Lingerie, pakaian dalam, dan kaos kaki butuh ukuran tubuh yang belum kami modelkan."
    if re.search(r"\b(?:costumes?|cosplay)\b", blob):
        return "Kostum sering memakai ukuran panggung sendiri; belum ada dasar ukuran yang bisa kami pertanggungjawabkan."
    return load_size_chart()["unsupported"]["message"]


def recommend_size_core(
    height_cm: float | None = None,
    weight_kg: float | None = None,
    *,
    size_kind: str | None = None,
    garment_kind: str | None = None,
    brand: str | None = None,
    department: str | None = None,
    gender: str | None = None,
    category: str | None = None,
    title: str | None = None,
    foot_length_cm: float | None = None,
    waist_cm: float | None = None,
    head_circumference_cm: float | None = None,
) -> dict:
    """Return a category-aware, honest sizing recommendation.

    The result always includes ``applicable``, ``size_kind``, ``recommendation``,
    ``suggested``, ``source``, ``confidence``, ``required_inputs`` and
    ``disclaimer``. Missing inputs or unsupported/dimension categories return
    ``applicable=False`` instead of guessing.
    """
    chart = load_size_chart()
    kind = (size_kind or garment_kind or "").strip().lower()
    if not kind:
        kind = size_kind_for(department, category, title)
    if kind not in _SIZE_KINDS:
        kind = "unsupported"

    if kind == "unsupported":
        reason = _unsupported_reason(department, category, title)
        return _not_applicable(kind, reason, required_inputs=[])

    if kind == "dimensions":
        return _not_applicable(kind, chart["dimensions"]["message"], required_inputs=[])

    if kind == "one_size":
        return {
            "applicable": True,
            "size_kind": kind,
            "recommendation": chart["one_size"]["message"],
            "suggested": {"size": "one_size"},
            "source": "store_estimate",
            "confidence": "medium",
            "required_inputs": [],
            "disclaimer": _disclaimer(),
            "reason": None,
        }

    required = _REQUIRED_INPUTS.get(kind, [])
    values = {
        "height_cm": height_cm,
        "weight_kg": weight_kg,
        "foot_length_cm": foot_length_cm,
        "waist_cm": waist_cm,
        "head_circumference_cm": head_circumference_cm,
    }
    missing = [item for item in required if values[item] is None]
    if missing:
        reason = f"Butuh {_labels(missing)} untuk memberi saran ukuran yang jujur."
        return _not_applicable(kind, reason, required_inputs=required)

    gender_key = _gender_for(department, gender)
    notes: list[str] = []

    if kind in {"tops", "bottoms", "dress"}:
        # Height/weight are the authored store-estimate layer.
        if kind == "tops":
            baseline = tops_size_from_tb_bb(float(height_cm), float(weight_kg), gender_key)
            if not baseline["in_range"]:
                return _not_applicable(kind, "Ukuran tubuh di luar rentang chart toko; kami tidak mau menebak.")
            suggested = {"size": baseline["primary"]}
            if baseline["agree"]:
                baseline_text = (
                    f"Berdasarkan {chart['_meta']['label']}, TB {height_cm:.0f} cm & BB {weight_kg:.0f} kg "
                    f"mengarah ke ukuran {suggested['size']}."
                )
            else:
                baseline_text = (
                    f"Berdasarkan {chart['_meta']['label']}, tinggi mengarah ke {baseline['by_height']} "
                    f"dan berat ke {baseline['by_weight']}. {baseline['rule']}"
                )
            source, confidence = "store_estimate", "medium"
        elif kind == "bottoms":
            baseline = bottoms_waist_from_weight(float(weight_kg), gender_key, float(height_cm))
            if not baseline["in_range"] or baseline["waist_size"] is None:
                return _not_applicable(kind, "Ukuran tubuh di luar rentang chart toko; kami tidak mau menebak.")
            suggested = {"waist_size": baseline["waist_size"]}
            baseline_text = (
                f"Berdasarkan {chart['_meta']['label']}, BB {weight_kg:.0f} kg mengarah ke "
                f"waist {baseline['waist_size']} ({baseline['waist_cm']} cm, ≈ {baseline['asian_size']})."
            )
            source, confidence = "store_estimate", "medium"
            if waist_cm is not None:
                waist = waist_size_from_cm(float(waist_cm))
                suggested = {"waist_size": waist["waist_size"], "waist_cm": waist["waist_cm"]}
                baseline_text += (
                    f" Dengan lingkar pinggang {waist_cm:.0f} cm, tabel standar mengarah ke "
                    f"waist {waist['waist_size']} ({waist['waist_cm']} cm)."
                )
                source, confidence = "standard", "high"
        else:
            sizes = chart["dress"]["sizes"]
            by_height = _find_by_range(sizes, float(height_cm), "height_min", "height_max")
            by_weight = _find_by_range(sizes, float(weight_kg), "weight_min", "weight_max")
            if by_height is None or by_weight is None:
                return _not_applicable(kind, "Ukuran tubuh di luar rentang chart toko; kami tidak mau menebak.")
            suggested_size = by_weight["size"]
            suggested = {"size": suggested_size}
            baseline = {
                "by_height": by_height["size"],
                "by_weight": by_weight["size"],
                "agree": by_height["size"] == by_weight["size"],
                "source": "store_estimate",
                "confidence": "medium",
            }
            baseline_text = (
                f"Berdasarkan {chart['_meta']['label']}, TB {height_cm:.0f} cm & BB {weight_kg:.0f} kg "
                f"mengarah ke ukuran {suggested_size}. {chart['dress']['guidance']}"
            )
            source, confidence = "store_estimate", "medium"
    elif kind == "footwear":
        size = footwear_size_from_length(float(foot_length_cm), gender_key)
        chart_rows = chart["footwear"][gender_key]["sizes"]
        low = min(float(row["foot_length_cm"]) for row in chart_rows)
        high = max(float(row["foot_length_cm"]) for row in chart_rows)
        if not (low <= float(foot_length_cm) <= high):
            return _not_applicable(kind, "Panjang kaki di luar rentang tabel standar; kami tidak mau menebak.")
        suggested = {
            "foot_length_cm": size["foot_length_cm"],
            "us": size["us"],
            "uk": size["uk"],
            "eu": size["eu"],
        }
        baseline = size
        baseline_text = (
            f"Untuk panjang kaki {foot_length_cm:.1f} cm, tabel standar mengarah ke "
            f"US {size['us']:g} / UK {size['uk']:g} / EU {size['eu']:g}."
        )
        source, confidence = "standard", "high"
    elif kind == "belt":
        size = belt_size_from_waist(float(waist_cm))
        chart_rows = chart["belt"]["sizes"]
        low = min(float(row["waist_cm"]) for row in chart_rows)
        high = max(float(row["waist_cm"]) for row in chart_rows)
        if not (low <= float(waist_cm) <= high):
            return _not_applicable(kind, "Lingkar pinggang di luar rentang tabel standar; kami tidak mau menebak.")
        suggested = {
            "waist_size": size["waist_size"],
            "waist_cm": size["waist_cm"],
            "belt_length_in": size["belt_length_in"],
            "belt_length_cm": size["belt_length_cm"],
        }
        baseline = size
        baseline_text = (
            f"Lingkar pinggang {waist_cm:.0f} cm mengarah ke belt sekitar "
            f"{size['belt_length_in']} in ({size['belt_length_cm']} cm), memakai aturan umum waist + 2 in."
        )
        source, confidence = "standard", "high"
    else:  # headwear
        chart_rows = chart["headwear"]["sizes"]
        low = min(float(row["head_circumference_min"]) for row in chart_rows)
        high = max(float(row["head_circumference_max"]) for row in chart_rows)
        if not (low <= float(head_circumference_cm) <= high):
            return _not_applicable(kind, "Lingkar kepala di luar rentang tabel standar; kami tidak mau menebak.")
        size = headwear_size_from_circumference(float(head_circumference_cm))
        suggested = {"size": size["size"]}
        baseline = size
        baseline_text = (
            f"Lingkar kepala {head_circumference_cm:.0f} cm masuk rentang ukuran {size['size']} "
            f"({size['head_circumference_cm_range'][0]}–{size['head_circumference_cm_range'][1]} cm)."
        )
        source, confidence = "standard", "high"

    offset = brand_offset(brand)
    if offset:
        notes.append(f"Catatan brand {offset['brand']}: {offset['offset_note']} {offset['action']}")
        if kind == "tops" and "size" in suggested:
            if offset["fit_label"] == "runs_small":
                suggested["size"] = _shift_size(suggested["size"], +1)
                notes.append(f"Karena {offset['brand']} cenderung kekecilan, mimin geser saran ke {suggested['size']}.")
            elif offset["fit_label"] == "runs_large":
                suggested["size"] = _shift_size(suggested["size"], -1)
                notes.append(f"Karena {offset['brand']} cenderung kebesaran, mimin geser saran ke {suggested['size']}.")
        confidence = "low"

    if kind == "bottoms":
        headline = f"Mimin sarankan coba waist {suggested['waist_size']} dulu kak"
    elif kind in {"footwear", "belt"}:
        headline = "Mimin sarankan coba ukuran ini dulu kak"
    else:
        headline = f"Mimin sarankan coba ukuran {suggested['size']} dulu kak"

    recommendation = (
        f"{headline} — tapi ini perkiraan ya, bukan patokan pasti. "
        f"{baseline_text}"
    )

    return {
        "applicable": True,
        "size_kind": kind,
        "recommendation": recommendation,
        "suggested": suggested,
        "source": source,
        "confidence": confidence,
        "required_inputs": required,
        "disclaimer": _disclaimer(),
        "reason": None,
        "garment_kind": kind,
        "baseline": baseline,
        "brand": offset["brand"] if offset else brand,
        "brand_offset": offset,
        "source_label": chart["_meta"]["label"],
        "notes": [n for n in notes if n],
        "gender": gender_key,
    }
