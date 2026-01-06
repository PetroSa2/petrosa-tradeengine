"""
Comprehensive tests for stop-loss and take-profit order handling.

Tests cover:
- Stop-loss order placement
- Take-profit order placement
- Individual SL/TP orders (non-OCO)
- Order tracking and position updates
- Error handling and edge cases
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contracts.order import OrderType, TradeOrder
from contracts.signal import Signal
from tradeengine.dispatcher import Dispatcher


@pytest.fixture
def mock_exchange():
    """Create a mock exchange for testing"""
    exchange = MagicMock()
    exchange.place_order = AsyncMock()
    exchange.cancel_order = AsyncMock()
    exchange.get_order_status = AsyncMock()
    return exchange


@pytest.fixture
def dispatcher_with_mocks(mock_exchange):
    """Create a dispatcher with mocked dependencies"""
    dispatcher = Dispatcher(exchange=mock_exchange)

    # Mock position manager
    dispatcher.position_manager = MagicMock()
    dispatcher.position_manager.update_position_risk_orders = AsyncMock()

    # Mock order manager
    dispatcher.order_manager = MagicMock()
    dispatcher.order_manager.track_order = AsyncMock()

    return dispatcher


@pytest.fixture
def sample_filled_order():
    """Create a sample filled order"""
    return TradeOrder(
        position_id="test-pos-123",
        symbol="BTCUSDT",
        side="buy",
        type="market",
        amount=0.001,
        target_price=50000.0,
        position_side="LONG",
        exchange="binance",
        stop_loss=48000.0,
        take_profit=52000.0,
        strategy_metadata={"strategy_id": "test_strategy"},
    )


@pytest.fixture
def sample_fill_result():
    """Create a sample order fill result"""
    return {
        "status": "filled",
        "order_id": "main-order-123",
        "fill_price": 50000.0,
        "amount": 0.001,
        "commission": 0.05,
    }


# ============================================================================
# Stop-Loss Order Placement Tests
# ============================================================================


@pytest.mark.asyncio
async def test_place_stop_loss_order_long_position(
    dispatcher_with_mocks, sample_filled_order, sample_fill_result
):
    """Test placing stop-loss order for LONG position"""
    dispatcher, mock_exchange = dispatcher_with_mocks

    # Mock successful SL order placement
    mock_exchange.place_order.return_value = {
        "status": "pending",
        "order_id": "sl-order-456",
        "stop_price": 48000.0,
    }

    await dispatcher._place_stop_loss_order(sample_filled_order, sample_fill_result)

    # Verify order was placed
    assert mock_exchange.place_order.called

    # Verify order parameters
    call_args = mock_exchange.place_order.call_args[0][0]
    assert call_args.symbol == "BTCUSDT"
    assert call_args.side == "sell"  # SL for LONG is SELL
    assert call_args.type == "stop_market" or call_args.type == "STOP_MARKET"
    assert call_args.stop_price == 48000.0
    assert call_args.amount == 0.001


@pytest.mark.asyncio
async def test_place_stop_loss_order_short_position(dispatcher_with_mocks):
    """Test placing stop-loss order for SHORT position"""
    dispatcher, mock_exchange = dispatcher_with_mocks

    short_order = TradeOrder(
        position_id="test-short-pos",
        symbol="BTCUSDT",
        side="sell",
        type="market",
        amount=0.002,
        target_price=50000.0,
        position_side="SHORT",
        exchange="binance",
        stop_loss=52000.0,  # SL for SHORT is above entry
    )

    fill_result = {
        "status": "filled",
        "fill_price": 50000.0,
        "amount": 0.002,
    }

    mock_exchange.place_order.return_value = {
        "status": "pending",
        "order_id": "sl-order-789",
        "stop_price": 52000.0,
    }

    await dispatcher._place_stop_loss_order(short_order, fill_result)

    # Verify order was placed
    assert mock_exchange.place_order.called

    # For SHORT, SL is a BUY order
    call_args = mock_exchange.place_order.call_args[0][0]
    assert call_args.side == "buy"  # SL for SHORT is BUY
    assert call_args.stop_price == 52000.0


@pytest.mark.asyncio
async def test_place_stop_loss_order_tracks_in_order_manager(
    dispatcher_with_mocks, sample_filled_order, sample_fill_result
):
    """Test that stop-loss order is tracked in order manager"""
    dispatcher, mock_exchange = dispatcher_with_mocks

    mock_exchange.place_order.return_value = {
        "status": "pending",
        "order_id": "sl-order-456",
        "stop_price": 48000.0,
    }

    await dispatcher._place_stop_loss_order(sample_filled_order, sample_fill_result)

    # Verify order was tracked
    assert dispatcher.order_manager.track_order.called


@pytest.mark.asyncio
async def test_place_stop_loss_order_updates_position(
    dispatcher_with_mocks, sample_filled_order, sample_fill_result
):
    """Test that stop-loss order ID is updated in position"""
    dispatcher, mock_exchange = dispatcher_with_mocks

    mock_exchange.place_order.return_value = {
        "status": "pending",
        "order_id": "sl-order-456",
        "stop_price": 48000.0,
    }

    await dispatcher._place_stop_loss_order(sample_filled_order, sample_fill_result)

    # Verify position was updated with SL order ID
    assert dispatcher.position_manager.update_position_risk_orders.called
    call_args = dispatcher.position_manager.update_position_risk_orders.call_args
    assert call_args[0][0] == "test-pos-123"  # position_id
    assert call_args[1]["stop_loss_order_id"] == "sl-order-456"


@pytest.mark.asyncio
async def test_place_stop_loss_order_handles_failure(
    dispatcher_with_mocks, sample_filled_order, sample_fill_result
):
    """Test handling of stop-loss order placement failure"""
    dispatcher, mock_exchange = dispatcher_with_mocks

    # Mock order placement failure
    mock_exchange.place_order.return_value = {
        "status": "rejected",
        "error": "Insufficient balance",
    }

    # Should not raise exception, just log error
    await dispatcher._place_stop_loss_order(sample_filled_order, sample_fill_result)

    # Verify order was attempted
    assert mock_exchange.place_order.called


# ============================================================================
# Take-Profit Order Placement Tests
# ============================================================================


@pytest.mark.asyncio
async def test_place_take_profit_order_long_position(
    dispatcher_with_mocks, sample_filled_order, sample_fill_result
):
    """Test placing take-profit order for LONG position"""
    dispatcher, mock_exchange = dispatcher_with_mocks

    # Mock successful TP order placement
    mock_exchange.place_order.return_value = {
        "status": "pending",
        "order_id": "tp-order-789",
        "price": 52000.0,
    }

    await dispatcher._place_take_profit_order(sample_filled_order, sample_fill_result)

    # Verify order was placed
    assert mock_exchange.place_order.called

    # Verify order parameters
    call_args = mock_exchange.place_order.call_args[0][0]
    assert call_args.symbol == "BTCUSDT"
    assert call_args.side == "sell"  # TP for LONG is SELL
    assert call_args.type == "limit" or call_args.type == "LIMIT"
    assert call_args.target_price == 52000.0
    assert call_args.amount == 0.001


@pytest.mark.asyncio
async def test_place_take_profit_order_short_position(dispatcher_with_mocks):
    """Test placing take-profit order for SHORT position"""
    dispatcher, mock_exchange = dispatcher_with_mocks

    short_order = TradeOrder(
        position_id="test-short-pos",
        symbol="BTCUSDT",
        side="sell",
        type="market",
        amount=0.002,
        target_price=50000.0,
        position_side="SHORT",
        exchange="binance",
        take_profit=48000.0,  # TP for SHORT is below entry
    )

    fill_result = {
        "status": "filled",
        "fill_price": 50000.0,
        "amount": 0.002,
    }

    mock_exchange.place_order.return_value = {
        "status": "pending",
        "order_id": "tp-order-101",
        "price": 48000.0,
    }

    await dispatcher._place_take_profit_order(short_order, fill_result)

    # For SHORT, TP is a BUY order
    call_args = mock_exchange.place_order.call_args[0][0]
    assert call_args.side == "buy"  # TP for SHORT is BUY
    assert call_args.target_price == 48000.0


@pytest.mark.asyncio
async def test_place_take_profit_order_tracks_in_order_manager(
    dispatcher_with_mocks, sample_filled_order, sample_fill_result
):
    """Test that take-profit order is tracked in order manager"""
    dispatcher, mock_exchange = dispatcher_with_mocks

    mock_exchange.place_order.return_value = {
        "status": "pending",
        "order_id": "tp-order-789",
        "price": 52000.0,
    }

    await dispatcher._place_take_profit_order(sample_filled_order, sample_fill_result)

    # Verify order was tracked
    assert dispatcher.order_manager.track_order.called


@pytest.mark.asyncio
async def test_place_take_profit_order_updates_position(
    dispatcher_with_mocks, sample_filled_order, sample_fill_result
):
    """Test that take-profit order ID is updated in position"""
    dispatcher, mock_exchange = dispatcher_with_mocks

    mock_exchange.place_order.return_value = {
        "status": "pending",
        "order_id": "tp-order-789",
        "price": 52000.0,
    }

    await dispatcher._place_take_profit_order(sample_filled_order, sample_fill_result)

    # Verify position was updated with TP order ID
    assert dispatcher.position_manager.update_position_risk_orders.called
    call_args = dispatcher.position_manager.update_position_risk_orders.call_args
    assert call_args[0][0] == "test-pos-123"  # position_id
    assert call_args[1]["take_profit_order_id"] == "tp-order-789"


# ============================================================================
# Individual Risk Orders (Non-OCO) Tests
# ============================================================================


@pytest.mark.asyncio
async def test_place_individual_risk_orders_stop_loss_only(dispatcher_with_mocks):
    """Test placing individual stop-loss order (no TP)"""
    dispatcher, mock_exchange = dispatcher_with_mocks

    order_with_sl_only = TradeOrder(
        position_id="test-pos-sl-only",
        symbol="BTCUSDT",
        side="buy",
        type="market",
        amount=0.001,
        target_price=50000.0,
        position_side="LONG",
        exchange="binance",
        stop_loss=48000.0,
        take_profit=None,  # No TP
    )

    fill_result = {
        "status": "filled",
        "fill_price": 50000.0,
        "amount": 0.001,
    }

    mock_exchange.place_order.return_value = {
        "status": "pending",
        "order_id": "sl-order-individual",
    }

    await dispatcher._place_individual_risk_orders(order_with_sl_only, fill_result)

    # Verify only SL order was placed (not TP)
    assert mock_exchange.place_order.call_count == 1
    call_args = mock_exchange.place_order.call_args[0][0]
    assert call_args.stop_price == 48000.0


@pytest.mark.asyncio
async def test_place_individual_risk_orders_take_profit_only(dispatcher_with_mocks):
    """Test placing individual take-profit order (no SL)"""
    dispatcher, mock_exchange = dispatcher_with_mocks

    order_with_tp_only = TradeOrder(
        position_id="test-pos-tp-only",
        symbol="BTCUSDT",
        side="buy",
        type="market",
        amount=0.001,
        target_price=50000.0,
        position_side="LONG",
        exchange="binance",
        stop_loss=None,  # No SL
        take_profit=52000.0,
    )

    fill_result = {
        "status": "filled",
        "fill_price": 50000.0,
        "amount": 0.001,
    }

    mock_exchange.place_order.return_value = {
        "status": "pending",
        "order_id": "tp-order-individual",
    }

    await dispatcher._place_individual_risk_orders(order_with_tp_only, fill_result)

    # Verify only TP order was placed (not SL)
    assert mock_exchange.place_order.call_count == 1
    call_args = mock_exchange.place_order.call_args[0][0]
    assert call_args.target_price == 52000.0


@pytest.mark.asyncio
async def test_place_individual_risk_orders_both(
    dispatcher_with_mocks, sample_filled_order, sample_fill_result
):
    """Test placing both SL and TP as individual orders"""
    dispatcher, mock_exchange = dispatcher_with_mocks

    mock_exchange.place_order.return_value = {
        "status": "pending",
        "order_id": "risk-order-123",
    }

    await dispatcher._place_individual_risk_orders(
        sample_filled_order, sample_fill_result
    )

    # Verify both orders were placed
    assert mock_exchange.place_order.call_count == 2

    # First call should be SL
    first_call = mock_exchange.place_order.call_args_list[0][0][0]
    assert first_call.stop_price == 48000.0

    # Second call should be TP
    second_call = mock_exchange.place_order.call_args_list[1][0][0]
    assert second_call.target_price == 52000.0


# ============================================================================
# Edge Cases and Error Handling
# ============================================================================


@pytest.mark.asyncio
async def test_place_stop_loss_with_zero_amount(
    dispatcher_with_mocks, sample_filled_order
):
    """Test stop-loss placement when fill result has zero amount"""
    dispatcher, mock_exchange = dispatcher_with_mocks

    fill_result_zero = {
        "status": "filled",
        "fill_price": 50000.0,
        "amount": 0.0,  # Zero amount
    }

    mock_exchange.place_order.return_value = {
        "status": "pending",
        "order_id": "sl-order-zero",
    }

    # Should use order.amount as fallback
    await dispatcher._place_stop_loss_order(sample_filled_order, fill_result_zero)

    assert mock_exchange.place_order.called
    call_args = mock_exchange.place_order.call_args[0][0]
    assert call_args.amount == 0.001  # Uses order.amount


@pytest.mark.asyncio
async def test_place_take_profit_with_string_amount(
    dispatcher_with_mocks, sample_filled_order
):
    """Test take-profit placement when fill result has string amount"""
    dispatcher, mock_exchange = dispatcher_with_mocks

    fill_result_string = {
        "status": "filled",
        "fill_price": 50000.0,
        "amount": "0.001",  # String amount
    }

    mock_exchange.place_order.return_value = {
        "status": "pending",
        "order_id": "tp-order-string",
    }

    await dispatcher._place_take_profit_order(sample_filled_order, fill_result_string)

    assert mock_exchange.place_order.called
    call_args = mock_exchange.place_order.call_args[0][0]
    assert call_args.amount == 0.001  # Should be converted to float


@pytest.mark.asyncio
async def test_place_stop_loss_handles_exception(
    dispatcher_with_mocks, sample_filled_order, sample_fill_result
):
    """Test that exceptions during SL placement are handled gracefully"""
    dispatcher, mock_exchange = dispatcher_with_mocks

    # Mock exception during order placement
    mock_exchange.place_order.side_effect = Exception("Exchange error")

    # Should not raise exception, just log error
    await dispatcher._place_stop_loss_order(sample_filled_order, sample_fill_result)

    # Verify attempt was made
    assert mock_exchange.place_order.called


@pytest.mark.asyncio
async def test_place_take_profit_handles_exception(
    dispatcher_with_mocks, sample_filled_order, sample_fill_result
):
    """Test that exceptions during TP placement are handled gracefully"""
    dispatcher, mock_exchange = dispatcher_with_mocks

    # Mock exception during order placement
    mock_exchange.place_order.side_effect = Exception("Exchange error")

    # Should not raise exception, just log error
    await dispatcher._place_take_profit_order(sample_filled_order, sample_fill_result)

    # Verify attempt was made
    assert mock_exchange.place_order.called
