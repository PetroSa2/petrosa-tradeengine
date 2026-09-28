from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tradeengine.dispatcher import OCOManager
from tradeengine.strategy_position_manager import StrategyPositionManager


def test_algo_exit_uses_actual_order_id_for_fill_lookup():
    client = Mock()
    client._request_futures_api.return_value = {"actualOrderId": "9001"}
    client.futures_get_order.return_value = {
        "avgPrice": "95.0",
        "executedQty": "2.0",
    }
    dispatcher = OCOManager(SimpleNamespace(client=client), Mock())

    details = dispatcher._fetch_oco_exit_details(
        "BTCUSDT",
        "7001",
        {"tp_order_id": "7001", "tp_is_algo": True},
    )

    assert details["avgPrice"] == "95.0"
    client.futures_get_order.assert_called_once_with(
        symbol="BTCUSDT", orderId="9001"
    )


@pytest.mark.asyncio
async def test_unknown_exit_does_not_turn_into_zero_pnl():
    strategy_position_manager = StrategyPositionManager()
    position_id = "sp-658"
    strategy_position_manager.strategy_positions[position_id] = {
        "status": "open",
        "side": "LONG",
        "entry_price": 100.0,
        "entry_quantity": 2.0,
        "exchange_position_key": "BTCUSDT_LONG",
        "strategy_id": "test",
        "symbol": "BTCUSDT",
    }

    result = await strategy_position_manager.close_strategy_position(
        strategy_position_id=position_id,
        exit_price=None,
        exit_quantity=2.0,
        close_reason="stop_loss",
        pnl_unknown=True,
    )

    assert result["pnl_unknown"] is True
    assert result["realized_pnl"] is None
