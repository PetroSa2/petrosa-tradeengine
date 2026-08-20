"""Tests for tradeengine#551 — wrong-side SL after market crosses entry.

Root cause: ``correct_protective_price`` only guarantees a stop is on the
correct side of ENTRY. Once the market crosses entry (position underwater), an
entry-side-correct stop can sit on the WRONG side of the LIVE market and be
rejected by Binance with ``APIError(-2021)`` "Order would immediately trigger",
which cancels the surviving OCO leg and leaves the position NAKED.

The live repro was XLMUSDT SHORT: entry≈0.1637, market rose to ≈0.171, requested
SL=0.16500 (below market → immediate trigger). A SHORT stop must sit ABOVE
market. These tests pin ``enforce_market_side_stop`` (the market-relative gate)
and the mirror LONG case.
"""

from __future__ import annotations

import pytest

from tradeengine.risk.sl_tp_direction import (
    MarketSideDecision,
    correct_protective_price,
    enforce_market_side_stop,
)

# ---------------------------------------------------------------------------
# AC1 — XLMUSDT SHORT live repro: SL below market must re-anchor ABOVE market.
# ---------------------------------------------------------------------------


class TestXLMUSDTShortRepro:
    """Replay the exact 2026-08-20 XLMUSDT SHORT market-crossed-entry incident."""

    def test_short_sl_below_market_gets_reanchored_above_market(self) -> None:
        # entry-relative correction: SL 0.165 is ABOVE entry 0.1637 → correct
        # side of entry, so correct_protective_price leaves it untouched.
        entry_corr = correct_protective_price(
            kind="SL",
            position_side="SHORT",
            requested_price=0.16500,
            requested_pct=None,
            reference_price=0.16370,
            min_distance_pct=0.06,
        )
        assert entry_corr.was_corrected is False
        assert entry_corr.price == pytest.approx(0.16500)

        # market-relative gate: market has risen to 0.171, so SL 0.165 is BELOW
        # market → would immediately trigger. Must re-anchor ABOVE market.
        decision = enforce_market_side_stop(
            position_side="SHORT",
            stop_price=entry_corr.price,
            market_price=0.17100,
            min_distance_pct=0.06,
        )
        assert decision.was_reanchored is True
        assert decision.should_flatten is False
        assert decision.price > 0.17100, "SHORT SL must sit ABOVE live market"
        assert decision.price == pytest.approx(0.17100 * (1 + 0.06))

    def test_short_sl_already_above_market_passes_through(self) -> None:
        decision = enforce_market_side_stop(
            position_side="SHORT",
            stop_price=0.18200,  # ~6.4% above market
            market_price=0.17100,
            min_distance_pct=0.06,
        )
        assert decision.was_reanchored is False
        assert decision.should_flatten is False
        assert decision.price == pytest.approx(0.18200)


# ---------------------------------------------------------------------------
# AC2 — mirror LONG case: SL above market must re-anchor BELOW market.
# ---------------------------------------------------------------------------


class TestLongMarketCrossed:
    def test_long_sl_above_market_gets_reanchored_below_market(self) -> None:
        # LONG entry 100, SL 98 is correct side of entry.
        entry_corr = correct_protective_price(
            kind="SL",
            position_side="LONG",
            requested_price=98.0,
            requested_pct=None,
            reference_price=100.0,
            min_distance_pct=0.06,
        )
        assert entry_corr.was_corrected is False

        # Market fell to 95 (LONG underwater). SL 98 is now ABOVE market →
        # a stop-sell at 98 immediately triggers. Must re-anchor BELOW market.
        decision = enforce_market_side_stop(
            position_side="LONG",
            stop_price=98.0,
            market_price=95.0,
            min_distance_pct=0.06,
        )
        assert decision.was_reanchored is True
        assert decision.should_flatten is False
        assert decision.price < 95.0, "LONG SL must sit BELOW live market"
        assert decision.price == pytest.approx(95.0 * (1 - 0.06))

    def test_long_sl_already_below_market_passes_through(self) -> None:
        decision = enforce_market_side_stop(
            position_side="LONG",
            stop_price=88.0,  # ~7.4% below market
            market_price=95.0,
            min_distance_pct=0.06,
        )
        assert decision.was_reanchored is False
        assert decision.price == pytest.approx(88.0)


# ---------------------------------------------------------------------------
# AC3 — no placeable stop → signal flatten (pathological floor >= cap).
# ---------------------------------------------------------------------------


class TestFlattenEscalation:
    def test_floor_at_or_above_max_band_signals_flatten(self) -> None:
        decision = enforce_market_side_stop(
            position_side="SHORT",
            stop_price=0.16500,
            market_price=0.17100,
            min_distance_pct=0.20,  # equals default max placeable band
            max_distance_pct=0.20,
        )
        assert decision.should_flatten is True
        assert decision.was_reanchored is False

    def test_inside_floor_band_reanchors_not_flatten_when_placeable(self) -> None:
        # SHORT SL slightly above market but INSIDE the 6% floor band — must
        # re-anchor out to the floor, not flatten (floor 6% << 20% cap).
        decision = enforce_market_side_stop(
            position_side="SHORT",
            stop_price=0.17200,  # 0.58% above market — inside 6% floor
            market_price=0.17100,
            min_distance_pct=0.06,
        )
        assert decision.should_flatten is False
        assert decision.was_reanchored is True
        assert decision.price == pytest.approx(0.17100 * (1 + 0.06))


class TestEdgeCases:
    def test_zero_market_price_raises(self) -> None:
        with pytest.raises(ValueError, match="market_price") as exc_info:
            enforce_market_side_stop(
                position_side="SHORT",
                stop_price=0.165,
                market_price=0.0,
                min_distance_pct=0.06,
            )
        assert "market_price" in str(exc_info.value)

    def test_decision_carries_original_price(self) -> None:
        decision = enforce_market_side_stop(
            position_side="SHORT",
            stop_price=0.16500,
            market_price=0.17100,
            min_distance_pct=0.06,
        )
        assert isinstance(decision, MarketSideDecision)
        assert decision.original_price == pytest.approx(0.16500)
        assert "#551" in decision.reason
