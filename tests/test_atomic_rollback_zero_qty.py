import asyncio
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from contracts.order import OrderSide, OrderType, TradeOrder
from tradeengine.dispatcher import Dispatcher


@pytest.fixture
def mock_exchange():
    """Mock exchange for testing"""
    exchange = AsyncMock()
    return exchange


@pytest.fixture
def dispatcher(mock_exchange):
    """Create dispatcher instance with mocked exchange"""
    d = Dispatcher(exchange=mock_exchange)
    d.logger = MagicMock()
    return d


@pytest.mark.asyncio
async def test_atomic_rollback_prioritizes_order_amount_when_result_amount_is_zero(
    dispatcher, mock_exchange
):
    """
    Test that atomic rollback uses order.amount when result.amount is 0.0 or missing.
    This verifies the fix for Ticket 277.
    """
    # 1. Setup the order
    order = TradeOrder(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        amount=1.5,
        target_price=50000.0,
        stop_loss=48000.0,
        take_profit=52000.0,
        position_id="test_pos_123",
        position_side="LONG",
    )

    # 2. Setup the result with 0.0 amount (the bug condition)
    result = {
        "status": "filled",
        "order_id": "entry_123",
        "amount": 0.0,  # This triggered the bug
    }

    # 3. Mock _place_risk_management_orders to fail, triggering rollback
    dispatcher._place_risk_management_orders = AsyncMock(
        side_effect=Exception("OCO placement failed")
    )

    # 4. Mock close_position_with_cleanup to capture the quantity passed to it
    dispatcher.close_position_with_cleanup = AsyncMock(
        return_value={"status": "success"}
    )

    # 5. Call the method that contains the rollback logic
    # We call _execute_order_with_consensus which contains the rollback logic
    # We need to mock some internal calls to get to that point
    with (
        patch.object(
            dispatcher.position_manager,
            "check_position_limits",
            AsyncMock(return_value=True),
        ),
        patch.object(
            dispatcher.position_manager,
            "check_daily_loss_limits",
            AsyncMock(return_value=True),
        ),
        patch.object(dispatcher, "execute_order", AsyncMock(return_value=result)),
        patch.object(dispatcher.position_manager, "update_position", AsyncMock()),
        patch.object(
            dispatcher.position_manager, "create_position_record", AsyncMock()
        ),
        patch.object(dispatcher, "order_to_signal", {order.order_id: Mock()}),
        patch("tradeengine.dispatcher.strategy_position_manager", Mock()),
    ):

        final_result = await dispatcher._execute_order_with_consensus(order)

    # 6. Verify that close_position_with_cleanup was called with order.amount (1.5) instead of 0.0
    dispatcher.close_position_with_cleanup.assert_called_once()
    args, kwargs = dispatcher.close_position_with_cleanup.call_args
    assert kwargs["quantity"] == 1.5
    assert final_result["status"] == "rolled_back"


@pytest.mark.asyncio
async def test_atomic_rollback_skips_when_total_quantity_is_zero(
    dispatcher, mock_exchange
):
    """
    Test that atomic rollback is skipped if both result.amount and order.amount are 0 or invalid.
    """
    # 1. Setup the order with 0 amount (unlikely but safe to test)
    order = TradeOrder(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        amount=0.0,
        position_id="test_pos_123",
        position_side="LONG",
    )

    # 2. Setup the result with 0.0 amount
    result = {
        "status": "filled",
        "amount": 0.0,
    }

    # 3. Mock failure to trigger rollback
    dispatcher._place_risk_management_orders = AsyncMock(
        side_effect=Exception("OCO failure")
    )
    dispatcher.close_position_with_cleanup = AsyncMock()

    # 4. Execute
    with (
        patch.object(
            dispatcher.position_manager,
            "check_position_limits",
            AsyncMock(return_value=True),
        ),
        patch.object(
            dispatcher.position_manager,
            "check_daily_loss_limits",
            AsyncMock(return_value=True),
        ),
        patch.object(dispatcher, "execute_order", AsyncMock(return_value=result)),
        patch.object(dispatcher.position_manager, "update_position", AsyncMock()),
        patch.object(
            dispatcher.position_manager, "create_position_record", AsyncMock()
        ),
        patch.object(dispatcher, "order_to_signal", {order.order_id: Mock()}),
        patch("tradeengine.dispatcher.strategy_position_manager", Mock()),
    ):

        final_result = await dispatcher._execute_order_with_consensus(order)

    # 5. Verify rollback was skipped
    dispatcher.close_position_with_cleanup.assert_not_called()
    assert final_result["status"] == "rolled_back_skipped"
    assert "Risk management failure" in final_result["error"]
    assert final_result["rollback_skipped_reason"] == "non_positive_filled_qty: 0.0"
    dispatcher.logger.warning.assert_any_call(
        "⚠️ Skipping atomic rollback for BTCUSDT: calculated filled_qty is 0.0"
    )
