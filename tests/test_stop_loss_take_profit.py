"""
Comprehensive tests for stop-loss and take-profit order handling.

Tests cover:
- Stop-loss order placement
- Take-profit order placement
- Individual SL/TP orders (non-OCO)
- Order tracking and position updates
- Error handling and edge cases
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contracts.order import OrderType, TradeOrder
from contracts.signal import Signal
from shared.constants import UTC
from tradeengine.dispatcher import Dispatcher


@pytest.fixture
def mock_exchange():
    """Create a mock exchange for testing"""
    exchange = MagicMock()
    exchange.execute = AsyncMock()  # Dispatcher uses execute(), not place_order()
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

    # Return tuple for unpacking in tests
    return dispatcher, mock_exchange


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
    mock_exchange.execute.return_value = {
        "status": "pending",
        "order_id": "sl-order-456",
        "fill_price": 48000.0,
    }

    await dispatcher._place_stop_loss_order(sample_filled_order, sample_fill_result)

    # Verify order was placed
    assert mock_exchange.execute.called

    # Verify order parameters
    call_args = mock_exchange.execute.call_args[0][0]
    assert call_args.symbol == "BTCUSDT"
    assert call_args.side == "sell"  # SL for LONG is SELL
    assert call_args.type == OrderType.STOP or call_args.type == "stop"
    assert call_args.stop_loss == 48000.0
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

    mock_exchange.execute.return_value = {
        "status": "pending",
        "order_id": "sl-order-789",
        "stop_price": 52000.0,
    }

    await dispatcher._place_stop_loss_order(short_order, fill_result)

    # Verify order was placed
    assert mock_exchange.execute.called

    # For SHORT, SL is a BUY order
    call_args = mock_exchange.execute.call_args[0][0]
    assert call_args.side == "buy"  # SL for SHORT is BUY
    assert call_args.stop_loss == 52000.0


@pytest.mark.asyncio
async def test_place_stop_loss_order_tracks_in_order_manager(
    dispatcher_with_mocks, sample_filled_order, sample_fill_result
):
    """Test that stop-loss order is tracked in order manager"""
    dispatcher, mock_exchange = dispatcher_with_mocks

    mock_exchange.execute.return_value = {
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

    mock_exchange.execute.return_value = {
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
    mock_exchange.execute.return_value = {
        "status": "rejected",
        "error": "Insufficient balance",
    }

    # Should raise exception to trigger atomic rollback
    with pytest.raises(Exception, match="STOP LOSS FAILED AFTER ALL RETRIES"):
        await dispatcher._place_stop_loss_order(sample_filled_order, sample_fill_result)

    # Verify order was attempted
    assert mock_exchange.execute.called


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
    mock_exchange.execute.return_value = {
        "status": "pending",
        "order_id": "tp-order-789",
        "price": 52000.0,
    }

    await dispatcher._place_take_profit_order(sample_filled_order, sample_fill_result)

    # Verify order was placed
    assert mock_exchange.execute.called

    # Verify order parameters
    call_args = mock_exchange.execute.call_args[0][0]
    assert call_args.symbol == "BTCUSDT"
    assert call_args.side == "sell"  # TP for LONG is SELL
    assert call_args.type == OrderType.TAKE_PROFIT or call_args.type == "take_profit"
    assert call_args.take_profit == 52000.0
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

    mock_exchange.execute.return_value = {
        "status": "pending",
        "order_id": "tp-order-101",
        "price": 48000.0,
    }

    await dispatcher._place_take_profit_order(short_order, fill_result)

    # For SHORT, TP is a BUY order
    call_args = mock_exchange.execute.call_args[0][0]
    assert call_args.side == "buy"  # TP for SHORT is BUY
    assert call_args.take_profit == 48000.0


@pytest.mark.asyncio
async def test_place_take_profit_order_tracks_in_order_manager(
    dispatcher_with_mocks, sample_filled_order, sample_fill_result
):
    """Test that take-profit order is tracked in order manager"""
    dispatcher, mock_exchange = dispatcher_with_mocks

    mock_exchange.execute.return_value = {
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

    mock_exchange.execute.return_value = {
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

    mock_exchange.execute.return_value = {
        "status": "pending",
        "order_id": "sl-order-individual",
    }

    await dispatcher._place_individual_risk_orders(order_with_sl_only, fill_result)

    # Verify only SL order was placed (not TP)
    assert mock_exchange.execute.call_count == 1
    call_args = mock_exchange.execute.call_args[0][0]
    assert call_args.stop_loss == 48000.0


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

    mock_exchange.execute.return_value = {
        "status": "pending",
        "order_id": "tp-order-individual",
    }

    await dispatcher._place_individual_risk_orders(order_with_tp_only, fill_result)

    # Verify only TP order was placed (not SL)
    assert mock_exchange.execute.call_count == 1
    call_args = mock_exchange.execute.call_args[0][0]
    assert call_args.take_profit == 52000.0


@pytest.mark.asyncio
async def test_place_individual_risk_orders_both(
    dispatcher_with_mocks, sample_filled_order, sample_fill_result
):
    """Test placing both SL and TP as individual orders"""
    dispatcher, mock_exchange = dispatcher_with_mocks

    mock_exchange.execute.return_value = {
        "status": "pending",
        "order_id": "risk-order-123",
    }

    await dispatcher._place_individual_risk_orders(
        sample_filled_order, sample_fill_result
    )

    # Verify both orders were placed
    assert mock_exchange.execute.call_count == 2

    # First call should be SL
    first_call = mock_exchange.execute.call_args_list[0][0][0]
    assert first_call.stop_loss == 48000.0

    # Second call should be TP
    second_call = mock_exchange.execute.call_args_list[1][0][0]
    assert second_call.take_profit == 52000.0


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

    mock_exchange.execute.return_value = {
        "status": "pending",
        "order_id": "sl-order-zero",
    }

    # Should use order.amount as fallback
    await dispatcher._place_stop_loss_order(sample_filled_order, fill_result_zero)

    assert mock_exchange.execute.called
    call_args = mock_exchange.execute.call_args[0][0]
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

    mock_exchange.execute.return_value = {
        "status": "pending",
        "order_id": "tp-order-string",
    }

    await dispatcher._place_take_profit_order(sample_filled_order, fill_result_string)

    assert mock_exchange.execute.called
    call_args = mock_exchange.execute.call_args[0][0]
    assert call_args.amount == 0.001  # Should be converted to float


@pytest.mark.asyncio
async def test_place_stop_loss_handles_exception(
    dispatcher_with_mocks, sample_filled_order, sample_fill_result
):
    """Test that exceptions during SL placement are handled gracefully"""
    dispatcher, mock_exchange = dispatcher_with_mocks

    # Mock exception during order placement
    mock_exchange.execute.side_effect = Exception("Exchange error")

    # Should raise exception to trigger atomic rollback
    with pytest.raises(Exception, match="STOP LOSS FAILED AFTER ALL RETRIES"):
        await dispatcher._place_stop_loss_order(sample_filled_order, sample_fill_result)

    # Verify attempt was made
    assert mock_exchange.execute.called


@pytest.mark.asyncio
async def test_place_take_profit_handles_exception(
    dispatcher_with_mocks, sample_filled_order, sample_fill_result
):
    """Test that exceptions during TP placement are handled gracefully"""
    dispatcher, mock_exchange = dispatcher_with_mocks

    # Mock exception during order placement
    mock_exchange.execute.side_effect = Exception("Exchange error")

    # Should raise exception to trigger atomic rollback
    with pytest.raises(Exception, match="Exchange error"):
        await dispatcher._place_take_profit_order(
            sample_filled_order, sample_fill_result
        )

    # Verify attempt was made
    assert mock_exchange.execute.called


# ============================================================================
# AC1 — SL Direction Fix Tests
# ============================================================================


@pytest.mark.asyncio
async def test_sl_direction_fix_short_sl_below_entry_is_corrected(
    dispatcher_with_mocks,
):
    """AC1: SHORT signal where stop_loss < entry_price → SL is corrected before OCO placement."""
    dispatcher, mock_exchange = dispatcher_with_mocks

    # Set up OCO manager mock to capture the stop_loss_price passed to it
    oco_place_calls = []

    async def capture_oco(*args, **kwargs):
        oco_place_calls.append(kwargs)
        return {"status": "success", "sl_order_id": "sl-001", "tp_order_id": "tp-001"}

    dispatcher.oco_manager.place_oco_orders = capture_oco

    entry_price = 70900.0
    wrong_sl = 70785.4  # Below entry — wrong for SHORT
    stop_loss_pct = 0.02  # 2%
    expected_sl = entry_price * (1 + stop_loss_pct)  # 72318.0 — above entry

    short_order = TradeOrder(
        position_id="test-short-btc",
        symbol="BTCUSDT",
        side="sell",
        type="market",
        amount=0.001,
        target_price=entry_price,
        position_side="SHORT",
        exchange="binance",
        stop_loss=wrong_sl,
        stop_loss_pct=stop_loss_pct,
        take_profit=entry_price * (1 - 0.03),  # valid TP below entry for SHORT
    )

    fill_result = {
        "status": "filled",
        "fill_price": entry_price,
        "price": entry_price,
        "amount": 0.001,
    }

    dispatcher.position_manager.update_position_risk_orders = AsyncMock()

    await dispatcher._place_risk_management_orders(short_order, fill_result)

    assert len(oco_place_calls) == 1, "OCO orders should have been placed"
    actual_sl = oco_place_calls[0]["stop_loss_price"]
    assert actual_sl > entry_price, (
        f"SHORT stop_loss must be above entry_price. Got {actual_sl}, entry={entry_price}"
    )
    assert abs(actual_sl - expected_sl) < 0.01, (
        f"Expected corrected SL≈{expected_sl}, got {actual_sl}"
    )


@pytest.mark.asyncio
async def test_sl_direction_fix_long_sl_above_entry_is_corrected(dispatcher_with_mocks):
    """AC1 (LONG): LONG signal where stop_loss > entry_price → SL is corrected before OCO placement."""
    dispatcher, mock_exchange = dispatcher_with_mocks

    oco_place_calls = []

    async def capture_oco(*args, **kwargs):
        oco_place_calls.append(kwargs)
        return {"status": "success", "sl_order_id": "sl-002", "tp_order_id": "tp-002"}

    dispatcher.oco_manager.place_oco_orders = capture_oco

    entry_price = 50000.0
    wrong_sl = 51000.0  # Above entry — wrong for LONG
    stop_loss_pct = 0.02
    expected_sl = entry_price * (1 - stop_loss_pct)  # 49000.0

    long_order = TradeOrder(
        position_id="test-long-btc",
        symbol="BTCUSDT",
        side="buy",
        type="market",
        amount=0.001,
        target_price=entry_price,
        position_side="LONG",
        exchange="binance",
        stop_loss=wrong_sl,
        stop_loss_pct=stop_loss_pct,
        take_profit=entry_price * (1 + 0.03),  # valid TP above entry for LONG
    )

    fill_result = {
        "status": "filled",
        "fill_price": entry_price,
        "price": entry_price,
        "amount": 0.001,
    }

    dispatcher.position_manager.update_position_risk_orders = AsyncMock()

    await dispatcher._place_risk_management_orders(long_order, fill_result)

    assert len(oco_place_calls) == 1, "OCO orders should have been placed"
    actual_sl = oco_place_calls[0]["stop_loss_price"]
    assert actual_sl < entry_price, (
        f"LONG stop_loss must be below entry_price. Got {actual_sl}, entry={entry_price}"
    )
    assert abs(actual_sl - expected_sl) < 0.01, (
        f"Expected corrected SL≈{expected_sl}, got {actual_sl}"
    )


@pytest.mark.asyncio
async def test_sl_direction_fix_correct_short_sl_not_modified(dispatcher_with_mocks):
    """AC1 regression: valid SHORT SL (above entry) should not be modified."""
    dispatcher, mock_exchange = dispatcher_with_mocks

    oco_place_calls = []

    async def capture_oco(*args, **kwargs):
        oco_place_calls.append(kwargs)
        return {"status": "success", "sl_order_id": "sl-003", "tp_order_id": "tp-003"}

    dispatcher.oco_manager.place_oco_orders = capture_oco

    entry_price = 70900.0
    correct_sl = 72318.0  # Already above entry — correct for SHORT

    short_order = TradeOrder(
        position_id="test-short-valid",
        symbol="BTCUSDT",
        side="sell",
        type="market",
        amount=0.001,
        target_price=entry_price,
        position_side="SHORT",
        exchange="binance",
        stop_loss=correct_sl,
        stop_loss_pct=0.02,
        take_profit=entry_price * (1 - 0.03),
    )

    fill_result = {
        "status": "filled",
        "fill_price": entry_price,
        "price": entry_price,
        "amount": 0.001,
    }

    dispatcher.position_manager.update_position_risk_orders = AsyncMock()

    await dispatcher._place_risk_management_orders(short_order, fill_result)

    assert len(oco_place_calls) == 1
    actual_sl = oco_place_calls[0]["stop_loss_price"]
    assert abs(actual_sl - correct_sl) < 0.01, (
        f"Valid SHORT SL should not be modified. Expected {correct_sl}, got {actual_sl}"
    )
