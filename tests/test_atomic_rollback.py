import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

from contracts.order import OrderSide, OrderType, TradeOrder
from shared.constants import UTC
from tradeengine.dispatcher import Dispatcher


@pytest.fixture
def mock_exchange():
    exchange = Mock()
    exchange.execute = AsyncMock(
        return_value={
            "status": "filled",
            "order_id": "entry_order_123",
            "fill_price": 50000.0,
            "amount": 0.001,
        }
    )
    return exchange


@pytest.fixture
def dispatcher(mock_exchange):
    d = Dispatcher(exchange=mock_exchange)
    d.position_manager = Mock()
    d.position_manager.check_position_limits = AsyncMock(return_value=True)
    d.position_manager.check_daily_loss_limits = AsyncMock(return_value=True)
    d.position_manager.update_position = AsyncMock()
    d.position_manager.create_position_record = AsyncMock()
    return d


@pytest.mark.asyncio
async def test_oco_failure_causes_rollback(dispatcher):
    """
    Test that if _place_risk_management_orders fails,
    close_position_with_cleanup is called (Atomic Rollback).
    """
    order = TradeOrder(
        symbol="BTCUSDT",
        side="buy",
        type="market",
        amount=0.001,
        order_id="test_order_123",
        position_id="pos_123",
        position_side="LONG",
        simulate=False,
    )

    # Mock _place_risk_management_orders to raise an exception
    dispatcher._place_risk_management_orders = AsyncMock(
        side_effect=Exception("OCO Placement Failed")
    )

    # Mock close_position_with_cleanup
    dispatcher.close_position_with_cleanup = AsyncMock(
        return_value={"status": "success"}
    )

    # Mock strategy_position_manager
    with patch("tradeengine.dispatcher.strategy_position_manager") as mock_spm:
        mock_spm.create_strategy_position = AsyncMock(return_value="strat_pos_123")

        # Execute order with consensus
        result = await dispatcher._execute_order_with_consensus(order)

        # Verify rollback was called
        dispatcher.close_position_with_cleanup.assert_called_once()
        assert result["status"] == "rolled_back"


@pytest.mark.asyncio
async def test_oco_timeout_causes_rollback(dispatcher):
    """
    Test that if _place_risk_management_orders times out,
    close_position_with_cleanup is called.
    """
    order = TradeOrder(
        symbol="BTCUSDT",
        side="buy",
        type="market",
        amount=0.001,
        order_id="test_order_124",
        position_id="pos_124",
        position_side="LONG",
        simulate=False,
    )

    # Mock _place_risk_management_orders to timeout
    dispatcher._place_risk_management_orders = AsyncMock(side_effect=TimeoutError())

    # Mock close_position_with_cleanup
    dispatcher.close_position_with_cleanup = AsyncMock(
        return_value={"status": "success"}
    )

    # Mock strategy_position_manager
    with patch("tradeengine.dispatcher.strategy_position_manager") as mock_spm:
        mock_spm.create_strategy_position = AsyncMock(return_value="strat_pos_124")

        # Execute order with consensus
        result = await dispatcher._execute_order_with_consensus(order)

        # Verify rollback was called
        dispatcher.close_position_with_cleanup.assert_called_once()
        assert result["status"] == "rolled_back"


@pytest.mark.asyncio
async def test_rollback_failure_is_handled(dispatcher):
    """
    Test that if close_position_with_cleanup itself fails during rollback,
    the failure path is handled and surfaced correctly.
    """
    order = TradeOrder(
        symbol="BTCUSDT",
        side="buy",
        type="market",
        amount=0.001,
        order_id="test_order_125",
        position_id="pos_125",
        position_side="LONG",
        simulate=False,
    )

    # Force risk management order placement to fail, triggering rollback.
    dispatcher._place_risk_management_orders = AsyncMock(
        side_effect=Exception("OCO Placement Failed")
    )

    # Now simulate a failure in the rollback itself.
    dispatcher.close_position_with_cleanup = AsyncMock(
        side_effect=Exception("Rollback Failed")
    )

    # Mock strategy_position_manager
    with patch("tradeengine.dispatcher.strategy_position_manager") as mock_spm:
        mock_spm.create_strategy_position = AsyncMock(return_value="strat_pos_125")

        # Execute order with consensus
        result = await dispatcher._execute_order_with_consensus(order)

        # Verify that rollback was attempted despite ultimately failing.
        dispatcher.close_position_with_cleanup.assert_called_once()
        # The dispatcher should surface a rollback failure status when rollback cannot complete.
        assert result["status"] == "rollback_failed"
        assert "Rollback Failed" in result["rollback_error"]
