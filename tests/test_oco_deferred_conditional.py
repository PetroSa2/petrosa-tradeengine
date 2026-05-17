"""Tests for deferred OCO placement on CONDITIONAL entry orders (#371).

CONDITIONAL_LIMIT/CONDITIONAL_STOP entries return status=NEW with no real fill
until they trigger. Placing OCO immediately against the stale target_price has
caused Binance -2021 'immediate trigger' rejections. The fix defers OCO
placement until the entry order transitions to FILLED, then triggers it with
the real avgPrice captured from the exchange.
"""

import logging
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from contracts.order import OrderType, TradeOrder
from tradeengine.dispatcher import OCOManager


def _make_conditional_order(
    symbol: str = "BTCUSDT", side: str = "buy", amount: float = 0.001
) -> TradeOrder:
    return TradeOrder(
        symbol=symbol,
        type=OrderType.CONDITIONAL_STOP,
        side=side,
        amount=amount,
        target_price=50000.0,
        stop_loss=48000.0,
        take_profit=52000.0,
        order_id="entry-cond-1",
        position_id="pos-1",
        position_side="LONG" if side == "buy" else "SHORT",
        exchange="binance",
    )


def _make_market_order(symbol: str = "BTCUSDT") -> TradeOrder:
    return TradeOrder(
        symbol=symbol,
        type=OrderType.MARKET,
        side="buy",
        amount=0.001,
        target_price=50000.0,
        stop_loss=48000.0,
        take_profit=52000.0,
        order_id="entry-mkt-1",
        position_id="pos-2",
        position_side="LONG",
        exchange="binance",
    )


@pytest.fixture
def logger() -> logging.Logger:
    return logging.getLogger("test_oco_deferred")


@pytest.fixture
def mock_exchange() -> Mock:
    ex = Mock()
    ex.get_order_status = AsyncMock()
    return ex


@pytest.fixture
def mock_dispatcher() -> Mock:
    d = Mock()
    d._place_risk_management_orders = AsyncMock(return_value=None)
    return d


@pytest.fixture
def oco_manager(
    mock_exchange: Mock, mock_dispatcher: Mock, logger: logging.Logger
) -> OCOManager:
    return OCOManager(exchange=mock_exchange, logger=logger, dispatcher=mock_dispatcher)


def test_is_conditional_pending_entry_true_for_conditional_new(
    oco_manager: OCOManager,
) -> None:
    order = _make_conditional_order()
    result: dict[str, Any] = {"status": "NEW", "order_id": "x", "fill_price": 0}
    assert oco_manager._is_conditional_pending_entry(order, result) is True


def test_is_conditional_pending_entry_false_for_market_new(
    oco_manager: OCOManager,
) -> None:
    """MARKET orders with NEW must still get immediate OCO — they fill instantly."""
    order = _make_market_order()
    result: dict[str, Any] = {"status": "NEW", "order_id": "x"}
    assert oco_manager._is_conditional_pending_entry(order, result) is False


def test_is_conditional_pending_entry_false_for_conditional_filled(
    oco_manager: OCOManager,
) -> None:
    """A CONDITIONAL order that's already FILLED skips the defer path."""
    order = _make_conditional_order()
    result: dict[str, Any] = {"status": "filled", "fill_price": 50100.0}
    assert oco_manager._is_conditional_pending_entry(order, result) is False


def test_is_conditional_pending_entry_handles_none_result(
    oco_manager: OCOManager,
) -> None:
    order = _make_conditional_order()
    assert oco_manager._is_conditional_pending_entry(order, None) is False


@pytest.mark.asyncio
async def test_defer_oco_until_filled_registers_pending_and_starts_monitoring(
    oco_manager: OCOManager,
) -> None:
    order = _make_conditional_order()
    assert oco_manager.pending_entries == {}
    assert oco_manager.monitoring_active is False

    await oco_manager.defer_oco_until_filled(order, entry_order_id="entry-cond-1")

    assert "entry-cond-1" in oco_manager.pending_entries
    entry = oco_manager.pending_entries["entry-cond-1"]
    assert entry["order"] is order
    assert entry["symbol"] == "BTCUSDT"
    assert entry["entry_order_id"] == "entry-cond-1"
    assert oco_manager.monitoring_active is True

    await oco_manager.stop_monitoring()


@pytest.mark.asyncio
async def test_defer_oco_until_filled_skips_when_entry_id_missing(
    oco_manager: OCOManager,
) -> None:
    order = _make_conditional_order()
    await oco_manager.defer_oco_until_filled(order, entry_order_id="")
    assert oco_manager.pending_entries == {}
    assert oco_manager.monitoring_active is False


@pytest.mark.asyncio
async def test_check_pending_entries_no_op_when_entry_still_new(
    oco_manager: OCOManager, mock_exchange: Mock, mock_dispatcher: Mock
) -> None:
    order = _make_conditional_order()
    oco_manager.pending_entries["entry-cond-1"] = {
        "order": order,
        "symbol": order.symbol,
        "entry_order_id": "entry-cond-1",
        "registered_at": 0.0,
    }
    mock_exchange.get_order_status.return_value = {
        "order_id": "entry-cond-1",
        "status": "NEW",
        "executed_qty": 0,
        "cummulative_quote_qty": 0,
    }

    await oco_manager._check_pending_entries()

    assert "entry-cond-1" in oco_manager.pending_entries
    mock_dispatcher._place_risk_management_orders.assert_not_called()


@pytest.mark.asyncio
async def test_check_pending_entries_triggers_oco_on_filled_with_real_avg_price(
    oco_manager: OCOManager, mock_exchange: Mock, mock_dispatcher: Mock
) -> None:
    order = _make_conditional_order()
    oco_manager.pending_entries["entry-cond-1"] = {
        "order": order,
        "symbol": order.symbol,
        "entry_order_id": "entry-cond-1",
        "registered_at": 0.0,
    }
    # executed_qty=0.002, cum_quote=100.4 → avg_price = 50200.0 (real fill, not target_price)
    mock_exchange.get_order_status.return_value = {
        "order_id": "entry-cond-1",
        "status": "FILLED",
        "executed_qty": 0.002,
        "cummulative_quote_qty": 100.4,
        "price": 0,
    }

    await oco_manager._check_pending_entries()

    assert "entry-cond-1" not in oco_manager.pending_entries
    mock_dispatcher._place_risk_management_orders.assert_awaited_once()
    call_args = mock_dispatcher._place_risk_management_orders.await_args
    passed_order, synthetic_result = call_args.args
    assert passed_order is order
    assert synthetic_result["status"] == "filled"
    assert synthetic_result["fill_price"] == pytest.approx(50200.0)
    assert synthetic_result["amount"] == pytest.approx(0.002)
    assert synthetic_result["order_id"] == "entry-cond-1"


@pytest.mark.asyncio
async def test_check_pending_entries_drops_entry_when_avg_price_unresolvable(
    oco_manager: OCOManager, mock_exchange: Mock, mock_dispatcher: Mock
) -> None:
    """If the exchange reports FILLED but no usable price data, drop the entry
    rather than spin forever or place OCO against price=0."""
    order = _make_conditional_order()
    oco_manager.pending_entries["entry-cond-1"] = {
        "order": order,
        "symbol": order.symbol,
        "entry_order_id": "entry-cond-1",
        "registered_at": 0.0,
    }
    mock_exchange.get_order_status.return_value = {
        "order_id": "entry-cond-1",
        "status": "FILLED",
        "executed_qty": 0,
        "cummulative_quote_qty": 0,
        "price": 0,
    }

    await oco_manager._check_pending_entries()

    assert "entry-cond-1" not in oco_manager.pending_entries
    mock_dispatcher._place_risk_management_orders.assert_not_called()


@pytest.mark.asyncio
async def test_check_pending_entries_falls_back_to_price_when_cum_quote_zero(
    oco_manager: OCOManager, mock_exchange: Mock, mock_dispatcher: Mock
) -> None:
    """Some exchange replies omit cummulative_quote_qty but report price. Use it."""
    order = _make_conditional_order()
    oco_manager.pending_entries["entry-cond-1"] = {
        "order": order,
        "symbol": order.symbol,
        "entry_order_id": "entry-cond-1",
        "registered_at": 0.0,
    }
    mock_exchange.get_order_status.return_value = {
        "order_id": "entry-cond-1",
        "status": "filled",
        "executed_qty": 0.001,
        "cummulative_quote_qty": 0,
        "price": "49850.5",
    }

    await oco_manager._check_pending_entries()

    mock_dispatcher._place_risk_management_orders.assert_awaited_once()
    _, synthetic_result = mock_dispatcher._place_risk_management_orders.await_args.args
    assert synthetic_result["fill_price"] == pytest.approx(49850.5)


@pytest.mark.asyncio
async def test_check_pending_entries_keeps_entry_when_status_lookup_fails(
    oco_manager: OCOManager, mock_exchange: Mock, mock_dispatcher: Mock
) -> None:
    """Transient exchange errors must not silently drop a pending entry."""
    order = _make_conditional_order()
    oco_manager.pending_entries["entry-cond-1"] = {
        "order": order,
        "symbol": order.symbol,
        "entry_order_id": "entry-cond-1",
        "registered_at": 0.0,
    }
    mock_exchange.get_order_status.side_effect = RuntimeError("network blip")

    await oco_manager._check_pending_entries()

    assert "entry-cond-1" in oco_manager.pending_entries
    mock_dispatcher._place_risk_management_orders.assert_not_called()
