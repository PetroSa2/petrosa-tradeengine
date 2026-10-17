from unittest.mock import AsyncMock, Mock, patch

import pytest

from contracts.order import OrderStatus, TradeOrder
from tradeengine.dispatcher import Dispatcher, OCOManager
from tradeengine.position_health_guard import check_position_stops
from tradeengine.strategy_position_manager import StrategyPositionManager


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sl_result", "expected_stop"),
    [
        ({"order_id": "1398104567890123", "fill_price": 80.0}, 80.0),
        ({"order_id": "1398104567890123"}, 80.0),
    ],
)
async def test_oco_record_falls_back_to_requested_stop_price(sl_result, expected_stop):
    exchange = Mock()
    exchange.execute = AsyncMock(
        side_effect=[sl_result, {"order_id": "1398104567890124"}]
    )
    manager = OCOManager(exchange=exchange, logger=Mock())

    result = await manager.place_oco_orders(
        position_id="position-509",
        symbol="BTCUSDT",
        position_side="LONG",
        quantity=0.01,
        stop_loss_price=80.0,
        take_profit_price=105.0,
        entry_price=100.0,
    )

    assert result["status"] == "success"
    assert result["stop_loss_price"] == expected_stop


@pytest.mark.asyncio
async def test_oco_record_keeps_effective_adjusted_stop_price():
    exchange = Mock()
    exchange.execute = AsyncMock(
        side_effect=[
            {
                "order_id": "1398104567890123",
                "fill_price": 95.0,
                "is_algo_order": True,
            },
            {
                "order_id": "1398104567890124",
                "fill_price": 105.0,
                "is_algo_order": True,
            },
        ]
    )
    manager = OCOManager(exchange=exchange, logger=Mock())

    result = await manager.place_oco_orders(
        position_id="position-509",
        symbol="BTCUSDT",
        position_side="LONG",
        quantity=0.01,
        stop_loss_price=80.0,
        take_profit_price=105.0,
        strategy_position_id="strategy-509",
        entry_price=100.0,
    )

    assert result["stop_loss_price"] == 95.0
    oco = manager.active_oco_pairs["BTCUSDT_LONG"][0]
    assert oco["stop_loss_price"] == 95.0
    assert oco["take_profit_price"] == 105.0


@pytest.mark.asyncio
async def test_strategy_position_persists_armed_prices():
    manager = StrategyPositionManager()
    manager.strategy_positions["strategy-509"] = {
        "strategy_position_id": "strategy-509",
        "sl_order_id": None,
        "tp_order_id": None,
        "stop_loss_price": 80.0,
        "take_profit_price": 105.0,
        "status": "open",
    }

    with patch("tradeengine.strategy_position_manager.position_client") as client:
        client.update_strategy_position = AsyncMock(return_value=Mock(ok=True))
        await manager.set_strategy_position_orders(
            strategy_position_id="strategy-509",
            sl_order_id="1398104567890123",
            tp_order_id="1398104567890124",
            stop_loss_price=95.0,
            take_profit_price=105.0,
        )

    position = manager.strategy_positions["strategy-509"]
    assert position["stop_loss_price"] == 95.0
    assert position["take_profit_price"] == 105.0
    client.update_strategy_position.assert_awaited_once_with(
        "strategy-509",
        {
            "sl_order_id": "1398104567890123",
            "tp_order_id": "1398104567890124",
            "stop_loss_price": 95.0,
            "take_profit_price": 105.0,
        },
    )


@pytest.mark.asyncio
async def test_dispatcher_passes_armed_prices_to_strategy_position():
    dispatcher = Dispatcher(exchange=Mock())
    dispatcher.position_manager.update_position_risk_orders = AsyncMock()
    dispatcher.protective_leg_manager.request_sync = Mock()
    dispatcher.order_to_strategy_position["order-509"] = "strategy-509"
    dispatcher.oco_manager.place_oco_orders = AsyncMock(
        return_value={
            "status": "success",
            "sl_order_id": "1398104567890123",
            "tp_order_id": "1398104567890124",
            "stop_loss_price": 95.0,
            "take_profit_price": 105.0,
        }
    )
    order = TradeOrder(
        order_id="order-509",
        position_id="position-509",
        symbol="BTCUSDT",
        side="buy",
        type="market",
        amount=0.01,
        stop_loss=80.0,
        take_profit=105.0,
        position_side="LONG",
        status=OrderStatus.PENDING,
    )

    with patch("tradeengine.dispatcher.strategy_position_manager") as manager:
        manager.set_strategy_position_orders = AsyncMock()
        await dispatcher._place_risk_management_orders(
            order,
            {"status": "filled", "fill_price": 100.0, "amount": 0.01},
        )

    manager.set_strategy_position_orders.assert_awaited_once_with(
        strategy_position_id="strategy-509",
        sl_order_id="1398104567890123",
        tp_order_id="1398104567890124",
        stop_loss_price=95.0,
        take_profit_price=105.0,
    )


@pytest.mark.asyncio
async def test_stops_health_accepts_position_with_armed_adjusted_price():
    position = {
        "strategy_position_id": "strategy-509",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "sl_order_id": "1398104567890123",
        "tp_order_id": "1398104567890124",
        "stop_loss_price": 95.0,
        "take_profit_price": 105.0,
        "entry_quantity": 0.01,
        "entry_price": 100.0,
        "strategy_id": "strategy",
        "status": "open",
    }
    strategy_positions = Mock()
    strategy_positions.get_all_open_strategy_positions.return_value = [position]
    exchange = Mock()
    exchange.get_all_open_orders = AsyncMock(
        return_value={"1398104567890123", "1398104567890124"}
    )
    publisher = Mock()
    publisher.publish = AsyncMock(return_value=True)

    response = await check_position_stops(
        strategy_positions,
        Mock(get_open_positions=AsyncMock(return_value=[])),
        exchange,
        publisher,
    )

    assert response.healthy_count == 1
    assert response.violation_count == 0
    assert response.positions[0].status == "healthy"
