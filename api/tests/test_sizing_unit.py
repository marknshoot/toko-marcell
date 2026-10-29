"""Unit tests for category-aware sizing (api/sizing.py).

Pure logic, no DB — safe to run in the offline CI subset.
"""

import json
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

from sizing import (
    bottoms_waist_from_weight,
    brand_offset,
    load_size_chart,
    load_size_kind_map,
    recommend_size_core,
    size_kind_for,
    sizing_info_for,
    tops_size_from_tb_bb,
)

KNOWLEDGE = API_DIR / "knowledge"
SIZE_KINDS = {
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


def _assert_ranges_do_not_overlap(rows, lo_key, hi_key):
    ordered = sorted(rows, key=lambda row: row[lo_key])
    for previous, current in zip(ordered, ordered[1:], strict=False):
        assert previous[hi_key] < current[lo_key], (previous, current)


# ── Chart loads and is labelled ──────────────────────────────────────────────

def test_chart_label_is_panduan_umum_toko():
    chart = load_size_chart()
    assert chart["_meta"]["label"] == "panduan umum toko"
    assert chart["_meta"]["version"] == "3.0"


def test_chart_cites_standards_and_formulas():
    sources = load_size_chart()["sources"]
    assert set(sources) == {"footwear", "waist", "belt", "headwear"}
    assert "ISO 9407" in sources["footwear"]["standard"]
    assert "2.54" in sources["waist"]["formula"]
    assert "waist_in + 2" in sources["belt"]["formula"]
    assert "54" in sources["headwear"]["formula"]


def test_every_chart_row_has_source_and_confidence():
    chart = load_size_chart()
    sections = {
        "tops": [*chart["tops"]["men"]["sizes"], *chart["tops"]["women"]["sizes"]],
        "bottoms": [*chart["bottoms"]["men"]["sizes"], *chart["bottoms"]["women"]["sizes"]],
        "dress": chart["dress"]["sizes"],
        "footwear": [*chart["footwear"]["men"]["sizes"], *chart["footwear"]["women"]["sizes"]],
        "waist": chart["waist"]["sizes"],
        "belt": chart["belt"]["sizes"],
        "headwear": chart["headwear"]["sizes"],
    }
    for name, rows in sections.items():
        assert rows, name
        for row in rows:
            assert row["source"] in {"standard", "store_estimate", "demo"}, (name, row)
            assert row["confidence"] in {"high", "medium", "low"}, (name, row)


def test_chart_ranges_are_monotonic_and_non_overlapping():
    chart = load_size_chart()
    for gender in ("men", "women"):
        tops = chart["tops"][gender]["sizes"]
        _assert_ranges_do_not_overlap(tops, "height_min", "height_max")
        _assert_ranges_do_not_overlap(tops, "weight_min", "weight_max")
        _assert_ranges_do_not_overlap(tops, "chest_min", "chest_max")

        bottoms = chart["bottoms"][gender]["sizes"]
        _assert_ranges_do_not_overlap(bottoms, "height_min", "height_max")
        _assert_ranges_do_not_overlap(bottoms, "weight_min", "weight_max")
        _assert_ranges_do_not_overlap(bottoms, "waist_min", "waist_max")

    _assert_ranges_do_not_overlap(chart["dress"]["sizes"], "height_min", "height_max")
    _assert_ranges_do_not_overlap(chart["dress"]["sizes"], "weight_min", "weight_max")

    for gender in ("men", "women"):
        lengths = [row["foot_length_cm"] for row in chart["footwear"][gender]["sizes"]]
        assert lengths == sorted(set(lengths))

    _assert_ranges_do_not_overlap(chart["headwear"]["sizes"], "head_circumference_min", "head_circumference_max")


def test_womens_charts_differ_from_mens():
    chart = load_size_chart()
    assert chart["tops"]["men"]["sizes"] != chart["tops"]["women"]["sizes"]
    assert chart["bottoms"]["men"]["sizes"] != chart["bottoms"]["women"]["sizes"]
    assert chart["footwear"]["men"]["sizes"] != chart["footwear"]["women"]["sizes"]
    assert chart["footwear"]["women"]["sizes"][0]["eu"] == 35
    assert chart["footwear"]["men"]["sizes"][-1]["eu"] == 47


def test_size_guide_seeding_keeps_examples_and_bottoms_baseline():
    from knowledge_seed import _chunk_markdown

    chunks = _chunk_markdown(str(KNOWLEDGE / "size_charts.md"), "sizing")
    assert any(chunk["category"] == "sizing_example" for chunk in chunks)
    bottoms = [
        chunk for chunk in chunks
        if chunk["category"] == "sizing_baseline" and "bottoms" in chunk["title"].lower()
    ]
    assert bottoms, "Bottoms must seed as sizing_baseline, not brand_sizing via the 'toms' substring"


def test_brand_offsets_are_explicit_demo_data():
    offsets = load_size_chart()["brand_offsets"]["brands"]
    assert len(offsets) == 6
    for entry in offsets.values():
        assert entry["source"] == "demo"
        assert entry["confidence"] == "low"


# ── Category map ─────────────────────────────────────────────────────────────

def test_category_map_covers_every_seed_pair():
    catalog = json.loads((KNOWLEDGE / "catalog_categories.json").read_text())
    mapping = load_size_kind_map()
    pairs = {f"{row['department']}|{row['category']}" for row in catalog["categories"]}
    assert set(mapping["categories"]) == pairs
    assert mapping["default"] == "unsupported"
    assert all(kind in SIZE_KINDS for kind in mapping["categories"].values())


def test_shoes_luggage_and_lingerie_never_default_to_tops():
    assert size_kind_for("Women", "Shoes") == "footwear"
    assert size_kind_for("Other", "Luggage") == "dimensions"
    assert size_kind_for("Women", "Lingerie") == "unsupported"
    assert size_kind_for("Women", "Hosiery & Socks") == "unsupported"


def test_kids_categories_stay_unsupported_even_for_shoes():
    assert size_kind_for("Girls", "Shoes") == "unsupported"
    assert size_kind_for("Baby", "Shoes") == "unsupported"


def test_ambiguous_categories_use_clear_title_hints_only():
    assert size_kind_for("Men", "Casual") == "bottoms"
    assert size_kind_for("Women", "Casual") == "dress"
    assert size_kind_for("Men", "Novelty", "Champion Men's Hoodie") == "tops"
    assert size_kind_for("Men", "Novelty", "Keychain bottle opener") == "unsupported"
    assert size_kind_for("Women", "Accessories", "Leather Belt") == "belt"


def test_unknown_pair_is_unsupported_not_tops():
    assert size_kind_for("Martian", "Ray Gun") == "unsupported"


def test_bare_category_uses_adult_pairs():
    assert size_kind_for(None, "Shoes") == "footwear"
    assert size_kind_for(None, "Pants") == "bottoms"
    assert size_kind_for(None, "Hats & Caps") == "headwear"
    assert size_kind_for(None, "Lingerie") == "unsupported"


# ── Tops TB/BB mapping ───────────────────────────────────────────────────────

def test_tops_mid_range_agrees():
    r = tops_size_from_tb_bb(170, 65)
    assert r["by_height"] == "M"
    assert r["by_weight"] == "M"
    assert r["agree"] is True
    assert r["primary"] == "M"


def test_tops_large_person():
    r = tops_size_from_tb_bb(182, 85)
    assert r["primary"] == "XL"


def test_tops_disagreement_surfaced():
    r = tops_size_from_tb_bb(178, 52)
    assert r["agree"] is False
    assert r["rule"]


def test_tops_boundary_values_are_exact():
    assert tops_size_from_tb_bb(167.9, 57.9)["primary"] == "S"
    assert tops_size_from_tb_bb(168, 58)["primary"] == "M"
    assert tops_size_from_tb_bb(200, 120)["primary"] == "XXL"


def test_womens_tops_use_a_different_chart():
    men = tops_size_from_tb_bb(170, 65, "men")
    women = tops_size_from_tb_bb(170, 65, "women")
    assert men["primary"] == "M"
    assert women["primary"] == "L"


# ── Bottoms waist mapping ────────────────────────────────────────────────────

def test_bottoms_waist_mapping():
    assert bottoms_waist_from_weight(70)["waist_size"] == 32
    assert bottoms_waist_from_weight(50)["waist_size"] == 28
    assert bottoms_waist_from_weight(70, "women")["waist_size"] == 32


# ── Category-aware core dispatch ─────────────────────────────────────────────

def test_core_tops_requires_both_height_and_weight():
    missing = recommend_size_core(height_cm=170, size_kind="tops")
    assert missing["applicable"] is False
    assert missing["required_inputs"] == ["height_cm", "weight_kg"]
    assert missing["suggested"] is None
    assert "Butuh" in missing["reason"]


def test_core_footwear_needs_only_foot_length():
    r = recommend_size_core(foot_length_cm=26.5, size_kind="footwear", gender="men")
    assert r["applicable"] is True
    assert r["size_kind"] == "footwear"
    assert r["suggested"]["eu"] == 42
    assert r["source"] == "standard"
    assert r["confidence"] == "high"

    missing = recommend_size_core(size_kind="footwear")
    assert missing["applicable"] is False
    assert missing["required_inputs"] == ["foot_length_cm"]


def test_core_belt_needs_waist_and_uses_standard_rule():
    r = recommend_size_core(waist_cm=82, size_kind="belt")
    assert r["applicable"] is True
    assert r["suggested"]["waist_size"] == 32
    assert r["suggested"]["belt_length_in"] == 34
    assert r["suggested"]["belt_length_cm"] == 86.4

    missing = recommend_size_core(size_kind="belt")
    assert missing["required_inputs"] == ["waist_cm"]


def test_core_headwear_bands_and_out_of_range():
    assert recommend_size_core(head_circumference_cm=57, size_kind="headwear")["suggested"]["size"] == "M"
    assert recommend_size_core(head_circumference_cm=61, size_kind="headwear")["suggested"]["size"] == "XL"
    out = recommend_size_core(head_circumference_cm=70, size_kind="headwear")
    assert out["applicable"] is False


def test_core_dress_uses_dress_chart_and_guidance():
    r = recommend_size_core(height_cm=165, weight_kg=60, size_kind="dress", department="Women")
    assert r["applicable"] is True
    assert r["suggested"]["size"] == "L"
    assert "bust" in r["recommendation"].lower()


def test_core_dimensions_is_not_a_body_size_question():
    r = recommend_size_core(height_cm=170, weight_kg=65, size_kind="dimensions")
    assert r["applicable"] is False
    assert r["size_kind"] == "dimensions"
    assert "dimensi" in r["recommendation"].lower()
    assert r["required_inputs"] == []


def test_core_one_size_returns_single_size():
    r = recommend_size_core(size_kind="one_size")
    assert r["applicable"] is True
    assert r["suggested"] == {"size": "one_size"}
    assert "ukuran tunggal" in r["recommendation"].lower()


def test_core_unsupported_category_is_honest():
    lingerie = recommend_size_core(
        height_cm=165, weight_kg=55, department="Women", category="Lingerie", title="Bra"
    )
    assert lingerie["applicable"] is False
    assert lingerie["size_kind"] == "unsupported"
    assert "belum kami modelkan" in lingerie["recommendation"]

    luggage = recommend_size_core(
        height_cm=170, weight_kg=65, department="Other", category="Luggage"
    )
    assert luggage["applicable"] is False
    assert "dimensi" in luggage["recommendation"].lower()

    # "brand" must not be mistaken for "bra".
    other = recommend_size_core(
        height_cm=170, weight_kg=65, department="Other", category="Other", title="Brand New Gadget"
    )
    assert "Lingerie" not in other["recommendation"]


def test_core_infers_kind_from_category_and_never_defaults_tops():
    shoes = recommend_size_core(height_cm=170, weight_kg=65, department="Women", category="Shoes")
    assert shoes["applicable"] is False
    assert shoes["required_inputs"] == ["foot_length_cm"]

    bare = recommend_size_core(height_cm=170, weight_kg=65, category="Shoes")
    assert bare["size_kind"] == "footwear"
    assert bare["required_inputs"] == ["foot_length_cm"]

    unknown = recommend_size_core(height_cm=170, weight_kg=65, department="Martian", category="Ray Gun")
    assert unknown["applicable"] is False
    assert unknown["size_kind"] == "unsupported"


def test_core_out_of_chart_is_not_clamped_to_a_guess():
    r = recommend_size_core(height_cm=250, weight_kg=200, size_kind="tops")
    assert r["applicable"] is False
    assert "di luar rentang" in r["recommendation"].lower()


def test_sizing_info_matches_required_inputs():
    info = sizing_info_for("Women", "Shoes")
    assert info["applicable"] is True
    assert info["required_inputs"] == ["foot_length_cm"]

    assert sizing_info_for("Other", "Luggage")["applicable"] is False
    assert sizing_info_for("Women", "Lingerie")["applicable"] is False
    assert sizing_info_for("Women", "Wallets")["size_kind"] == "one_size"


# ── Brand offsets (data-driven corrections) ──────────────────────────────────

def test_brand_offset_dickies_runs_small():
    assert brand_offset("Dickies")["fit_label"] == "runs_small"


def test_brand_offset_champion_not_runs_large():
    assert brand_offset("Champion")["fit_label"] == "true_to_size"


def test_brand_offset_carhartt_true_to_size():
    assert brand_offset("Carhartt")["fit_label"] == "true_to_size"


def test_brand_offset_birkenstock_runs_large():
    assert brand_offset("Birkenstock")["fit_label"] == "runs_large"


def test_brand_offset_case_insensitive_and_unknown():
    assert brand_offset("dickies")["fit_label"] == "runs_small"
    assert brand_offset("NoSuchBrand") is None
    assert brand_offset(None) is None


# ── The advisory recommender ─────────────────────────────────────────────────

def test_recommend_never_a_certainty():
    r = recommend_size_core(170, 65, garment_kind="tops")
    assert r["disclaimer"]
    assert "perkiraan" in r["recommendation"].lower()
    assert "sarankan" in r["recommendation"].lower()


def test_recommend_runs_small_brand_shifts_up():
    base = recommend_size_core(178, 75, garment_kind="tops")
    dickies = recommend_size_core(178, 75, garment_kind="tops", brand="Dickies")
    order = ["S", "M", "L", "XL", "XXL"]
    assert order.index(dickies["suggested"]["size"]) >= order.index(base["suggested"]["size"])
    assert any("kekecilan" in note for note in dickies["notes"])


def test_recommend_champion_does_not_shift_down():
    base = recommend_size_core(178, 75, garment_kind="tops")
    champ = recommend_size_core(178, 75, garment_kind="tops", brand="Champion")
    assert champ["suggested"]["size"] == base["suggested"]["size"]


def test_recommend_runs_large_brand_shifts_down():
    base = recommend_size_core(178, 75, garment_kind="tops")
    birk = recommend_size_core(178, 75, garment_kind="tops", brand="Birkenstock")
    order = ["S", "M", "L", "XL", "XXL"]
    assert order.index(birk["suggested"]["size"]) <= order.index(base["suggested"]["size"])


def test_recommend_bottoms_returns_waist():
    r = recommend_size_core(175, 70, garment_kind="bottoms", brand="Dickies")
    assert "waist_size" in r["suggested"]
    assert r["garment_kind"] == "bottoms"


def test_recommend_bottoms_waist_measurement_beats_weight_estimate():
    r = recommend_size_core(175, 70, waist_cm=82, garment_kind="bottoms")
    assert r["source"] == "standard"
    assert r["confidence"] == "high"
    assert r["suggested"]["waist_size"] == 32
