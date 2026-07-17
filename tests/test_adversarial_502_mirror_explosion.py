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
by a sane maximum distance so it never emits a price outside a plausible band.
They are EXPECTED TO FAIL against current code (red) and pass once #502 lands.

Marked ``xfail(strict=True)`` so CI is green now AND flips loud (XPASS→fail)
the moment the bug is fixed, forcing the marker to be removed with the fix.
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

    @pytest.mark.xfail(
        strict=True,
        reason="#502: mirror is unbounded; produces ~2x reference for garbage input",
    )
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

    @pytest.mark.xfail(
        strict=True,
        reason="#502: mirror unbounded for far wrong-side SHORT SL",
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

    @pytest.mark.xfail(
        strict=True,
        reason="#502: TP mirror also unbounded",
    )
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
    """Table of wrong-side inputs → current mirror output, documenting the blast
    radius. These are NOT xfail: they PASS today and lock in the observed buggy
    magnitudes so the fix's behavior change is explicit and reviewable in diff.
    """

    @pytest.mark.parametrize(
        "reference,requested,expected_multiple",
        [
            (223.66, 0.01, 2.0),  # ~2x — the incident
            (100.0, 1.0, 2.0),  # near-0 → ~2x
            (50.0, 200.0, 4.0),  # 3x wrong-side above → ~4x reference
        ],
    )
    def test_current_mirror_explodes(
        self, reference: float, requested: float, expected_multiple: float
    ) -> None:
        """LONG SL: wrong-side (above ref) with no pct → mirrors below by implied
        distance. Documents that huge implied distances pass straight through."""
        out = correct_protective_price(
            kind="SL",
            position_side="SHORT",  # SHORT SL must be above ref; below = wrong side
            requested_price=requested,
            requested_pct=None,
            reference_price=reference,
            min_distance_pct=0.06,
        )
        implied = _distance_pct(requested, reference)
        # Current (buggy) contract: corrected = reference * (1 + implied), no cap.
        assert out.price == pytest.approx(reference * (1 + implied))
        # And that magnitude is absurd (> plausible band) — proving the bug.
        assert _distance_pct(out.price, reference) > MAX_PLAUSIBLE_DISTANCE_PCT
