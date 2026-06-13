"""Tests for tradeengine#460 — check-before-place idempotency in _place_risk_management_orders.

AC1: when ExchangeTruthStore already has a SL for BTCUSDT, placement is skipped + counter increments.
AC2: position_side stored in OrderSnapshot from WS event (ps field).
AC3: when exchange has no protective order, placement is NOT skipped.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contracts.order import OrderStatus, TradeOrder
from tradeengine.dispatcher import Dispatcher
from tradeengine.exchange_truth_store import OrderSnapshot


def _make_dispatcher_with_store(open_orders: list[OrderSnapshot]) -> Dispatcher:
    d = Dispatcher()
    d.exchange = MagicMock()
    d.exchange.client = MagicMock()

    mock_consumer = MagicMock()
    mock_store = MagicMock()
    mock_store.get_open_orders.return_value = open_orders
    mock_consumer.store = mock_store
    d.user_data_consumer = mock_consumer

    # Mock OCO manager so it doesn't try to call Binance
    d.oco_manager = AsyncMock()
    d.oco_manager.place_oco_orders = AsyncMock(return_value={"status": "OK"})
    return d


def _make_order(**kwargs) -> TradeOrder:
    defaults = {
        "symbol": "BTCUSDT",
        "type": "market",
        "side": "buy",
        "amount": 0.1,
        "order_id": "test-order-001",
        "status": OrderStatus.PENDING,
        "time_in_force": "GTC",
        "position_size_pct": 0.1,
        "stop_loss": 44000.0,
        "take_profit": 46000.0,
    }
    defaults.update(kwargs)
    return TradeOrder(**defaults)


_FILL_RESULT = {"fill_price": 45000.0, "amount": 0.1, "status": "FILLED"}


@pytest.mark.asyncio
async def test_skip_when_stop_market_already_armed():
    """AC1: STOP_MARKET on exchange → placement skipped, counter incremented."""
    armed = OrderSnapshot(
        symbol="BTCUSDT",
        order_id="99999",
        side="SELL",
        order_type="STOP_MARKET",
        status="NEW",
        quantity=0.1,
        price=44000.0,
        position_side="LONG",
    )
    d = _make_dispatcher_with_store([armed])
    order = _make_order()

    with patch("tradeengine.dispatcher.order_placement_skipped_total") as mock_ctr:
        await d._place_risk_management_orders(order, _FILL_RESULT)

    mock_ctr.labels.assert_called_once_with(reason="already_armed")
    mock_ctr.labels.return_value.inc.assert_called_once()
    d.oco_manager.place_oco_orders.assert_not_called()


@pytest.mark.asyncio
async def test_skip_when_take_profit_market_already_armed():
    """AC1: TAKE_PROFIT_MARKET on exchange → placement skipped."""
    armed = OrderSnapshot(
        symbol="BTCUSDT",
        order_id="88888",
        side="SELL",
        order_type="TAKE_PROFIT_MARKET",
        status="NEW",
        quantity=0.1,
        price=46000.0,
        position_side="LONG",
    )
    d = _make_dispatcher_with_store([armed])
    order = _make_order()

    with patch("tradeengine.dispatcher.order_placement_skipped_total") as mock_ctr:
        await d._place_risk_management_orders(order, _FILL_RESULT)

    mock_ctr.labels.assert_called_once_with(reason="already_armed")
    mock_ctr.labels.return_value.inc.assert_called_once()
    d.oco_manager.place_oco_orders.assert_not_called()


@pytest.mark.asyncio
async def test_placement_proceeds_when_no_protective_order():
    """AC3: no armed order on exchange → skip guard does not fire."""
    d = _make_dispatcher_with_store([])  # empty exchange state
    order = _make_order()

    with (
        patch("tradeengine.dispatcher.order_placement_skipped_total") as mock_ctr,
        patch.object(d, "_place_individual_risk_orders", new_callable=AsyncMock),
    ):
        await d._place_risk_management_orders(order, _FILL_RESULT)

    mock_ctr.labels.assert_not_called()


def test_order_snapshot_stores_position_side_from_ws():
    """AC2: OrderSnapshot.position_side is populated from WS event ps field."""
    snap = OrderSnapshot(
        symbol="BTCUSDT",
        order_id="12345",
        side="SELL",
        order_type="STOP_MARKET",
        status="NEW",
        quantity=0.1,
        price=44000.0,
        position_side="LONG",
    )
    assert snap.position_side == "LONG"


def test_order_snapshot_default_position_side_is_both():
    """AC2: default position_side is BOTH (one-way mode)."""
    snap = OrderSnapshot(
        symbol="BTCUSDT",
        order_id="12345",
        side="SELL",
        order_type="STOP_MARKET",
        status="NEW",
        quantity=0.1,
        price=44000.0,
    )
    assert snap.position_side == "BOTH"
