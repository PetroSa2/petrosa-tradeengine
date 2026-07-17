"""Adversarial tests for tradeengine#502 — unbounded mirror manufactures ~2x prices.

`correct_protective_price` in tradeengine/risk/sl_tp_direction.py, when a
wrong-side absolute price arrives WITHOUT a pct hint, mirrors across the
reference using ``implied_pct = abs(requested - reference)/reference`` and
``effective_pct = max(implied_pct, min_distance_pct)``. There is **no upper
bound** on ``effective_pct``. A garbage / far-wrong-side requested price
therefore yields ``corrected ≈ reference * (1 + effective_pct)`` which can be
2x, 3x, or arbitrarily large — exactly the BCHUSDT $223.66 → $454.66 (+103%)
observed on 2026-07-16, which Binance then rejected with -2021.

These tests assert the CORRECT (post-fix) behavior: the mirror must be bounded
by ``MAX_PLAUSIBLE_DISTANCE_PCT`` so it never emits a price outside a plausible
band. When the implied distance exceeds the bound the correction clamps to the
safe configured floor (``min_distance_pct``) instead of mirroring to ~2x/4x.

#502 has landed — the xfail markers were removed with the fix (per the marker
contract in the original red tests) and these now pass green.
"""

from __future__ import annotations

import pytest

from tradeengine.risk.sl_tp_direction import correct_protective_price

# Maximum plausible protective-order distance. Ties to the widest PERCENT_PRICE
# band the exchange will accept for a stop; anything beyond this is a broken
# input, not a legitimate stop. The fix should reject or clamp to <= this.
MAX_PLAUSIBLE_DISTANCE_PCT = 0.20  # 20% — generous upper bound


def _distance_pct(price: float, reference: float) -> float:
    return abs(price - reference) / reference


class TestMirrorMustBeBounded:
    """A wrong-side price with no pct hint must never mirror to an absurd level."""

    def test_bchusdt_shortish_garbage_does_not_produce_2x(self) -> None:
        """The exact 2026-07-16 shape: reference ~223, wrong-side requested near 0
        implies ~100% distance → current code emits ~2x reference. Must not."""
        out = correct_protective_price(
            kind="SL",
            position_side="LONG",
            requested_price=0.01,  # effectively garbage / far wrong side
            requested_pct=None,
            reference_price=223.66,
            min_distance_pct=0.06,
        )
        # Post-fix: corrected price must stay within a plausible band of reference.
        assert _distance_pct(out.price, 223.66) <= MAX_PLAUSIBLE_DISTANCE_PCT, (
            f"Corrected SL {out.price} is {_distance_pct(out.price, 223.66) * 100:.1f}% "
            f"from reference 223.66 — unbounded mirror explosion (#502)"
        )

    def test_short_sl_far_wrong_side_bounded(self) -> None:
        # SHORT SL must be ABOVE reference; requested far below (near 0) implies
        # ~100% → current code mirrors to ~2x. Must be bounded instead.
        out = correct_protective_price(
            kind="SL",
            position_side="SHORT",
            requested_price=0.5,
            requested_pct=None,
            reference_price=100.0,
            min_distance_pct=0.06,
        )
        assert _distance_pct(out.price, 100.0) <= MAX_PLAUSIBLE_DISTANCE_PCT

    def test_long_tp_far_wrong_side_bounded(self) -> None:
        out = correct_protective_price(
            kind="TP",
            position_side="LONG",
            requested_price=0.01,
            requested_pct=None,
            reference_price=50.0,
            min_distance_pct=0.0,
        )
        assert _distance_pct(out.price, 50.0) <= MAX_PLAUSIBLE_DISTANCE_PCT


class TestMirrorRegressionTable:
    """Table of the exact wrong-side incident shapes (2026-07-16). Pre-#502 the
    unbounded mirror produced the 2x/4x prices in the comments; post-#502 every
    one of these must instead CLAMP to the safe floor (6%) and stay well inside
    the plausible band. Locks in the behavior change so it is explicit in diff.
    """

    @pytest.mark.parametrize(
        "reference,requested,pre_fix_multiple",
        [
            (223.66, 0.01, 2.0),  # ~2x — the BCHUSDT incident (wrong side, below)
            (100.0, 1.0, 2.0),  # near-0 → ~2x pre-fix (wrong side, below)
        ],
    )
    def test_wrong_side_mirror_now_clamps_to_floor(
        self, reference: float, requested: float, pre_fix_multiple: float
    ) -> None:
        """SHORT SL: wrong-side (below ref) with no pct hint and an implied
        distance far beyond the plausible band must clamp to the 6% safe floor,
        never mirror to the pre-fix multiple documented above."""
        floor = 0.06
        out = correct_protective_price(
            kind="SL",
            position_side="SHORT",  # SHORT SL must be above ref; below = wrong side
            requested_price=requested,
            requested_pct=None,
            reference_price=reference,
            min_distance_pct=floor,
        )
        # Post-#502 contract: clamped to the floor on the required (above) side.
        assert out.was_corrected is True
        assert out.was_clamped is True
        assert out.price == pytest.approx(reference * (1 + floor))
        # And the result is inside the plausible band, not the pre-fix explosion.
        assert _distance_pct(out.price, reference) <= MAX_PLAUSIBLE_DISTANCE_PCT
        # Sanity: pre-fix would have produced this absurd multiple.
        assert (
            reference * pre_fix_multiple
        ) / reference - 1 > MAX_PLAUSIBLE_DISTANCE_PCT

    def test_correct_side_but_absurdly_far_clamps_to_max(self) -> None:
        """SHORT SL: requested 200 vs reference 50 is on the CORRECT side (above)
        but 300% away — pre-fix passed straight through as ~4x. Post-#502 it
        clamps back to the max plausible distance on the correct side."""
        reference, requested = 50.0, 200.0
        out = correct_protective_price(
            kind="SL",
            position_side="SHORT",
            requested_price=requested,
            requested_pct=None,
            reference_price=reference,
            min_distance_pct=0.06,
        )
        assert out.was_corrected is True
        assert out.was_clamped is True
        assert out.price == pytest.approx(reference * (1 + MAX_PLAUSIBLE_DISTANCE_PCT))
        assert _distance_pct(out.price, reference) <= MAX_PLAUSIBLE_DISTANCE_PCT


class TestSmallCorrectionsStillWork:
    """AC2: small, plausible wrong-side corrections must still work as before —
    the bound only trips on absurd inputs, never on legitimate ones."""

    def test_small_wrong_side_mirror_unchanged(self) -> None:
        """A wrong-side SL implying a modest ~8% distance (inside the band) still
        mirrors normally and is NOT clamped."""
        reference = 100.0
        # LONG SL must be BELOW ref; requested above by 8% = wrong side, small.
        out = correct_protective_price(
            kind="SL",
            position_side="LONG",
            requested_price=108.0,
            requested_pct=None,
            reference_price=reference,
            min_distance_pct=0.06,
        )
        assert out.was_corrected is True
        assert out.was_clamped is False
        # implied 8% > floor 6% → mirrors below by 8%.
        assert out.price == pytest.approx(reference * (1 - 0.08))
        assert _distance_pct(out.price, reference) <= MAX_PLAUSIBLE_DISTANCE_PCT

    def test_pct_hint_within_band_unchanged(self) -> None:
        """A provided pct hint inside the plausible band is honored verbatim and
        not clamped (AC2 regression)."""
        reference = 100.0
        out = correct_protective_price(
            kind="TP",
            position_side="LONG",
            requested_price=95.0,  # wrong side (TP LONG must be above)
            requested_pct=0.10,  # 10% hint, inside band
            reference_price=reference,
            min_distance_pct=0.0,
        )
        assert out.was_corrected is True
        assert out.was_clamped is False
        assert out.price == pytest.approx(reference * (1 + 0.10))

    def test_correct_side_price_untouched(self) -> None:
        """A price already on the correct side is never corrected or clamped."""
        out = correct_protective_price(
            kind="SL",
            position_side="LONG",
            requested_price=94.0,  # below ref = correct side for LONG SL
            requested_pct=None,
            reference_price=100.0,
            min_distance_pct=0.06,
        )
        assert out.was_corrected is False
        assert out.was_clamped is False
        assert out.price == pytest.approx(94.0)
