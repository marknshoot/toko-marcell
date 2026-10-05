"""Unit tests for the sizing recommender logic (api/sizing.py).

Pure logic, no DB — safe to run in the offline CI subset.
"""

import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))

from sizing import (
    bottoms_waist_from_weight,
    brand_offset,
    load_size_chart,
    recommend_size_core,
    tops_size_from_tb_bb,
)

# ── Chart loads and is labelled ──────────────────────────────────────────────

def test_chart_label_is_panduan_umum_toko():
    chart = load_size_chart()
    assert chart["_meta"]["label"] == "panduan umum toko"


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
    # Tall but light: height → larger, weight → smaller. Must flag disagreement.
    r = tops_size_from_tb_bb(178, 52)
    assert r["agree"] is False
    assert r["rule"]  # the chart rule is surfaced, not silently resolved


def test_tops_out_of_range_clamps():
    small = tops_size_from_tb_bb(150, 45)
    assert small["by_weight"] == "S"
    big = tops_size_from_tb_bb(200, 120)
    assert big["by_weight"] == "XXL"


# ── Bottoms waist mapping ────────────────────────────────────────────────────

def test_bottoms_waist_mapping():
    r = bottoms_waist_from_weight(70)
    assert r["waist_size"] == 32

    light = bottoms_waist_from_weight(50)
    assert light["waist_size"] == 28


# ── Brand offsets (data-driven corrections) ──────────────────────────────────

def test_brand_offset_dickies_runs_small():
    o = brand_offset("Dickies")
    assert o["fit_label"] == "runs_small"


def test_brand_offset_champion_not_runs_large():
    """Design §3 correction: Champion is NOT runs_large; data says true_to_size."""
    o = brand_offset("Champion")
    assert o["fit_label"] == "true_to_size"


def test_brand_offset_carhartt_true_to_size():
    o = brand_offset("Carhartt")
    assert o["fit_label"] == "true_to_size"


def test_brand_offset_birkenstock_runs_large():
    o = brand_offset("Birkenstock")
    assert o["fit_label"] == "runs_large"


def test_brand_offset_case_insensitive():
    assert brand_offset("dickies")["fit_label"] == "runs_small"


def test_brand_offset_unknown_is_none():
    assert brand_offset("NoSuchBrand") is None
    assert brand_offset(None) is None


# ── The 3-layer recommender ──────────────────────────────────────────────────

def test_recommend_never_a_certainty():
    """Must always read as guidance + carry a disclaimer."""
    r = recommend_size_core(170, 65, garment_kind="tops")
    assert "disclaimer" in r and r["disclaimer"]
    # phrased as a suggestion, not a command
    assert "perkiraan" in r["recommendation"].lower()
    assert "sarankan" in r["recommendation"].lower()


def test_recommend_runs_small_brand_shifts_up():
    """A runs_small brand (Dickies) should nudge a tops size UP."""
    base = recommend_size_core(178, 75, garment_kind="tops")  # no brand
    dickies = recommend_size_core(178, 75, garment_kind="tops", brand="Dickies")
    order = ["S", "M", "L", "XL", "XXL"]
    assert order.index(dickies["suggested"]["size"]) >= order.index(base["suggested"]["size"])
    assert any("kekecilan" in n for n in dickies["notes"])


def test_recommend_champion_does_not_shift_down():
    """Champion is true_to_size now — must NOT be treated as runs_large."""
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


def test_recommend_includes_product_fit_phrasing():
    pf = {"phrasing": "Dari 50 ulasan: 60% bilang kekecilan, 30% pas, 10% kebesaran.", "label": "runs_small"}
    r = recommend_size_core(170, 65, garment_kind="tops", product_fit=pf)
    assert any("ulasan" in n for n in r["notes"])
