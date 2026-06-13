"""Tests for tradeengine#461 — shadow-compare mode for ExchangeTruthStore migration.

AC1: in shadow mode, divergence emits exchange_truth_shadow_delta_total; local store is authority.
AC2: flip-criteria constants documented in code (zero shadow_delta over 24h window).
AC4:
- Unit test: divergence between local and exchange emits shadow_delta counter
- Unit test: local store remains the authority in shadow mode (return value unchanged)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tradeengine.exchange_truth_store import PositionSnapshot
from tradeengine.position_manager import PositionManager


def _make_position_manager(flag: str, positions: dict, exchange_positions: dict):
    """Return a PositionManager with the flag set and both stores populated."""
    pm = PositionManager()
    pm.positions = positions

    mock_store = MagicMock()
    mock_store.get_positions.return_value = exchange_positions
    pm.exchange_truth_store = mock_store

    return pm, flag


def _local_pos(symbol: str, side: str, qty: float) -> dict:
    return {
        "symbol": symbol,
        "position_side": side,
        "quantity": qty,
        "avg_price": 45000.0,
        "status": "open",
    }


def _exchange_snap(symbol: str, side: str, qty: float) -> PositionSnapshot:
    return PositionSnapshot(
        symbol=symbol,
        side=side,
        quantity=qty,
        entry_price=45000.0,
        unrealized_pnl=0.0,
    )


@patch("tradeengine.position_manager.TE_EXCHANGE_TRUTH_STORE_ENABLED", "shadow")
def test_shadow_mode_quantity_divergence_emits_counter():
    """AC1/AC4: quantity mismatch → shadow_delta{field=quantity} emitted."""
    local = {("BTCUSDT", "LONG"): _local_pos("BTCUSDT", "LONG", 0.5)}
    exchange = {
        ("BTCUSDT", "LONG"): _exchange_snap("BTCUSDT", "LONG", 0.3)
    }  # different qty
    pm, _ = _make_position_manager("shadow", local, exchange)

    with (
        patch("tradeengine.position_manager.TE_EXCHANGE_TRUTH_STORE_ENABLED", "shadow"),
        patch(
            "tradeengine.position_manager.exchange_truth_shadow_delta_total"
        ) as mock_ctr,
    ):
        result = pm.get_positions()

    mock_ctr.labels.assert_called_once_with(
        symbol="BTCUSDT", side="LONG", field="quantity"
    )
    mock_ctr.labels.return_value.inc.assert_called_once()
    # Local authority: returned value reflects local store
    assert result[("BTCUSDT", "LONG")]["quantity"] == 0.5


@patch("tradeengine.position_manager.TE_EXCHANGE_TRUTH_STORE_ENABLED", "shadow")
def test_shadow_mode_local_is_authority_return_value():
    """AC1/AC4: in shadow mode, get_positions() always returns local store regardless of divergence."""
    local = {("BTCUSDT", "LONG"): _local_pos("BTCUSDT", "LONG", 1.0)}
    exchange = {("BTCUSDT", "LONG"): _exchange_snap("BTCUSDT", "LONG", 0.0)}
    pm, _ = _make_position_manager("shadow", local, exchange)

    with (
        patch("tradeengine.position_manager.TE_EXCHANGE_TRUTH_STORE_ENABLED", "shadow"),
        patch("tradeengine.position_manager.exchange_truth_shadow_delta_total"),
    ):
        result = pm.get_positions()

    assert ("BTCUSDT", "LONG") in result
    assert result[("BTCUSDT", "LONG")]["quantity"] == 1.0


@patch("tradeengine.position_manager.TE_EXCHANGE_TRUTH_STORE_ENABLED", "shadow")
def test_shadow_mode_missing_in_exchange_emits_counter():
    """AC1: position in local but not on exchange → missing_in_exchange delta."""
    local = {("ETHUSDT", "SHORT"): _local_pos("ETHUSDT", "SHORT", 2.0)}
    exchange = {}  # exchange sees nothing
    pm, _ = _make_position_manager("shadow", local, exchange)

    with (
        patch("tradeengine.position_manager.TE_EXCHANGE_TRUTH_STORE_ENABLED", "shadow"),
        patch(
            "tradeengine.position_manager.exchange_truth_shadow_delta_total"
        ) as mock_ctr,
    ):
        pm.get_positions()

    mock_ctr.labels.assert_called_once_with(
        symbol="ETHUSDT", side="SHORT", field="missing_in_exchange"
    )


@patch("tradeengine.position_manager.TE_EXCHANGE_TRUTH_STORE_ENABLED", "shadow")
def test_shadow_mode_missing_in_local_emits_counter():
    """AC1: position on exchange but not in local → missing_in_local delta."""
    local = {}  # local sees nothing
    exchange = {("SOLUSDT", "LONG"): _exchange_snap("SOLUSDT", "LONG", 5.0)}
    pm, _ = _make_position_manager("shadow", local, exchange)

    with (
        patch("tradeengine.position_manager.TE_EXCHANGE_TRUTH_STORE_ENABLED", "shadow"),
        patch(
            "tradeengine.position_manager.exchange_truth_shadow_delta_total"
        ) as mock_ctr,
    ):
        pm.get_positions()

    mock_ctr.labels.assert_called_once_with(
        symbol="SOLUSDT", side="LONG", field="missing_in_local"
    )


@patch("tradeengine.position_manager.TE_EXCHANGE_TRUTH_STORE_ENABLED", "shadow")
def test_shadow_mode_no_divergence_emits_no_counter():
    """AC1: identical local and exchange → no shadow_delta emitted."""
    local = {("BTCUSDT", "LONG"): _local_pos("BTCUSDT", "LONG", 0.5)}
    exchange = {("BTCUSDT", "LONG"): _exchange_snap("BTCUSDT", "LONG", 0.5)}
    pm, _ = _make_position_manager("shadow", local, exchange)

    with (
        patch("tradeengine.position_manager.TE_EXCHANGE_TRUTH_STORE_ENABLED", "shadow"),
        patch(
            "tradeengine.position_manager.exchange_truth_shadow_delta_total"
        ) as mock_ctr,
    ):
        pm.get_positions()

    mock_ctr.labels.assert_not_called()


def test_off_mode_returns_local_no_exchange_read():
    """off mode: exchange_truth_store is never queried."""
    local = {("BTCUSDT", "LONG"): _local_pos("BTCUSDT", "LONG", 0.5)}
    pm, _ = _make_position_manager("off", local, {})

    with patch("tradeengine.position_manager.TE_EXCHANGE_TRUTH_STORE_ENABLED", "off"):
        result = pm.get_positions()

    pm.exchange_truth_store.get_positions.assert_not_called()
    assert ("BTCUSDT", "LONG") in result
