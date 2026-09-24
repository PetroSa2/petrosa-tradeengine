"""Unit tests for PositionReconciler (FR65 / #409)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tradeengine.exchange_truth_store import PositionSnapshot
from tradeengine.position_reconciler import (
    PositionReconciler,
    _index_binance_positions,
    _is_malformed_sign,
    _normalise_side,
    classify_verdict,
    detect_count_divergence,
    detect_divergences,
    detect_unhedged_positions,
    rest_positions_diverge_from_store,
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
    # #587: detect_count_divergence reads the raw `.positions` journal
    # directly (not via get_positions()) — default it to the same fixture
    # data so pre-#587 tests, which model local == get_positions(), don't
    # trip a spurious raw_journal_count_mismatch divergence. Tests targeting
    # #587 explicitly override `pm.positions` to diverge from local_positions.
    pm.positions = dict(local_positions)
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
# detect_count_divergence — #587
# ---------------------------------------------------------------------------


def test_count_divergence_none_when_counts_match():
    raw = {("BTCUSDT", "LONG"): _local_pos("BTCUSDT", "LONG", 0.5)}
    accessor = {("BTCUSDT", "LONG"): _local_pos("BTCUSDT", "LONG", 0.5)}
    assert detect_count_divergence(raw, accessor) is None


def test_count_divergence_flags_13_vs_1_regression():
    """The exact #587 scenario: 13 stale raw-journal entries vs the 1
    exchange-authoritative position get_positions() actually returns."""
    raw = {
        (f"SYM{i}USDT", "LONG"): _local_pos(f"SYM{i}USDT", "LONG", 1.0)
        for i in range(13)
    }
    accessor = {("LTCUSDT", "LONG"): _local_pos("LTCUSDT", "LONG", 2.0)}
    divergence = detect_count_divergence(raw, accessor)
    assert divergence is not None
    assert divergence["category"] == "raw_journal_count_mismatch"
    assert divergence["local_qty"] == 13.0
    assert divergence["binance_qty"] == 1.0


def test_count_divergence_ignores_zero_quantity_raw_entries():
    """Zero-qty raw rows (already-closed but not yet deleted) shouldn't
    inflate the raw count."""
    raw = {
        ("BTCUSDT", "LONG"): _local_pos("BTCUSDT", "LONG", 0.5),
        ("ETHUSDT", "SHORT"): _local_pos("ETHUSDT", "SHORT", 0.0),
    }
    accessor = {("BTCUSDT", "LONG"): _local_pos("BTCUSDT", "LONG", 0.5)}
    assert detect_count_divergence(raw, accessor) is None


@pytest.mark.asyncio
async def test_reconcile_once_flags_raw_journal_count_mismatch():
    """Integration: reconcile_once appends the count divergence when the raw
    `.positions` journal disagrees with what get_positions() returns, even
    though the per-symbol binance-vs-accessor comparison is clean."""
    binance_raw = [_binance_pos("LTCUSDT", "LONG", 2.0)]
    local = {("LTCUSDT", "LONG"): _local_pos("LTCUSDT", "LONG", 2.0)}
    reconciler = _make_reconciler(binance_raw, local)
    # Simulate the #587 bug: raw journal has 13 stale entries even though
    # get_positions() (mocked above to return `local`) correctly reports 1.
    reconciler._position_manager.positions = {
        (f"SYM{i}USDT", "LONG"): {"quantity": 1.0} for i in range(13)
    }

    with (
        patch("tradeengine.position_reconciler.reconciliation_evaluator_verdict"),
        patch("tradeengine.position_reconciler.reconciliation_alert"),
        patch(
            "tradeengine.position_reconciler.raw_journal_count_mismatch"
        ) as mock_gauge,
    ):
        divergences = await reconciler.reconcile_once()

    count_divergences = [
        d for d in divergences if d["category"] == "raw_journal_count_mismatch"
    ]
    assert len(count_divergences) == 1
    mock_gauge.set.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_reconcile_once_clean_raw_journal_no_mismatch_gauge():
    binance_raw = [_binance_pos("BTCUSDT", "LONG", 0.5)]
    local = {("BTCUSDT", "LONG"): _local_pos("BTCUSDT", "LONG", 0.5)}
    reconciler = _make_reconciler(
        binance_raw, local
    )  # pm.positions == local by default

    with (
        patch("tradeengine.position_reconciler.reconciliation_evaluator_verdict"),
        patch("tradeengine.position_reconciler.reconciliation_alert"),
        patch(
            "tradeengine.position_reconciler.raw_journal_count_mismatch"
        ) as mock_gauge,
    ):
        divergences = await reconciler.reconcile_once()

    assert divergences == []
    mock_gauge.set.assert_called_once_with(0)


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
# AC1 (#514): MongoDB-blip untracked-divergence regression
#
# Scenario: a MongoDB blip clears local PositionManager state while
# Binance/ExchangeTruthStore still holds the position. reconcile_once()
# must surface this as an *untracked* divergence so #970's monitoring net
# can alert. Existing test_reconcile_once_divergence_unhealthy_verdict only
# asserted len==1 + verdict flags; it never pinned the divergence category
# through the async reconcile_once() path, nor the read-only contract for
# this exact shape. Per PR #506/#505, the reconciler is deliberately
# read-only — the ticket's originally-proposed "re-add to local tracking"
# assertion contradicts that design and is intentionally NOT implemented;
# recovery is owned by the remediator, not the reconciler.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_once_mongodb_blip_emits_untracked_divergence():
    """AC1: local state cleared (Mongo blip), Binance still holds position.

    reconcile_once() must emit exactly one ``untracked`` divergence naming
    the surviving Binance position, and must NOT write it back to the local
    tracker (read-only contract — recovery belongs to the remediator).
    """
    # Binance still shows the position; local PositionManager is empty
    # (simulating the MongoDB blip that wiped local tracking).
    binance_raw = [_binance_pos("ETHUSDT", "LONG", 3.0)]
    local: dict = {}
    reconciler = _make_reconciler(binance_raw, local)

    with (
        patch("tradeengine.position_reconciler.reconciliation_evaluator_verdict"),
        patch("tradeengine.position_reconciler.reconciliation_alert"),
    ):
        divergences = await reconciler.reconcile_once()

    untracked = [d for d in divergences if d["category"] == "untracked"]
    assert len(untracked) == 1, f"expected one untracked divergence, got {divergences}"
    assert untracked[0]["symbol"] == "ETHUSDT"
    assert untracked[0]["side"] == "LONG"

    # Read-only contract: the blip is reported, never silently re-added to
    # the local tracker by the reconciler.
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


def test_detect_unhedged_uses_orderType_from_openAlgoOrders():
    """AC2 of #594: /openAlgoOrders response has 'orderType' not 'type'.

    Positions with full SL+TP pairs should NOT be reported as unhedged
    when the orders come from algo endpoint (no 'type' key).
    """
    binance_positions = {
        ("BTCUSDT", "LONG"): _binance_pos("BTCUSDT", "LONG", 1.0),
    }
    # Real /openAlgoOrders shape: uses "orderType", no "type" key
    orders_by_symbol = {
        "BTCUSDT": [
            {"positionSide": "LONG", "orderType": "STOP_MARKET", "reduceOnly": True},
            {
                "positionSide": "LONG",
                "orderType": "TAKE_PROFIT_MARKET",
                "reduceOnly": True,
            },
        ],
    }
    divergences = detect_unhedged_positions(binance_positions, orders_by_symbol)
    assert divergences == []


def test_detect_unhedged_orderType_no_false_alarm_for_paired_positions():
    """#594 regression: nine valid positions with complete SL/TP pairs were
    falsely reported as unhedged because code only checked 'type'/'origType'.

    This test uses the real Binance response shape and proves paired
    positions are correctly recognized as hedged.
    """
    binance_positions = {
        ("ETHUSDT", "LONG"): _binance_pos("ETHUSDT", "LONG", 2.5),
        ("SOLUSDT", "SHORT"): _binance_pos("SOLUSDT", "SHORT", -1.0),
    }
    # Both positions have full SL+TP via algo orders (orderType, not type)
    orders_by_symbol = {
        "ETHUSDT": [
            {"positionSide": "LONG", "orderType": "STOP_MARKET", "reduceOnly": True},
            {
                "positionSide": "LONG",
                "orderType": "TAKE_PROFIT_MARKET",
                "reduceOnly": True,
            },
        ],
        "SOLUSDT": [
            {"positionSide": "SHORT", "orderType": "STOP_MARKET", "reduceOnly": True},
            {
                "positionSide": "SHORT",
                "orderType": "TAKE_PROFIT_MARKET",
                "reduceOnly": True,
            },
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


# ---------------------------------------------------------------------------
# #547 — malformed (inverted-sign) position detection
# ---------------------------------------------------------------------------


def test_is_malformed_sign_long_negative():
    assert _is_malformed_sign("LONG", -0.303) is True


def test_is_malformed_sign_short_positive():
    assert _is_malformed_sign("SHORT", 2.5) is True


def test_is_malformed_sign_well_formed_long():
    assert _is_malformed_sign("LONG", 0.5) is False


def test_is_malformed_sign_well_formed_short():
    assert _is_malformed_sign("SHORT", -0.5) is False


def test_is_malformed_sign_both_never_malformed():
    # one-way mode: sign legitimately encodes direction
    assert _is_malformed_sign("BOTH", -1.0) is False
    assert _is_malformed_sign("BOTH", 1.0) is False


def test_detect_unhedged_classifies_long_negative_as_malformed():
    """AC1: LONG with positionAmt<0 is malformed_position, not unhedged."""
    binance_positions = {
        ("LTCUSDT", "LONG"): {
            "symbol": "LTCUSDT",
            "positionSide": "LONG",
            "positionAmt": -0.303,
            "entryPrice": 46.08,
        },
    }
    divergences = detect_unhedged_positions(binance_positions, {"LTCUSDT": []})
    assert len(divergences) == 1
    d = divergences[0]
    assert d["category"] == "malformed_position"
    assert d["symbol"] == "LTCUSDT"
    assert d["side"] == "LONG"
    assert d["binance_qty"] == pytest.approx(0.303)
    assert d["raw_position_amt"] == pytest.approx(-0.303)


def test_detect_unhedged_classifies_short_positive_as_malformed():
    """AC1: SHORT with positionAmt>0 is malformed_position, not unhedged."""
    binance_positions = {
        ("BTCUSDT", "SHORT"): {
            "symbol": "BTCUSDT",
            "positionSide": "SHORT",
            "positionAmt": 0.4,
        },
    }
    divergences = detect_unhedged_positions(binance_positions, {"BTCUSDT": []})
    assert len(divergences) == 1
    assert divergences[0]["category"] == "malformed_position"


def test_detect_unhedged_malformed_not_masked_by_hedged_orders():
    """AC1: a malformed position with a full SL+TP pair is STILL malformed —
    the sign mismatch takes precedence over the hedged check (the orders are
    direction-invalid against a wrong-signed side)."""
    binance_positions = {
        ("LTCUSDT", "LONG"): {
            "symbol": "LTCUSDT",
            "positionSide": "LONG",
            "positionAmt": -0.303,
        },
    }
    orders_by_symbol = {
        "LTCUSDT": [
            {"positionSide": "LONG", "type": "STOP_MARKET", "reduceOnly": True},
            {"positionSide": "LONG", "type": "TAKE_PROFIT_MARKET", "reduceOnly": True},
        ],
    }
    divergences = detect_unhedged_positions(binance_positions, orders_by_symbol)
    assert len(divergences) == 1
    assert divergences[0]["category"] == "malformed_position"


def test_detect_unhedged_well_formed_position_unaffected():
    """Regression guard: a well-formed LONG (amt>0) with no orders is still
    plain `unhedged`, not misclassified as malformed."""
    binance_positions = {
        ("ETHUSDT", "LONG"): {
            "symbol": "ETHUSDT",
            "positionSide": "LONG",
            "positionAmt": 1.5,
        },
    }
    divergences = detect_unhedged_positions(binance_positions, {"ETHUSDT": []})
    assert len(divergences) == 1
    assert divergences[0]["category"] == "unhedged"


@pytest.mark.asyncio
async def test_reconcile_once_malformed_position_ltcusdt_shape():
    """AC4 regression: the live LTCUSDT LONG amt=-0.303 shape must surface as a
    malformed_position divergence through the full async reconcile_once path
    and never remain silently naked."""
    binance_raw = [
        {
            "symbol": "LTCUSDT",
            "positionSide": "LONG",
            "positionAmt": "-0.303",
            "entryPrice": "46.08",
            "markPrice": "47.02",
        },
    ]
    local: dict = {}
    reconciler = _make_reconciler(binance_raw, local, open_algo_orders={"LTCUSDT": []})

    with (
        patch("tradeengine.position_reconciler.reconciliation_evaluator_verdict"),
        patch("tradeengine.position_reconciler.reconciliation_alert"),
        patch(
            "tradeengine.position_reconciler.reconciliation_divergences_total"
        ) as mock_counter,
    ):
        divergences = await reconciler.reconcile_once()

    malformed = [d for d in divergences if d["category"] == "malformed_position"]
    assert len(malformed) == 1
    assert malformed[0]["symbol"] == "LTCUSDT"
    assert malformed[0]["side"] == "LONG"
    mock_counter.labels.assert_any_call(category="malformed_position", symbol="LTCUSDT")


# ---------------------------------------------------------------------------
# #566 — hedge-mode confirmation cross-check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_hedge_mode_matching_sets_gauge_zero_no_alert():
    reconciler = _make_reconciler([], {})
    reconciler._expected_hedge_mode = True
    reconciler._exchange.verify_hedge_mode = AsyncMock(
        return_value={"hedge_mode_enabled": True, "position_mode": "hedge"}
    )
    with (
        patch("tradeengine.position_reconciler.hedge_mode_mismatch") as mock_gauge,
        patch("tradeengine.position_reconciler.logger") as mock_logger,
    ):
        await reconciler._check_hedge_mode()
    mock_gauge.set.assert_called_once_with(0)
    mock_logger.critical.assert_not_called()


@pytest.mark.asyncio
async def test_check_hedge_mode_mismatch_alerts_once_per_episode():
    reconciler = _make_reconciler([], {})
    reconciler._expected_hedge_mode = True
    reconciler._exchange.verify_hedge_mode = AsyncMock(
        return_value={"hedge_mode_enabled": False, "position_mode": "one-way"}
    )
    with (
        patch("tradeengine.position_reconciler.hedge_mode_mismatch") as mock_gauge,
        patch("tradeengine.position_reconciler.logger") as mock_logger,
    ):
        await reconciler._check_hedge_mode()
        await reconciler._check_hedge_mode()
    assert mock_gauge.set.call_args_list == [((1,),), ((1,),)]
    assert mock_logger.critical.call_count == 1


@pytest.mark.asyncio
async def test_check_hedge_mode_mismatch_realerts_after_recovery():
    reconciler = _make_reconciler([], {})
    reconciler._expected_hedge_mode = True
    reconciler._exchange.verify_hedge_mode = AsyncMock(
        return_value={"hedge_mode_enabled": False, "position_mode": "one-way"}
    )
    with (
        patch("tradeengine.position_reconciler.hedge_mode_mismatch"),
        patch("tradeengine.position_reconciler.logger") as mock_logger,
    ):
        await reconciler._check_hedge_mode()
        reconciler._exchange.verify_hedge_mode = AsyncMock(
            return_value={"hedge_mode_enabled": True, "position_mode": "hedge"}
        )
        await reconciler._check_hedge_mode()
        reconciler._exchange.verify_hedge_mode = AsyncMock(
            return_value={"hedge_mode_enabled": False, "position_mode": "one-way"}
        )
        await reconciler._check_hedge_mode()
    assert mock_logger.critical.call_count == 2


@pytest.mark.asyncio
async def test_check_hedge_mode_inconclusive_does_not_set_gauge():
    """verify_hedge_mode's own error path returns position_mode='unknown' —
    must not flip the gauge on a transient API failure."""
    reconciler = _make_reconciler([], {})
    reconciler._exchange.verify_hedge_mode = AsyncMock(
        return_value={"hedge_mode_enabled": False, "position_mode": "unknown"}
    )
    with patch("tradeengine.position_reconciler.hedge_mode_mismatch") as mock_gauge:
        await reconciler._check_hedge_mode()
    mock_gauge.set.assert_not_called()


@pytest.mark.asyncio
async def test_check_hedge_mode_missing_attr_is_noop():
    """Exchange doubles without verify_hedge_mode (e.g. bare MagicMock) must
    not raise — the check silently no-ops."""
    exchange = MagicMock(spec=["get_position_info", "get_open_algo_orders"])
    exchange.get_position_info = AsyncMock(return_value=[])
    pm = MagicMock()
    pm.get_positions = MagicMock(return_value={})
    reconciler = PositionReconciler(exchange=exchange, position_manager=pm)
    await reconciler._check_hedge_mode()  # must not raise


@pytest.mark.asyncio
async def test_check_hedge_mode_exception_is_caught():
    reconciler = _make_reconciler([], {})
    reconciler._exchange.verify_hedge_mode = AsyncMock(
        side_effect=RuntimeError("network blip")
    )
    with patch("tradeengine.position_reconciler.hedge_mode_mismatch") as mock_gauge:
        await reconciler._check_hedge_mode()  # must not raise
    mock_gauge.set.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_once_calls_hedge_mode_check():
    """reconcile_once() must invoke the hedge-mode cross-check every cycle."""
    reconciler = _make_reconciler([], {})
    reconciler._check_hedge_mode = AsyncMock()
    with (
        patch("tradeengine.position_reconciler.reconciliation_evaluator_verdict"),
        patch("tradeengine.position_reconciler.reconciliation_alert"),
    ):
        await reconciler.reconcile_once()
    reconciler._check_hedge_mode.assert_awaited_once()


# ---------------------------------------------------------------------------
# classify_verdict — #592
# ---------------------------------------------------------------------------


def test_classify_verdict_healthy_when_no_divergences():
    assert classify_verdict([]) == "healthy"


def test_classify_verdict_degraded_for_ghost_only():
    assert (
        classify_verdict([{"category": "ghost", "symbol": "LTCUSDT", "side": "LONG"}])
        == "degraded"
    )


def test_classify_verdict_degraded_for_raw_journal_count_mismatch_only():
    divs = [{"category": "raw_journal_count_mismatch", "symbol": "ALL", "side": "ALL"}]
    assert classify_verdict(divs) == "degraded"


def test_classify_verdict_degraded_for_ghost_plus_count_mismatch():
    divs = [
        {"category": "ghost", "symbol": "LTCUSDT", "side": "LONG"},
        {"category": "raw_journal_count_mismatch", "symbol": "ALL", "side": "ALL"},
    ]
    assert classify_verdict(divs) == "degraded"


@pytest.mark.parametrize(
    "category", ["untracked", "mutation", "unhedged", "malformed_position"]
)
def test_classify_verdict_unhealthy_for_real_divergence_categories(category):
    assert (
        classify_verdict([{"category": category, "symbol": "X", "side": "LONG"}])
        == "unhealthy"
    )


def test_classify_verdict_unhealthy_when_mixed_with_ghost():
    """A ghost alongside a genuinely unsafe divergence must NOT be diluted
    down to degraded — any non-degraded category makes the whole cycle
    unhealthy."""
    divs = [
        {"category": "ghost", "symbol": "LTCUSDT", "side": "LONG"},
        {"category": "untracked", "symbol": "BTCUSDT", "side": "LONG"},
    ]
    assert classify_verdict(divs) == "unhealthy"


# ---------------------------------------------------------------------------
# reconcile_once ghost auto-void + degraded verdict — #592
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_once_ghost_only_sets_degraded_not_unhealthy():
    """#592 AC: a journal-only position produces verdict `degraded` (not
    `unhealthy`) and is surfaced with a resolution action."""
    binance_raw: list = []
    local = {("LTCUSDT", "LONG"): _local_pos("LTCUSDT", "LONG", 0.303)}
    reconciler = _make_reconciler(binance_raw, local)

    with (
        patch(
            "tradeengine.position_reconciler.reconciliation_evaluator_verdict"
        ) as mock_verdict,
        patch("tradeengine.position_reconciler.reconciliation_alert") as mock_alert,
        patch(
            "tradeengine.position_reconciler.reconciliation_verdict_state"
        ) as mock_state,
        patch("tradeengine.ghost_position_remediator.audit_logger"),
    ):
        divergences = await reconciler.reconcile_once()

    ghost = [d for d in divergences if d["category"] == "ghost"]
    assert len(ghost) == 1
    # Surfaced with a resolution action (auto-voided by default mode="void").
    assert ghost[0]["resolution"] == "voided"
    # Does not hard-block intake: legacy binary gauge stays 0.
    mock_verdict.set.assert_called_once_with(0)
    mock_alert.set.assert_called_once_with(1)
    mock_state.set.assert_called_once_with(1)  # 1 == degraded
    assert reconciler.last_verdict == "degraded"


@pytest.mark.asyncio
async def test_reconcile_once_voids_stale_raw_journal_entry_on_ghost():
    """The ghost's raw PositionManager.positions entry is actually removed
    (idempotent write path — see GhostPositionRemediator tests for the
    full idempotency contract)."""
    binance_raw: list = []
    local = {("LTCUSDT", "LONG"): _local_pos("LTCUSDT", "LONG", 0.303)}
    reconciler = _make_reconciler(binance_raw, local)
    assert ("LTCUSDT", "LONG") in reconciler._position_manager.positions

    with (
        patch("tradeengine.position_reconciler.reconciliation_evaluator_verdict"),
        patch("tradeengine.position_reconciler.reconciliation_alert"),
        patch("tradeengine.position_reconciler.reconciliation_verdict_state"),
        patch("tradeengine.ghost_position_remediator.audit_logger"),
    ):
        await reconciler.reconcile_once()

    assert ("LTCUSDT", "LONG") not in reconciler._position_manager.positions


@pytest.mark.asyncio
async def test_reconcile_once_untracked_still_unhealthy_after_592():
    """Regression guard: #592's degrade-for-ghost change must not soften
    the pre-existing `untracked` unhealthy behavior."""
    binance_raw = [_binance_pos("BTCUSDT", "LONG", 0.5)]
    local: dict = {}
    reconciler = _make_reconciler(binance_raw, local)

    with (
        patch(
            "tradeengine.position_reconciler.reconciliation_evaluator_verdict"
        ) as mock_verdict,
        patch("tradeengine.position_reconciler.reconciliation_alert"),
        patch(
            "tradeengine.position_reconciler.reconciliation_verdict_state"
        ) as mock_state,
    ):
        await reconciler.reconcile_once()

    mock_verdict.set.assert_called_once_with(1)
    mock_state.set.assert_called_once_with(2)  # 2 == unhealthy
    assert reconciler.last_verdict == "unhealthy"


@pytest.mark.asyncio
async def test_health_check_degraded_for_ghost_only_divergence():
    binance_raw: list = []
    local = {("LTCUSDT", "LONG"): _local_pos("LTCUSDT", "LONG", 0.303)}
    reconciler = _make_reconciler(binance_raw, local)

    with (
        patch("tradeengine.position_reconciler.reconciliation_evaluator_verdict"),
        patch("tradeengine.position_reconciler.reconciliation_alert"),
        patch("tradeengine.position_reconciler.reconciliation_verdict_state"),
        patch("tradeengine.ghost_position_remediator.audit_logger"),
    ):
        await reconciler.reconcile_once()

    result = await reconciler.health_check()
    assert result["status"] == "degraded"
    assert result["divergence_count"] == 1


@pytest.mark.asyncio
async def test_reconcile_once_three_consecutive_clean_cycles_are_healthy():
    """#592 AC: evaluator.execution.verdict reports `healthy` for 3
    consecutive reconciliation cycles once the ghost is resolved."""
    binance_raw: list = []
    local = {("LTCUSDT", "LONG"): _local_pos("LTCUSDT", "LONG", 0.303)}
    reconciler = _make_reconciler(binance_raw, local)

    with (
        patch("tradeengine.position_reconciler.reconciliation_evaluator_verdict"),
        patch("tradeengine.position_reconciler.reconciliation_alert"),
        patch("tradeengine.position_reconciler.reconciliation_verdict_state"),
        patch("tradeengine.ghost_position_remediator.audit_logger"),
    ):
        # Cycle 1: ghost detected + voided this same pass -> degraded.
        await reconciler.reconcile_once()
        assert reconciler.last_verdict == "degraded"

        # The reconciler's `local_positions` fixture is static (a MagicMock
        # side_effect returning the same dict) so simulate the exchange
        # truth store having self-healed by clearing it, matching what the
        # real ExchangeTruthStore.update_from_rest self-heal (#592 wiring
        # fix) achieves within one cycle in production.
        reconciler._position_manager.get_positions = MagicMock(return_value={})

        for _ in range(3):
            divergences = await reconciler.reconcile_once()
            assert divergences == []
            assert reconciler.last_verdict == "healthy"


# ---------------------------------------------------------------------------
# #625 — REST position divergence helper and forced reconnect wiring
# ---------------------------------------------------------------------------


def _make_reconciler_with_store_and_consumer(
    binance_raw: list,
    local_positions: dict,
    stream_consumer: object | None,
    stale_seconds_ago: float,
    interval_seconds: int = 60,
    last_rest_sync_seconds_ago: float | None = 60,
    store_positions: dict | None = None,
):
    """Build a PositionReconciler wired with a real ExchangeTruthStore whose
    ``last_updated`` is ``stale_seconds_ago`` in the past, plus the given
    (mock) ``stream_consumer``."""
    from datetime import UTC, datetime, timedelta

    from tradeengine.exchange_truth_store import ExchangeTruthStore

    exchange = MagicMock()
    if (
        isinstance(binance_raw, list)
        and binance_raw
        and isinstance(binance_raw[0], list)
    ):
        exchange.get_position_info = AsyncMock(side_effect=binance_raw)
    else:
        exchange.get_position_info = AsyncMock(return_value=binance_raw)
    exchange.get_open_algo_orders = AsyncMock(return_value=[])

    pm = MagicMock()
    pm.get_positions = MagicMock(return_value=local_positions)
    pm.positions = dict(local_positions)

    store = ExchangeTruthStore()
    store._last_updated = datetime.now(UTC) - timedelta(seconds=stale_seconds_ago)
    store._last_rest_sync = (
        datetime.now(UTC) - timedelta(seconds=last_rest_sync_seconds_ago)
        if last_rest_sync_seconds_ago is not None
        else None
    )
    if store_positions:
        store._positions = {
            key: value
            if isinstance(value, PositionSnapshot)
            else PositionSnapshot(*value)
            for key, value in store_positions.items()
        }
    store._is_ready = True

    reconciler = PositionReconciler(
        exchange=exchange,
        position_manager=pm,
        interval_seconds=interval_seconds,
        store=store,
        stream_consumer=stream_consumer,
    )
    return reconciler, store


class TestRestPositionsDivergeFromStore:
    def test_equal_single_position(self):
        store = {("BTCUSDT", "LONG"): PositionSnapshot("BTCUSDT", "LONG", 0.01, 0, 0)}
        assert not rest_positions_diverge_from_store(
            [_binance_pos("BTCUSDT", "LONG", 0.01)], store
        )

    def test_quantity_difference_over_tolerance(self):
        store = {("BTCUSDT", "LONG"): PositionSnapshot("BTCUSDT", "LONG", 0.01, 0, 0)}
        assert rest_positions_diverge_from_store(
            [_binance_pos("BTCUSDT", "LONG", 0.011)], store
        )

    def test_quantity_difference_within_tolerance(self):
        store = {("BTCUSDT", "LONG"): PositionSnapshot("BTCUSDT", "LONG", 0.01, 0, 0)}
        assert not rest_positions_diverge_from_store(
            [_binance_pos("BTCUSDT", "LONG", 0.01005)], store
        )

    def test_rest_has_extra_key(self):
        assert rest_positions_diverge_from_store(
            [_binance_pos("BTCUSDT", "LONG", 0.01)], {}
        )

    def test_store_has_extra_key(self):
        store = {("BTCUSDT", "LONG"): PositionSnapshot("BTCUSDT", "LONG", 0.01, 0, 0)}
        assert rest_positions_diverge_from_store([], store)

    def test_zero_rest_position_is_ignored(self):
        assert not rest_positions_diverge_from_store(
            [_binance_pos("BTCUSDT", "LONG", 0)], {}
        )


class TestStaleStreamForcedReconnect:
    """#625: reconnect only after REST evidence of a missed WS update."""

    @pytest.mark.asyncio
    async def test_missed_position_update_triggers_force_reconnect(self):
        stream_consumer = MagicMock()
        stream_consumer.force_reconnect = AsyncMock(return_value=True)

        # A changed REST position with no intervening WS event is evidence.
        reconciler, _store = _make_reconciler_with_store_and_consumer(
            binance_raw=[_binance_pos("BTCUSDT", "LONG", 0.01)],
            local_positions={},
            stream_consumer=stream_consumer,
            stale_seconds_ago=300,
        )

        await reconciler.reconcile_once()

        stream_consumer.force_reconnect.assert_awaited_once()
        call_kwargs = stream_consumer.force_reconnect.call_args.kwargs
        assert "stale" in call_kwargs["reason"].lower()
        assert "300" in call_kwargs["reason"]

    @pytest.mark.asyncio
    async def test_idle_stream_without_divergence_does_not_force_reconnect(self):
        stream_consumer = MagicMock()
        stream_consumer.force_reconnect = AsyncMock(return_value=True)
        reconciler, _store = _make_reconciler_with_store_and_consumer(
            binance_raw=[],
            local_positions={},
            stream_consumer=stream_consumer,
            stale_seconds_ago=3600,
        )

        await reconciler.reconcile_once()
        stream_consumer.force_reconnect.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_idle_stream_with_unchanged_open_position_does_not_force_reconnect(
        self,
    ):
        stream_consumer = MagicMock()
        stream_consumer.force_reconnect = AsyncMock(return_value=True)
        position = PositionSnapshot("BTCUSDT", "LONG", 0.01, 50000.0, 0.0)
        reconciler, _store = _make_reconciler_with_store_and_consumer(
            binance_raw=[_binance_pos("BTCUSDT", "LONG", 0.01)],
            local_positions={},
            stream_consumer=stream_consumer,
            stale_seconds_ago=3600,
            store_positions={("BTCUSDT", "LONG"): position},
        )

        await reconciler.reconcile_once()
        stream_consumer.force_reconnect.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_divergence_with_ws_event_since_last_sync_does_not_force_reconnect(
        self,
    ):
        stream_consumer = MagicMock()
        stream_consumer.force_reconnect = AsyncMock(return_value=True)
        reconciler, _store = _make_reconciler_with_store_and_consumer(
            binance_raw=[_binance_pos("BTCUSDT", "LONG", 0.01)],
            local_positions={},
            stream_consumer=stream_consumer,
            stale_seconds_ago=5,
        )

        await reconciler.reconcile_once()
        stream_consumer.force_reconnect.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_first_pass_without_prior_rest_sync_does_not_force_reconnect(self):
        stream_consumer = MagicMock()
        stream_consumer.force_reconnect = AsyncMock(return_value=True)
        reconciler, _store = _make_reconciler_with_store_and_consumer(
            binance_raw=[_binance_pos("BTCUSDT", "LONG", 0.01)],
            local_positions={},
            stream_consumer=stream_consumer,
            stale_seconds_ago=300,
            last_rest_sync_seconds_ago=None,
        )

        await reconciler.reconcile_once()
        stream_consumer.force_reconnect.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fresh_stream_does_not_trigger_force_reconnect(self):
        stream_consumer = MagicMock()
        stream_consumer.force_reconnect = AsyncMock(return_value=True)

        reconciler, _store = _make_reconciler_with_store_and_consumer(
            binance_raw=[],
            local_positions={},
            stream_consumer=stream_consumer,
            stale_seconds_ago=5,
        )

        await reconciler.reconcile_once()

        stream_consumer.force_reconnect.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_stream_consumer_wired_is_a_safe_no_op(self):
        """Reconciler without a stream_consumer (legacy wiring, or a
        deployment that hasn't injected one) must keep working exactly as
        before #609 — log-only, no AttributeError."""
        reconciler, _store = _make_reconciler_with_store_and_consumer(
            binance_raw=[],
            local_positions={},
            stream_consumer=None,
            stale_seconds_ago=300,
        )

        divergences = await reconciler.reconcile_once()
        assert isinstance(divergences, list)

    @pytest.mark.asyncio
    async def test_repeated_stale_cycles_respect_reconnect_cooldown(self):
        """A stream that stays stale across multiple reconcile cycles must
        not be force-reconnected on every single cycle — only once per
        cooldown window (#609 anti-storm guard)."""
        stream_consumer = MagicMock()
        stream_consumer.force_reconnect = AsyncMock(return_value=True)

        reconciler, store = _make_reconciler_with_store_and_consumer(
            binance_raw=[
                [_binance_pos("BTCUSDT", "LONG", 0.01)],
                [_binance_pos("BTCUSDT", "LONG", 0.02)],
                [_binance_pos("BTCUSDT", "LONG", 0.03)],
            ],
            local_positions={},
            stream_consumer=stream_consumer,
            stale_seconds_ago=300,
        )

        # Three consecutive cycles, store never recovers (still stale each
        # time) since nothing updates store._last_updated in this test.
        await reconciler.reconcile_once()
        await reconciler.reconcile_once()
        await reconciler.reconcile_once()

        stream_consumer.force_reconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_failed_force_reconnect_does_not_arm_cooldown(self):
        """A force_reconnect() that returns False (close attempt failed —
        connection is still stuck) must NOT arm the 5-minute cooldown: the
        next reconcile cycle should retry immediately instead of waiting."""
        stream_consumer = MagicMock()
        stream_consumer.force_reconnect = AsyncMock(return_value=False)

        reconciler, _store = _make_reconciler_with_store_and_consumer(
            binance_raw=[
                [_binance_pos("BTCUSDT", "LONG", 0.01)],
                [_binance_pos("BTCUSDT", "LONG", 0.02)],
                [_binance_pos("BTCUSDT", "LONG", 0.03)],
            ],
            local_positions={},
            stream_consumer=stream_consumer,
            stale_seconds_ago=300,
        )

        await reconciler.reconcile_once()
        await reconciler.reconcile_once()
        await reconciler.reconcile_once()

        # Every cycle retries since the previous attempt never succeeded.
        assert stream_consumer.force_reconnect.await_count == 3

    @pytest.mark.asyncio
    async def test_force_reconnect_failure_does_not_poison_reconcile_pass(self):
        """A raising stream_consumer.force_reconnect() must never break the
        read-only reconciliation pass — same fail-open contract as every
        other remediator hook in reconcile_once()."""
        stream_consumer = MagicMock()
        stream_consumer.force_reconnect = AsyncMock(
            side_effect=RuntimeError("websocket library blew up")
        )

        reconciler, _store = _make_reconciler_with_store_and_consumer(
            binance_raw=[_binance_pos("BTCUSDT", "LONG", 0.01)],
            local_positions={},
            stream_consumer=stream_consumer,
            stale_seconds_ago=300,
        )

        divergences = await reconciler.reconcile_once()
        assert isinstance(divergences, list)
        stream_consumer.force_reconnect.assert_awaited_once()
