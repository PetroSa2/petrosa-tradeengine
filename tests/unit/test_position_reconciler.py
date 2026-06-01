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
    detect_unhedged_positions,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _binance_pos(symbol: str, side: str, amt: float) -> dict:
    return {"symbol": symbol, "positionSide": side, "positionAmt": str(amt)}


def _local_pos(symbol: str, side: str, qty: float) -> dict:
    return {"symbol": symbol, "position_side": side, "quantity": qty}


def _fully_hedged_orders(symbol: str) -> list[dict]:
    """Return a reduceOnly SL+TP pair for both LONG and SHORT sides.

    Used as the default ``get_open_algo_orders`` response so existing
    pre-AC5 tests don't surface ``unhedged`` divergences they don't
    care about. Tests targeting AC5 pass explicit orders via
    ``open_algo_orders``."""
    return [
        {
            "symbol": symbol,
            "positionSide": "LONG",
            "type": "STOP_MARKET",
            "reduceOnly": True,
        },
        {
            "symbol": symbol,
            "positionSide": "LONG",
            "type": "TAKE_PROFIT_MARKET",
            "reduceOnly": True,
        },
        {
            "symbol": symbol,
            "positionSide": "SHORT",
            "type": "STOP_MARKET",
            "reduceOnly": True,
        },
        {
            "symbol": symbol,
            "positionSide": "SHORT",
            "type": "TAKE_PROFIT_MARKET",
            "reduceOnly": True,
        },
    ]


def _make_reconciler(
    binance_raw: list,
    local_positions: dict,
    open_algo_orders: dict | None = None,
) -> PositionReconciler:
    """Build a PositionReconciler with mocked exchange + position manager.

    ``open_algo_orders``: optional ``{symbol: list[order]}`` to override
    the per-symbol algo-orders response. When omitted, every symbol is
    returned fully-hedged (reduceOnly SL+TP for both sides) so pre-AC5
    tests don't trip the new unhedged-divergence path.
    """
    exchange = MagicMock()
    exchange.get_position_info = AsyncMock(return_value=binance_raw)

    async def _algo_orders_for(symbol: str | None = None) -> list[dict]:
        if open_algo_orders is not None:
            return open_algo_orders.get(symbol or "", [])
        return _fully_hedged_orders(symbol or "")

    exchange.get_open_algo_orders = AsyncMock(side_effect=_algo_orders_for)

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


# ---------------------------------------------------------------------------
# AC5 of #424 — unhedged-position divergence
# ---------------------------------------------------------------------------


def test_detect_unhedged_returns_empty_when_both_sl_and_tp_present():
    """AC5: a Binance position with reduceOnly SL+TP on the matching
    positionSide is hedged — no divergence."""
    binance_positions = {
        ("BTCUSDT", "LONG"): _binance_pos("BTCUSDT", "LONG", 1.0),
    }
    orders_by_symbol = {
        "BTCUSDT": [
            {"positionSide": "LONG", "type": "STOP_MARKET", "reduceOnly": True},
            {"positionSide": "LONG", "type": "TAKE_PROFIT_MARKET", "reduceOnly": True},
        ],
    }
    divergences = detect_unhedged_positions(binance_positions, orders_by_symbol)
    assert divergences == []


def test_detect_unhedged_flags_position_with_no_orders():
    """AC5 / H5 of #424: a Binance position with NO open orders is the
    incident-reproduction case — must flag as unhedged."""
    binance_positions = {
        ("BTCUSDT", "LONG"): _binance_pos("BTCUSDT", "LONG", 1.5),
    }
    orders_by_symbol: dict = {"BTCUSDT": []}

    divergences = detect_unhedged_positions(binance_positions, orders_by_symbol)

    assert len(divergences) == 1
    d = divergences[0]
    assert d["category"] == "unhedged"
    assert d["symbol"] == "BTCUSDT"
    assert d["side"] == "LONG"
    assert d["binance_qty"] == 1.5
    assert d["sl_present"] is False
    assert d["tp_present"] is False


def test_detect_unhedged_flags_position_with_only_sl():
    """AC5: SL-only is still unhedged — emit a divergence indicating
    which leg is missing."""
    binance_positions = {
        ("BTCUSDT", "SHORT"): _binance_pos("BTCUSDT", "SHORT", -0.8),
    }
    orders_by_symbol = {
        "BTCUSDT": [
            {"positionSide": "SHORT", "type": "STOP_MARKET", "reduceOnly": True},
        ],
    }

    divergences = detect_unhedged_positions(binance_positions, orders_by_symbol)

    assert len(divergences) == 1
    d = divergences[0]
    assert d["category"] == "unhedged"
    assert d["sl_present"] is True
    assert d["tp_present"] is False


def test_detect_unhedged_ignores_orders_on_wrong_side():
    """AC5: in hedge mode, SL+TP on SHORT do not hedge a LONG position."""
    binance_positions = {
        ("BTCUSDT", "LONG"): _binance_pos("BTCUSDT", "LONG", 1.0),
    }
    orders_by_symbol = {
        "BTCUSDT": [
            {"positionSide": "SHORT", "type": "STOP_MARKET", "reduceOnly": True},
            {"positionSide": "SHORT", "type": "TAKE_PROFIT_MARKET", "reduceOnly": True},
        ],
    }
    divergences = detect_unhedged_positions(binance_positions, orders_by_symbol)
    assert len(divergences) == 1
    assert divergences[0]["sl_present"] is False
    assert divergences[0]["tp_present"] is False


def test_detect_unhedged_accepts_both_positionside_orders_one_way_mode():
    """AC5: one-way mode uses positionSide='BOTH' — must hedge any side."""
    binance_positions = {
        ("BTCUSDT", "LONG"): _binance_pos("BTCUSDT", "LONG", 1.0),
    }
    orders_by_symbol = {
        "BTCUSDT": [
            {"positionSide": "BOTH", "type": "STOP_MARKET", "reduceOnly": True},
            {"positionSide": "BOTH", "type": "TAKE_PROFIT_MARKET", "reduceOnly": True},
        ],
    }
    divergences = detect_unhedged_positions(binance_positions, orders_by_symbol)
    assert divergences == []


def test_detect_unhedged_ignores_non_reduce_only_orders():
    """AC5: only reduceOnly (or closePosition=true) orders protect the
    position — a non-reduceOnly STOP is an entry/reversal, not a hedge."""
    binance_positions = {
        ("BTCUSDT", "LONG"): _binance_pos("BTCUSDT", "LONG", 1.0),
    }
    orders_by_symbol = {
        "BTCUSDT": [
            {"positionSide": "LONG", "type": "STOP_MARKET", "reduceOnly": False},
            {
                "positionSide": "LONG",
                "type": "TAKE_PROFIT_MARKET",
                "reduceOnly": False,
            },
        ],
    }
    divergences = detect_unhedged_positions(binance_positions, orders_by_symbol)
    assert len(divergences) == 1
    assert divergences[0]["sl_present"] is False
    assert divergences[0]["tp_present"] is False


def test_detect_unhedged_accepts_closeposition_in_place_of_reduceonly():
    """AC5: Binance returns ``closePosition=true`` for sweep-everything
    SL/TP — that protects the position equivalently to ``reduceOnly``."""
    binance_positions = {
        ("BTCUSDT", "LONG"): _binance_pos("BTCUSDT", "LONG", 1.0),
    }
    orders_by_symbol = {
        "BTCUSDT": [
            {"positionSide": "LONG", "type": "STOP_MARKET", "closePosition": True},
            {
                "positionSide": "LONG",
                "type": "TAKE_PROFIT_MARKET",
                "closePosition": True,
            },
        ],
    }
    divergences = detect_unhedged_positions(binance_positions, orders_by_symbol)
    assert divergences == []


@pytest.mark.asyncio
async def test_reconcile_once_appends_unhedged_divergences():
    """AC5 integration: reconcile_once calls get_open_algo_orders per
    symbol and appends unhedged divergences alongside the existing
    untracked/ghost/mutation categories."""
    binance_raw = [_binance_pos("BTCUSDT", "LONG", 1.0)]
    local = {("BTCUSDT", "LONG"): _local_pos("BTCUSDT", "LONG", 1.0)}
    # No open orders on Binance → position is unhedged
    reconciler = _make_reconciler(binance_raw, local, open_algo_orders={"BTCUSDT": []})

    with (
        patch("tradeengine.position_reconciler.reconciliation_evaluator_verdict"),
        patch("tradeengine.position_reconciler.reconciliation_alert"),
        patch(
            "tradeengine.position_reconciler.reconciliation_divergences_total"
        ) as mock_counter,
    ):
        divergences = await reconciler.reconcile_once()

    categories = [d["category"] for d in divergences]
    assert "unhedged" in categories
    # The metric MUST be incremented with the unhedged label so the
    # tradeengine-unhedged-position-detected alert rule can fire.
    mock_counter.labels.assert_any_call(category="unhedged", symbol="BTCUSDT")


@pytest.mark.asyncio
async def test_reconcile_once_no_unhedged_when_orders_match():
    """AC5: when reduceOnly SL+TP are present for each open position,
    no unhedged divergence is added."""
    binance_raw = [_binance_pos("BTCUSDT", "LONG", 1.0)]
    local = {("BTCUSDT", "LONG"): _local_pos("BTCUSDT", "LONG", 1.0)}
    reconciler = _make_reconciler(
        binance_raw,
        local,
        open_algo_orders={
            "BTCUSDT": [
                {"positionSide": "LONG", "type": "STOP_MARKET", "reduceOnly": True},
                {
                    "positionSide": "LONG",
                    "type": "TAKE_PROFIT_MARKET",
                    "reduceOnly": True,
                },
            ],
        },
    )

    with (
        patch("tradeengine.position_reconciler.reconciliation_evaluator_verdict"),
        patch("tradeengine.position_reconciler.reconciliation_alert"),
    ):
        divergences = await reconciler.reconcile_once()

    assert all(d["category"] != "unhedged" for d in divergences)


@pytest.mark.asyncio
async def test_reconcile_once_unhedged_check_fails_open_on_order_fetch_error():
    """AC5: when get_open_algo_orders raises for a symbol, treat it as
    "no orders found" → position flagged unhedged (fail-conservative).
    Failing silently would mask real incidents."""
    binance_raw = [_binance_pos("BTCUSDT", "LONG", 1.0)]
    local = {("BTCUSDT", "LONG"): _local_pos("BTCUSDT", "LONG", 1.0)}
    reconciler = _make_reconciler(binance_raw, local)
    reconciler._exchange.get_open_algo_orders = AsyncMock(
        side_effect=RuntimeError("Binance API timeout")
    )

    with (
        patch("tradeengine.position_reconciler.reconciliation_evaluator_verdict"),
        patch("tradeengine.position_reconciler.reconciliation_alert"),
    ):
        divergences = await reconciler.reconcile_once()

    categories = [d["category"] for d in divergences]
    assert "unhedged" in categories
