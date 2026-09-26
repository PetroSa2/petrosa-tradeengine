"""#651 wiring: how the rest of the engine feeds and respects the protective-leg
lifecycle (dispatcher triggers, the reconciler's view of explicit legs, the
user-data stream hook and the rollback metric)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from contracts.order import OrderStatus, TradeOrder
from tradeengine.dispatcher import Dispatcher
from tradeengine.exchange_truth_store import ExchangeTruthStore, PositionSnapshot
from tradeengine.metrics import protective_leg_mode_status, set_protective_leg_mode
from tradeengine.position_reconciler import (
    _order_is_reduce_only,
    detect_unhedged_positions,
)
from tradeengine.strategy_position_manager import strategy_position_manager


class _FakeConsumer:
    def __init__(self, store: ExchangeTruthStore) -> None:
        self.store = store


@pytest.fixture(autouse=True)
def _isolated_strategy_positions() -> Any:
    saved = dict(strategy_position_manager.strategy_positions)
    strategy_position_manager.strategy_positions.clear()
    yield
    strategy_position_manager.strategy_positions.clear()
    strategy_position_manager.strategy_positions.update(saved)


def _dispatcher() -> Dispatcher:
    exchange = AsyncMock()
    exchange._get_current_price = AsyncMock(return_value=50000.0)
    exchange.get_percent_price_filter = MagicMock(
        return_value={"multiplierUp": "1.10", "multiplierDown": "0.90"}
    )
    exchange.execute = AsyncMock(return_value={"status": "FILLED", "order_id": "c1"})
    disp = Dispatcher(exchange=exchange)
    disp.protective_leg_manager.request_sync = MagicMock()  # type: ignore[method-assign]
    return disp


def test_dispatcher_owns_a_leg_manager_bound_to_its_oco_manager() -> None:
    disp = Dispatcher(exchange=AsyncMock())
    assert disp.protective_leg_manager._oco is disp.oco_manager
    assert disp.protective_leg_manager.running is False


@pytest.mark.asyncio
async def test_exchange_pair_exists_does_not_fall_back_to_individual_legs() -> None:
    """#550's exchange-truth dedup used to fall through to the individual
    SL/TP fallback — with explicit-quantity legs that stacks a second full-size
    pair on the side. The existing pair is resized by the leg manager instead."""
    disp = _dispatcher()
    disp.oco_manager.place_oco_orders = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "status": "skipped_exchange_pair_exists",
            "sl_order_id": None,
            "tp_order_id": None,
        }
    )
    disp._place_individual_risk_orders = AsyncMock()  # type: ignore[method-assign]
    order = TradeOrder(
        symbol="BTCUSDT",
        side="buy",
        type="market",
        amount=0.01,
        stop_loss=45000.0,
        take_profit=55000.0,
        position_side="LONG",
        status=OrderStatus.PENDING,
    )

    await disp._place_risk_management_orders(
        order, {"fill_price": 50000.0, "amount": 0.01, "status": "filled"}
    )

    disp.oco_manager.place_oco_orders.assert_awaited_once()
    disp._place_individual_risk_orders.assert_not_awaited()
    disp.protective_leg_manager.request_sync.assert_called_with(
        "BTCUSDT", "LONG", reason="risk_management"
    )


@pytest.mark.asyncio
async def test_reduce_only_fill_requests_leg_sync_too() -> None:
    disp = _dispatcher()
    order = TradeOrder(
        symbol="BTCUSDT",
        side="sell",
        type="market",
        amount=0.01,
        position_side="LONG",
        reduce_only=True,
        status=OrderStatus.PENDING,
    )
    await disp._place_risk_management_orders(order, {"amount": 0.01})
    disp.protective_leg_manager.request_sync.assert_called_once_with(
        "BTCUSDT", "LONG", reason="risk_management"
    )


@pytest.mark.asyncio
async def test_close_with_cleanup_requests_leg_sync() -> None:
    disp = _dispatcher()
    disp.position_manager.close_position_record = AsyncMock()  # type: ignore[method-assign]
    store = ExchangeTruthStore()
    store._positions = {
        ("BNBUSDT", "LONG"): PositionSnapshot(
            symbol="BNBUSDT",
            side="LONG",
            quantity=0.17,
            entry_price=600.0,
            unrealized_pnl=0.0,
        )
    }
    store._is_ready = True
    disp.user_data_consumer = _FakeConsumer(store)  # type: ignore[assignment]

    result = await disp.close_position_with_cleanup(
        position_id="",
        symbol="BNBUSDT",
        position_side="LONG",
        quantity=0.17,
        reason="take_profit",
    )

    assert result["position_closed"] is True
    disp.protective_leg_manager.request_sync.assert_called_with(
        "BNBUSDT", "LONG", reason="close:take_profit"
    )


@pytest.mark.asyncio
async def test_skipped_close_on_flat_side_still_requests_leg_sync() -> None:
    disp = _dispatcher()
    store = ExchangeTruthStore()
    store._is_ready = True  # confidently flat
    disp.user_data_consumer = _FakeConsumer(store)  # type: ignore[assignment]

    result = await disp.close_position_with_cleanup(
        position_id="",
        symbol="BNBUSDT",
        position_side="LONG",
        quantity=0.17,
        reason="cio_exit_now",
    )

    assert result["status"] == "skipped_no_exchange_position"
    disp.protective_leg_manager.request_sync.assert_called_with(
        "BNBUSDT", "LONG", reason="close:cio_exit_now"
    )


@pytest.mark.asyncio
async def test_user_data_closing_fill_runs_post_fill_guard() -> None:
    disp = _dispatcher()
    disp.protective_leg_manager.on_protective_fill = MagicMock()  # type: ignore[method-assign]
    disp._record_reduce_only_fill = AsyncMock()  # type: ignore[method-assign]

    await disp._on_user_data_fill(
        {"s": "DOTUSDT", "i": 42, "S": "SELL", "R": True, "o": "MARKET", "ps": "LONG"}
    )

    disp.protective_leg_manager.on_protective_fill.assert_called_once_with(
        "DOTUSDT", "LONG", "42"
    )


def test_account_update_change_requests_side_syncs() -> None:
    disp = _dispatcher()
    disp._on_exchange_position_change([("DOTUSDT", "LONG"), ("XRPUSDT", "SHORT")])
    calls = [c.args for c in disp.protective_leg_manager.request_sync.call_args_list]
    assert calls == [("DOTUSDT", "LONG"), ("XRPUSDT", "SHORT")]


@pytest.mark.asyncio
async def test_replaced_leg_repoints_strategy_and_position_records() -> None:
    disp = Dispatcher(exchange=AsyncMock())
    old_id, new_id = "1000000000000001", "1000000000000009"
    strategy_position_manager.strategy_positions["sp1"] = {
        "sl_order_id": old_id,
        "tp_order_id": "1000000000000002",
        "status": "open",
    }
    disp.oco_manager.active_oco_pairs["DOTUSDT_LONG"] = [
        {"position_id": "pos-9", "sl_order_id": new_id, "status": "active"},
        {"position_id": "reconciled_x", "sl_order_id": new_id, "status": "active"},
    ]
    disp.position_manager.update_position_risk_orders = AsyncMock()  # type: ignore[method-assign]

    await disp._on_protective_leg_replaced("DOTUSDT", "LONG", "SL", old_id, new_id)

    assert strategy_position_manager.strategy_positions["sp1"]["sl_order_id"] == new_id
    disp.position_manager.update_position_risk_orders.assert_awaited_once_with(
        "pos-9", stop_loss_order_id=new_id
    )


# ---------------------------------------------------------------------------
# PositionReconciler: explicit-quantity hedge legs ARE protection
# ---------------------------------------------------------------------------
def test_hedge_closing_direction_counts_as_reduce_only() -> None:
    assert _order_is_reduce_only(
        {
            "positionSide": "LONG",
            "side": "SELL",
            "reduceOnly": False,
            "closePosition": False,
        }
    )
    assert _order_is_reduce_only({"positionSide": "SHORT", "side": "BUY"})
    assert not _order_is_reduce_only({"positionSide": "LONG", "side": "BUY"})
    assert not _order_is_reduce_only({"positionSide": "BOTH", "side": "SELL"})


def test_explicit_quantity_legs_hedge_the_position() -> None:
    """Without this the naked-position remediator (arm_only in prod) would
    read every explicit-qty pair as missing and stack a new pair each cycle."""
    positions = {
        ("DOTUSDT", "LONG"): {
            "symbol": "DOTUSDT",
            "positionSide": "LONG",
            "positionAmt": "82.8",
        }
    }
    legs = [
        {
            "positionSide": "LONG",
            "side": "SELL",
            "orderType": kind,
            "reduceOnly": False,
            "closePosition": False,
            "quantity": "82.8",
        }
        for kind in ("STOP_MARKET", "TAKE_PROFIT_MARKET")
    ]
    assert detect_unhedged_positions(positions, {"DOTUSDT": legs}) == []


# ---------------------------------------------------------------------------
# User-data stream hook
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_truth_store_reports_changed_sides() -> None:
    store = ExchangeTruthStore()
    calls: list[list[tuple[str, str]]] = []
    store.set_on_position_change(calls.append)
    opened = {
        "a": {"P": [{"s": "DOTUSDT", "ps": "LONG", "pa": "82.8", "ep": "4", "up": "0"}]}
    }
    closed = {
        "a": {"P": [{"s": "DOTUSDT", "ps": "LONG", "pa": "0", "ep": "0", "up": "0"}]}
    }

    await store.update_positions_from_account_update(opened)
    await store.update_positions_from_account_update(opened)  # unchanged
    await store.update_positions_from_account_update(closed)

    assert calls == [[("DOTUSDT", "LONG")], [("DOTUSDT", "LONG")]]


@pytest.mark.asyncio
async def test_truth_store_callback_failure_is_contained() -> None:
    store = ExchangeTruthStore()
    store.set_on_position_change(MagicMock(side_effect=RuntimeError("boom")))
    event = {
        "a": {"P": [{"s": "DOTUSDT", "ps": "LONG", "pa": "1", "ep": "4", "up": "0"}]}
    }
    await store.update_positions_from_account_update(event)
    assert store.get_positions()[("DOTUSDT", "LONG")].quantity == 1.0


def test_protective_leg_mode_gauge() -> None:
    set_protective_leg_mode("close_position")
    assert protective_leg_mode_status.labels(mode="close_position")._value.get() == 1
    assert protective_leg_mode_status.labels(mode="explicit_qty")._value.get() == 0
    set_protective_leg_mode("garbage")
    assert protective_leg_mode_status.labels(mode="explicit_qty")._value.get() == 1
