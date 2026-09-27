import sys
from pathlib import Path
import pytest

PIPELINES_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PIPELINES_DIR))

from build_catalog import clean_text, is_scraped_script, parse_price_usd
from eval_search import compute_dcg, compute_ndcg, compute_mrr


def test_clean_text_strips_html_tags_and_scripts():
    raw_html = "<p>Great <b>sneakers</b>! <script>var x = 1;</script> Soft leather.</p>"
    cleaned = clean_text(raw_html)
    assert cleaned == "Great sneakers ! Soft leather."


def test_clean_text_strips_css_blocks():
    raw = "Description text <style>.header { color: red; }</style> More details."
    cleaned = clean_text(raw)
    assert cleaned == "Description text More details."


def test_is_scraped_script_detects_amazon_telemetry():
    assert is_scraped_script("var aPageStart = (new Date()).getTime();") is True
    assert is_scraped_script("window.ue_csm = window.ue_csm || {};") is True
    assert is_scraped_script("Levi's Men's 501 Original Fit Jeans") is False


def test_parse_price_usd_valid_formats():
    assert parse_price_usd("$19.99") == 19.99
    assert parse_price_usd("$9.99 - $29.99") == 9.99
    assert parse_price_usd("$1,200.00") is None  # Plausibility filter drops > $1,000
    assert parse_price_usd("$0.01") is None  # Plausibility filter drops < $1.00
    assert parse_price_usd("Free") is None
    assert parse_price_usd(None) is None


def test_bayesian_popularity_math():
    prior_mean = 4.31
    prior_weight = 100.0

    # Low review item: dominated by prior
    rating_count_low = 2.0
    avg_rating_low = 5.0
    score_low = (rating_count_low * avg_rating_low + prior_weight * prior_mean) / (
        rating_count_low + prior_weight
    )
    assert round(score_low, 2) == 4.32

    # High review item: reflects authentic high rating
    rating_count_high = 2000.0
    avg_rating_high = 4.8
    score_high = (rating_count_high * avg_rating_high + prior_weight * prior_mean) / (
        rating_count_high + prior_weight
    )
    assert round(score_high, 2) == 4.78


def test_ir_metrics_computation():
    # Grades: 2 (exact), 1 (relevant), 0 (irrelevant)
    grades = [2, 0, 1, 0, 0]

    # MRR of first relevant item (rank 1 -> 1.0)
    assert compute_mrr(grades, k=5) == 1.0
    assert compute_mrr([0, 0, 1], k=5) == round(1.0 / 3.0, 4)
    assert compute_mrr([0, 0, 0], k=5) == 0.0

    # nDCG bounded in [0.0, 1.0]
    ndcg = compute_ndcg(grades, k=5)
    assert 0.0 <= ndcg <= 1.0
    # Perfect ranking gets nDCG of 1.0
    perfect_grades = [2, 2, 1, 0, 0]
    assert compute_ndcg(perfect_grades, k=5) == 1.0
