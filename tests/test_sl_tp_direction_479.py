"""Tests for tradeengine#479 — SL/TP direction inversion fix.

Covers AC1 (LONG SL below entry), AC2 (SHORT SL above entry), AC3 (position-side-
aware bounds), and AC5 (regression test for the 18:42:03 LTCUSDT scenario from
the issue body). AC6 (post-fix stops-health endpoint) is a deploy-time check
outside the unit-test surface.
"""

from __future__ import annotations

import pytest

from tradeengine.risk.sl_tp_direction import (
    DirectionCorrection,
    correct_protective_price,
)

# ---------------------------------------------------------------------------
# Pure helper tests — exercise the position-side-aware direction guard.
# ---------------------------------------------------------------------------


class TestSLDirectionLong:
    """AC1: LONG SL must land BELOW entry."""

    def test_long_sl_already_below_entry_passes_through(self) -> None:
        out = correct_protective_price(
            kind="SL",
            position_side="LONG",
            requested_price=40.0,
            requested_pct=None,
            reference_price=43.32,
            min_distance_pct=0.06,
        )
        assert out.was_corrected is False
        assert out.price == pytest.approx(40.0)

    def test_long_sl_wrong_side_with_pct_recomputes_correctly(self) -> None:
        out = correct_protective_price(
            kind="SL",
            position_side="LONG",
            requested_price=44.84,  # wrong side: +3.51% above entry
            requested_pct=0.0351,
            reference_price=43.32,
            min_distance_pct=0.06,
        )
        assert out.was_corrected is True
        # pct (3.51%) is below safety floor (6%) → clamp to 6% below entry
        assert out.price == pytest.approx(43.32 * (1 - 0.06))
        assert out.price < 43.32

    def test_long_sl_wrong_side_without_pct_mirrors_then_clamps(self) -> None:
        """The bug observed 2026-06-18 — strategy sends absolute SL only."""
        out = correct_protective_price(
            kind="SL",
            position_side="LONG",
            requested_price=44.84,  # +3.51% wrong side
            requested_pct=None,  # no pct hint at all
            reference_price=43.32,
            min_distance_pct=0.06,
        )
        assert out.was_corrected is True
        assert out.price < 43.32, "Corrected LONG SL must land below entry"
        # implied 3.51% < 6% floor → land at floor edge
        assert out.price == pytest.approx(43.32 * (1 - 0.06))

    def test_long_sl_wrong_side_pct_above_floor_uses_pct(self) -> None:
        out = correct_protective_price(
            kind="SL",
            position_side="LONG",
            requested_price=50.0,  # wrong side
            requested_pct=0.08,  # 8% — above 6% floor
            reference_price=43.32,
            min_distance_pct=0.06,
        )
        assert out.was_corrected is True
        assert out.price == pytest.approx(43.32 * (1 - 0.08))

    def test_long_sl_correct_side_inside_floor_passes_through(self) -> None:
        """On correct side, the direction helper does NOT clamp — the existing
        dispatcher floor + binance safety-floor remain the authoritative
        upstream/downstream gates. This avoids double-clamping that would
        regress the strategy-level MIN_SL_DISTANCE_PCT semantics."""
        out = correct_protective_price(
            kind="SL",
            position_side="LONG",
            requested_price=43.0,  # below entry, 0.74% — inside 6% binance floor
            requested_pct=None,
            reference_price=43.32,
            min_distance_pct=0.06,
        )
        assert out.was_corrected is False
        assert out.price == pytest.approx(43.0)


class TestSLDirectionShort:
    """AC2: SHORT SL must land ABOVE entry."""

    def test_short_sl_already_above_entry_passes_through(self) -> None:
        out = correct_protective_price(
            kind="SL",
            position_side="SHORT",
            requested_price=46.0,  # +6.2%
            requested_pct=None,
            reference_price=43.32,
            min_distance_pct=0.06,
        )
        assert out.was_corrected is False
        assert out.price == pytest.approx(46.0)

    def test_short_sl_wrong_side_with_pct_recomputes_correctly(self) -> None:
        out = correct_protective_price(
            kind="SL",
            position_side="SHORT",
            requested_price=41.80,  # wrong side: below entry
            requested_pct=0.0351,
            reference_price=43.32,
            min_distance_pct=0.06,
        )
        assert out.was_corrected is True
        assert out.price > 43.32, "Corrected SHORT SL must land above entry"
        assert out.price == pytest.approx(43.32 * (1 + 0.06))

    def test_short_sl_wrong_side_without_pct_mirrors(self) -> None:
        """Implied 7.66% > 6% floor → mirror at the implied distance."""
        out = correct_protective_price(
            kind="SL",
            position_side="SHORT",
            requested_price=40.0,  # below entry — wrong for SHORT (7.66% away)
            requested_pct=None,
            reference_price=43.32,
            min_distance_pct=0.06,
        )
        assert out.was_corrected is True
        assert out.price > 43.32
        implied = abs(40.0 - 43.32) / 43.32
        assert out.price == pytest.approx(43.32 * (1 + implied))

    def test_short_sl_wrong_side_below_floor_clamps(self) -> None:
        """Implied 3.51% < 6% floor → clamp to floor edge."""
        out = correct_protective_price(
            kind="SL",
            position_side="SHORT",
            requested_price=41.80,
            requested_pct=None,
            reference_price=43.32,
            min_distance_pct=0.06,
        )
        assert out.was_corrected is True
        assert out.price == pytest.approx(43.32 * (1 + 0.06))


class TestTPDirection:
    """LONG TP must be ABOVE entry; SHORT TP must be BELOW entry. No safety floor."""

    def test_long_tp_correct_side_passes_through(self) -> None:
        out = correct_protective_price(
            kind="TP",
            position_side="LONG",
            requested_price=45.08,  # +4.06%
            requested_pct=None,
            reference_price=43.32,
            min_distance_pct=0.0,
        )
        assert out.was_corrected is False
        assert out.price == pytest.approx(45.08)

    def test_long_tp_wrong_side_mirrors(self) -> None:
        out = correct_protective_price(
            kind="TP",
            position_side="LONG",
            requested_price=41.50,  # below entry — wrong for LONG TP
            requested_pct=None,
            reference_price=43.32,
            min_distance_pct=0.0,
        )
        assert out.was_corrected is True
        assert out.price > 43.32

    def test_short_tp_correct_side_passes_through(self) -> None:
        out = correct_protective_price(
            kind="TP",
            position_side="SHORT",
            requested_price=41.50,
            requested_pct=None,
            reference_price=43.32,
            min_distance_pct=0.0,
        )
        assert out.was_corrected is False

    def test_short_tp_wrong_side_with_pct(self) -> None:
        out = correct_protective_price(
            kind="TP",
            position_side="SHORT",
            requested_price=45.08,  # above entry — wrong for SHORT TP
            requested_pct=0.04,
            reference_price=43.32,
            min_distance_pct=0.0,
        )
        assert out.was_corrected is True
        assert out.price < 43.32
        assert out.price == pytest.approx(43.32 * (1 - 0.04))


class TestEdgeCases:
    def test_zero_reference_price_raises(self) -> None:
        with pytest.raises(ValueError, match="reference_price") as exc_info:
            correct_protective_price(
                kind="SL",
                position_side="LONG",
                requested_price=40.0,
                requested_pct=None,
                reference_price=0.0,
                min_distance_pct=0.06,
            )
        assert "reference_price" in str(exc_info.value)

    def test_negative_reference_price_raises(self) -> None:
        with pytest.raises(ValueError, match="reference_price") as exc_info:
            correct_protective_price(
                kind="SL",
                position_side="LONG",
                requested_price=40.0,
                requested_pct=None,
                reference_price=-1.0,
                min_distance_pct=0.06,
            )
        assert "reference_price" in str(exc_info.value)

    def test_correction_result_carries_original_price(self) -> None:
        out = correct_protective_price(
            kind="SL",
            position_side="LONG",
            requested_price=44.84,
            requested_pct=None,
            reference_price=43.32,
            min_distance_pct=0.06,
        )
        assert isinstance(out, DirectionCorrection)
        assert out.original_price == pytest.approx(44.84)
        assert "wrong side" in out.reason.lower()


# ---------------------------------------------------------------------------
# AC5 — regression test for the LTCUSDT 18:42:03 scenario from the issue body.
# ---------------------------------------------------------------------------


class TestLTCUSDTRegression:
    """Replay the exact 2026-06-18 18:42:03 LTCUSDT inversion that triggered #479."""

    def test_ltcusdt_long_entry_43_32_sl_44_84_gets_corrected(self) -> None:
        out = correct_protective_price(
            kind="SL",
            position_side="LONG",
            requested_price=44.84,
            requested_pct=None,
            reference_price=43.32,
            min_distance_pct=0.06,
        )
        assert out.was_corrected is True, "LTCUSDT LONG SL must be corrected"
        assert out.price < 43.32, (
            f"Corrected SL ({out.price}) must be BELOW entry (43.32) for LONG"
        )
        assert out.price == pytest.approx(43.32 * (1 - 0.06))
        # The corrected price now satisfies the binance safety floor (6%) — so
        # downstream validate_and_adjust_price_for_percent_filter will accept it
        # instead of rejecting with `sl_unreachable_within_filter`.

    def test_ltcusdt_long_entry_43_32_tp_45_08_passes_through(self) -> None:
        """TP at +4.06% above entry was correct in the issue — must not be flipped."""
        out = correct_protective_price(
            kind="TP",
            position_side="LONG",
            requested_price=45.08,
            requested_pct=None,
            reference_price=43.32,
            min_distance_pct=0.0,
        )
        assert out.was_corrected is False, (
            "LONG TP above entry was already correct and must not be touched"
        )
        assert out.price == pytest.approx(45.08)

    def test_bnbusdt_long_sl_inversion(self) -> None:
        """Second symbol from the issue evidence — BNBUSDT entry 575.67, SL 595.71."""
        out = correct_protective_price(
            kind="SL",
            position_side="LONG",
            requested_price=595.71,
            requested_pct=None,
            reference_price=575.67,
            min_distance_pct=0.06,
        )
        assert out.was_corrected is True
        assert out.price < 575.67
        assert out.price == pytest.approx(575.67 * (1 - 0.06))

    def test_linkusdt_long_sl_inversion(self) -> None:
        """Third symbol — LINKUSDT entry 7.836, SL 8.079."""
        out = correct_protective_price(
            kind="SL",
            position_side="LONG",
            requested_price=8.079,
            requested_pct=None,
            reference_price=7.836,
            min_distance_pct=0.06,
        )
        assert out.was_corrected is True
        assert out.price < 7.836
        assert out.price == pytest.approx(7.836 * (1 - 0.06))


# ---------------------------------------------------------------------------
# OCO defense-in-depth — OCOManager.place_oco_orders must refuse wrong-side input.
# ---------------------------------------------------------------------------


class TestOCODefenseInDepth:
    """OCO is the second line of defense. If a caller bypasses the dispatcher
    direction validator (NakedPositionRemediator, PositionHealthGuard, etc.),
    the OCO entry must refuse to ship a known-wrong-side leg rather than fire
    an order destined to immediately trigger or get rejected with -2021."""

    @pytest.mark.asyncio
    async def test_oco_refuses_long_with_sl_above_entry(self) -> None:
        from unittest.mock import AsyncMock, MagicMock

        from tradeengine.dispatcher import OCOManager

        exchange = MagicMock()
        exchange._get_current_price = AsyncMock(return_value=43.32)
        logger = MagicMock()
        manager = OCOManager(exchange=exchange, logger=logger, dispatcher=None)

        result = await manager.place_oco_orders(
            position_id="pos-1",
            symbol="LTCUSDT",
            position_side="LONG",
            quantity=1.924,
            stop_loss_price=44.84,  # WRONG: above entry for LONG
            take_profit_price=45.08,
            entry_price=43.32,
        )

        assert result["status"] == "rejected"
        assert result["error"] == "direction_inversion"
        assert result["sl_wrong_side"] is True
        assert result["tp_wrong_side"] is False

    @pytest.mark.asyncio
    async def test_oco_refuses_short_with_tp_above_entry(self) -> None:
        from unittest.mock import AsyncMock, MagicMock

        from tradeengine.dispatcher import OCOManager

        exchange = MagicMock()
        exchange._get_current_price = AsyncMock(return_value=43.32)
        logger = MagicMock()
        manager = OCOManager(exchange=exchange, logger=logger, dispatcher=None)

        result = await manager.place_oco_orders(
            position_id="pos-2",
            symbol="LTCUSDT",
            position_side="SHORT",
            quantity=1.924,
            stop_loss_price=45.50,  # correct for SHORT (above entry)
            take_profit_price=46.00,  # WRONG: above entry for SHORT TP
            entry_price=43.32,
        )

        assert result["status"] == "rejected"
        assert result["error"] == "direction_inversion"
        assert result["tp_wrong_side"] is True

    @pytest.mark.asyncio
    async def test_oco_skips_defense_when_entry_price_absent(self) -> None:
        """When entry_price is not provided the defense skips — the dispatcher
        is the authoritative direction validator. This keeps OCO callers that
        legitimately omit entry_price (e.g. re-arm paths) from being blocked
        by a mocked exchange that returns a non-credible market price."""
        from unittest.mock import AsyncMock, MagicMock

        from tradeengine.dispatcher import OCOManager

        exchange = AsyncMock()
        logger = MagicMock()
        manager = OCOManager(exchange=exchange, logger=logger, dispatcher=None)

        result = await manager.place_oco_orders(
            position_id="pos-3",
            symbol="LTCUSDT",
            position_side="LONG",
            quantity=1.924,
            stop_loss_price=44.84,  # would be wrong-side, but no entry → skip check
            take_profit_price=45.08,
            entry_price=None,
        )

        # Direction defense didn't fire (status != rejected/direction_inversion).
        # Actual placement outcome depends on the mocked exchange; only thing we
        # care about is that the early-rejection path didn't kick in.
        assert result.get("error") != "direction_inversion"
