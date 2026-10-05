"""Unit tests for the fit regex classifier (pipelines/build_fit_signals.py).

These run in CI without a database or raw data — they test the regex logic only.
"""

import sys
from pathlib import Path

PIPELINES_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PIPELINES_DIR))

from build_fit_signals import classify_fit, fit_label

# ── runs_small ───────────────────────────────────────────────────────────────

class TestRunsSmall:
    def test_runs_small_basic(self):
        assert "runs_small" in classify_fit("This shirt runs small, had to return.")

    def test_too_small(self):
        assert "runs_small" in classify_fit("Way too small for me.")

    def test_too_tight(self):
        assert "runs_small" in classify_fit("These pants are too tight in the waist.")

    def test_size_up(self):
        assert "runs_small" in classify_fit("I recommend you size up one size.")

    def test_sized_up(self):
        assert "runs_small" in classify_fit("I sized up to a Large and it was perfect.")

    def test_ordered_bigger(self):
        assert "runs_small" in classify_fit("Ordered a size bigger than usual.")

    def test_smaller_than_expected(self):
        assert "runs_small" in classify_fit("It's smaller than expected.")

    def test_needed_bigger_size(self):
        assert "runs_small" in classify_fit("I needed a bigger size.")


# ── true_to_size ─────────────────────────────────────────────────────────────

class TestTrueToSize:
    def test_true_to_size(self):
        assert "true_to_size" in classify_fit("True to size, fits perfectly.")

    def test_tts_abbreviation(self):
        assert "true_to_size" in classify_fit("TTS, love it.")

    def test_fits_great(self):
        assert "true_to_size" in classify_fit("Fits great, just like I expected.")

    def test_fits_perfectly(self):
        assert "true_to_size" in classify_fit("This jacket fits perfectly.")

    def test_perfect_fit(self):
        assert "true_to_size" in classify_fit("Perfect fit and comfortable.")

    def test_fits_well(self):
        assert "true_to_size" in classify_fit("Fits well, good quality.")

    def test_fits_normal_size(self):
        assert "true_to_size" in classify_fit("Fits my normal size just right.")


# ── runs_large ───────────────────────────────────────────────────────────────

class TestRunsLarge:
    def test_runs_large_basic(self):
        assert "runs_large" in classify_fit("This hoodie runs large.")

    def test_runs_big(self):
        assert "runs_large" in classify_fit("Runs big, wish I'd gotten a smaller size.")

    def test_too_big(self):
        assert "runs_large" in classify_fit("Way too big for me, returning it.")

    def test_too_loose(self):
        assert "runs_large" in classify_fit("The waist is too loose.")

    def test_too_baggy(self):
        assert "runs_large" in classify_fit("It's too baggy, not flattering at all.")

    def test_size_down(self):
        assert "runs_large" in classify_fit("You should size down.")

    def test_sized_down(self):
        assert "runs_large" in classify_fit("I sized down and it still fits loose.")

    def test_bigger_than_expected(self):
        assert "runs_large" in classify_fit("Bigger than expected for a Medium.")


# ── Negation handling ────────────────────────────────────────────────────────

class TestNegation:
    def test_not_too_small(self):
        """'not too small' should NOT be classified as runs_small."""
        labels = classify_fit("It's not too small, fits fine.")
        assert "runs_small" not in labels

    def test_not_too_small_flips_to_large(self):
        """Negated small → weak large signal."""
        labels = classify_fit("It's not too small at all.")
        assert "runs_large" in labels

    def test_doesnt_run_small(self):
        labels = classify_fit("This doesn't run small like other reviews said.")
        assert "runs_small" not in labels
        assert "runs_large" in labels

    def test_not_too_big_flips_to_small(self):
        """Negated large → weak small signal."""
        labels = classify_fit("Not too big, a nice fit.")
        assert "runs_large" not in labels
        assert "runs_small" in labels

    def test_didnt_size_up(self):
        """'didn't need to size up' negates the small signal."""
        labels = classify_fit("I didn't need to size up, it fit my normal size.")
        assert "runs_small" not in labels

    def test_negated_tts_discarded(self):
        """Negated TTS is ambiguous and should be discarded."""
        labels = classify_fit("It's not true to size at all.")
        assert "true_to_size" not in labels

    def test_usually_size_up_but_these_fit(self):
        """'I usually size up but these fit' — the 'size up' is general, not about this product.

        The regex fires on 'size up' and the negation window doesn't help here,
        but the TTS pattern also fires and adds that label.
        """
        text = "I usually size up but these fit perfectly."
        labels = classify_fit(text)
        # Both labels may appear — the important thing is TTS is present
        assert "true_to_size" in labels

    def test_wasnt_too_tight(self):
        labels = classify_fit("Wasn't too tight, fits well.")
        assert "runs_small" not in labels

    def test_never_runs_large(self):
        labels = classify_fit("This brand never runs large.")
        assert "runs_large" not in labels
        assert "runs_small" in labels  # negated large → small


# ── Edge cases ───────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_string(self):
        assert classify_fit("") == set()

    def test_no_fit_mention(self):
        assert classify_fit("Great color, fast shipping. Love it!") == set()

    def test_multiple_signals(self):
        """A review can mention both small and TTS (e.g., different items)."""
        labels = classify_fit("The shirt runs small but the pants fit great.")
        assert "runs_small" in labels
        assert "true_to_size" in labels


# ── fit_label function ───────────────────────────────────────────────────────

class TestFitLabel:
    def test_insufficient_data(self):
        assert fit_label(0.0, 2) == "insufficient_data"

    def test_runs_small_label(self):
        assert fit_label(-0.20, 10) == "runs_small"

    def test_runs_large_label(self):
        assert fit_label(0.25, 10) == "runs_large"

    def test_true_to_size_label(self):
        assert fit_label(0.05, 10) == "true_to_size"

    def test_boundary_tts(self):
        assert fit_label(0.15, 10) == "true_to_size"
        assert fit_label(-0.15, 10) == "true_to_size"
