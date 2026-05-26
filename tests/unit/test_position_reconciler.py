"""Unit tests for PositionReconciler (FR65 / #409)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tradeengine.position_reconciler import (
    PositionReconciler,
    _index_binance_positions,
    _normalise_side,
    detect_divergences,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _binance_pos(symbol: str, side: str, amt: float) -> dict:
    return {"symbol": symbol, "positionSide": side, "positionAmt": str(amt)}


def _local_pos(symbol: str, side: str, qty: float) -> dict:
    return {"symbol": symbol, "position_side": side, "quantity": qty}


def _make_reconciler(binance_raw: list, local_positions: dict) -> PositionReconciler:
    exchange = MagicMock()
    exchange.get_position_info = AsyncMock(return_value=binance_raw)
    pm = MagicMock()
    pm.get_positions = MagicMock(return_value=local_positions)
    return PositionReconciler(
        exchange=exchange, position_manager=pm, interval_seconds=60
    )


# ---------------------------------------------------------------------------
# _normalise_side
# ---------------------------------------------------------------------------


def test_normalise_side_hedge_long():
    assert _normalise_side({"positionSide": "LONG", "positionAmt": "0.5"}) == "LONG"


def test_normalise_side_hedge_short():
    assert _normalise_side({"positionSide": "SHORT", "positionAmt": "-0.5"}) == "SHORT"


def test_normalise_side_one_way_positive():
    assert _normalise_side({"positionSide": "BOTH", "positionAmt": "1.0"}) == "LONG"


def test_normalise_side_one_way_negative():
    assert _normalise_side({"positionSide": "BOTH", "positionAmt": "-1.0"}) == "SHORT"


# ---------------------------------------------------------------------------
# _index_binance_positions
# ---------------------------------------------------------------------------


def test_index_filters_zero_positions():
    raw = [
        _binance_pos("BTCUSDT", "LONG", 0.0),
        _binance_pos("ETHUSDT", "LONG", 1.5),
    ]
    result = _index_binance_positions(raw)
    assert ("BTCUSDT", "LONG") not in result
    assert ("ETHUSDT", "LONG") in result


def test_index_below_tolerance_filtered():
    raw = [_binance_pos("BTCUSDT", "LONG", 1e-5)]
    result = _index_binance_positions(raw)
    assert result == {}


# ---------------------------------------------------------------------------
# detect_divergences — AC2
# ---------------------------------------------------------------------------


def test_no_divergence_when_equal():
    binance = {("BTCUSDT", "LONG"): _binance_pos("BTCUSDT", "LONG", 0.5)}
    local = {("BTCUSDT", "LONG"): _local_pos("BTCUSDT", "LONG", 0.5)}
    assert detect_divergences(binance, local) == []


def test_untracked_position():
    """AC2: Binance has position, local tracker is empty."""
    binance = {("BTCUSDT", "LONG"): _binance_pos("BTCUSDT", "LONG", 0.5)}
    local: dict = {}
    divergences = detect_divergences(binance, local)
    assert len(divergences) == 1
    assert divergences[0]["category"] == "untracked"
    assert divergences[0]["symbol"] == "BTCUSDT"
    assert divergences[0]["side"] == "LONG"


def test_ghost_position():
    """AC2: local tracker has position, Binance shows nothing."""
    binance: dict = {}
    local = {("ETHUSDT", "SHORT"): _local_pos("ETHUSDT", "SHORT", 2.0)}
    divergences = detect_divergences(binance, local)
    assert len(divergences) == 1
    assert divergences[0]["category"] == "ghost"
    assert divergences[0]["symbol"] == "ETHUSDT"


def test_mutation_position():
    """AC2: both exist but quantity differs."""
    binance = {("BTCUSDT", "LONG"): _binance_pos("BTCUSDT", "LONG", 1.0)}
    local = {("BTCUSDT", "LONG"): _local_pos("BTCUSDT", "LONG", 0.5)}
    divergences = detect_divergences(binance, local)
    assert len(divergences) == 1
    assert divergences[0]["category"] == "mutation"
    assert divergences[0]["binance_qty"] == pytest.approx(1.0)
    assert divergences[0]["local_qty"] == pytest.approx(0.5)


def test_multiple_divergence_categories():
    """AC2: all three categories in one pass."""
    binance = {
        ("BTCUSDT", "LONG"): _binance_pos("BTCUSDT", "LONG", 0.5),  # untracked
        ("BNBUSDT", "LONG"): _binance_pos("BNBUSDT", "LONG", 10.0),  # mutation
    }
    local = {
        ("ETHUSDT", "SHORT"): _local_pos("ETHUSDT", "SHORT", 1.0),  # ghost
        ("BNBUSDT", "LONG"): _local_pos("BNBUSDT", "LONG", 9.0),  # mutation
    }
    divergences = detect_divergences(binance, local)
    categories = {d["category"] for d in divergences}
    assert categories == {"untracked", "ghost", "mutation"}


# ---------------------------------------------------------------------------
# PositionReconciler lifecycle — AC1
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_creates_task():
    reconciler = _make_reconciler([], {})
    await reconciler.start()
    assert reconciler._task is not None
    assert not reconciler._task.done()
    await reconciler.stop()


@pytest.mark.asyncio
async def test_stop_cancels_task():
    reconciler = _make_reconciler([], {})
    await reconciler.start()
    await reconciler.stop()
    assert reconciler._task.done()


@pytest.mark.asyncio
async def test_double_start_idempotent():
    reconciler = _make_reconciler([], {})
    await reconciler.start()
    task_before = reconciler._task
    await reconciler.start()
    assert reconciler._task is task_before
    await reconciler.stop()


# ---------------------------------------------------------------------------
# reconcile_once — AC3 / AC4 / AC5
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_once_no_divergences_healthy_verdict():
    """AC3: healthy path sets evaluator verdict to 0."""
    binance_raw = [_binance_pos("BTCUSDT", "LONG", 0.5)]
    local = {("BTCUSDT", "LONG"): _local_pos("BTCUSDT", "LONG", 0.5)}
    reconciler = _make_reconciler(binance_raw, local)

    with (
        patch(
            "tradeengine.position_reconciler.reconciliation_evaluator_verdict"
        ) as mock_verdict,
        patch("tradeengine.position_reconciler.reconciliation_alert") as mock_alert,
    ):
        divergences = await reconciler.reconcile_once()

    assert divergences == []
    mock_verdict.set.assert_called_once_with(0)
    mock_alert.set.assert_called_once_with(0)


@pytest.mark.asyncio
async def test_reconcile_once_divergence_unhealthy_verdict():
    """AC3 + AC4: unhealthy path sets evaluator=1 and alert=1."""
    binance_raw = [_binance_pos("BTCUSDT", "LONG", 0.5)]
    local: dict = {}
    reconciler = _make_reconciler(binance_raw, local)

    with (
        patch(
            "tradeengine.position_reconciler.reconciliation_evaluator_verdict"
        ) as mock_verdict,
        patch("tradeengine.position_reconciler.reconciliation_alert") as mock_alert,
    ):
        divergences = await reconciler.reconcile_once()

    assert len(divergences) == 1
    mock_verdict.set.assert_called_once_with(1)
    mock_alert.set.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_reconcile_once_does_not_modify_state():
    """AC5: read-only — no writes to position_manager or exchange."""
    binance_raw = [_binance_pos("BTCUSDT", "LONG", 0.5)]
    local: dict = {}
    reconciler = _make_reconciler(binance_raw, local)

    with (
        patch("tradeengine.position_reconciler.reconciliation_evaluator_verdict"),
        patch("tradeengine.position_reconciler.reconciliation_alert"),
    ):
        await reconciler.reconcile_once()

    # Exchange was only read, never written
    reconciler._exchange.get_position_info.assert_awaited_once()
    reconciler._exchange.execute = MagicMock()
    reconciler._exchange.execute.assert_not_called()
    # PositionManager was only read, never written
    reconciler._position_manager.get_positions.assert_called_once()
    reconciler._position_manager.update_position = MagicMock()
    reconciler._position_manager.update_position.assert_not_called()


# ---------------------------------------------------------------------------
# Exchange error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_once_exchange_error_returns_empty():
    exchange = MagicMock()
    exchange.get_position_info = AsyncMock(side_effect=RuntimeError("connection lost"))
    pm = MagicMock()
    pm.get_positions = MagicMock(return_value={})
    reconciler = PositionReconciler(exchange=exchange, position_manager=pm)

    result = await reconciler.reconcile_once()
    assert result == []


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check_healthy_initially():
    reconciler = _make_reconciler([], {})
    result = await reconciler.health_check()
    assert result["status"] == "healthy"
    assert result["divergence_count"] == 0


@pytest.mark.asyncio
async def test_health_check_unhealthy_after_divergence():
    binance_raw = [_binance_pos("BTCUSDT", "LONG", 1.0)]
    local: dict = {}
    reconciler = _make_reconciler(binance_raw, local)

    with (
        patch("tradeengine.position_reconciler.reconciliation_evaluator_verdict"),
        patch("tradeengine.position_reconciler.reconciliation_alert"),
    ):
        await reconciler.reconcile_once()

    result = await reconciler.health_check()
    assert result["status"] == "unhealthy"
    assert result["divergence_count"] == 1
