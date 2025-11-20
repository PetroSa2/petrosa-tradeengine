"""
Comprehensive tests for OCO (One-Cancels-the-Other) order functionality.

This test suite verifies:
1. Position creation with SL/TP orders
2. OCO order placement (both SL and TP)
3. OCO monitoring and cancellation logic
4. When one order fills, the other is cancelled
"""

import asyncio
import time
from typing import Any, Dict
from unittest.mock import AsyncMock, Mock

import pytest

from contracts.order import TradeOrder
from contracts.signal import Signal
from tradeengine.dispatcher import Dispatcher, OCOManager


@pytest.fixture
def mock_exchange():
    """Mock exchange for testing OCO functionality"""
    exchange = Mock()
    exchange.client = Mock()

    # Mock order execution - return successful results
    async def mock_execute(order: TradeOrder) -> Dict[str, Any]:
        return {
            "status": "filled",
            "order_id": f"test_order_{int(time.time())}_{order.type}",
            "fill_price": order.target_price or 50000.0,
            "amount": order.amount,
            "symbol": order.symbol,
        }

    exchange.execute = mock_execute

    # Mock batch order cancellation
    def mock_cancel_batch(symbol: str, orderIdList: list) -> list:
        return [{"orderId": oid, "status": "CANCELED"} for oid in orderIdList]

    exchange.client.futures_cancel_batch_orders = mock_cancel_batch

    # Mock single order cancellation
    def mock_cancel_order(symbol: str, orderId: str) -> dict:
        return {"orderId": orderId, "status": "CANCELED"}

    exchange.client.futures_cancel_order = mock_cancel_order

    # Mock get open orders
    exchange.client.futures_get_open_orders = Mock(return_value=[])

    return exchange


@pytest.fixture
def mock_position_manager():
    """Mock position manager for testing"""
    manager = Mock()
    manager.check_position_limits = AsyncMock(return_value=True)
    manager.check_daily_loss_limits = AsyncMock(return_value=True)
    manager.update_position = AsyncMock(return_value=None)
    manager.create_position_record = AsyncMock(return_value=None)
    manager.update_position_risk_orders = AsyncMock(return_value=None)
    return manager


@pytest.fixture
def oco_manager(mock_exchange):
    """Create OCO manager instance for testing"""
    import logging

    logger = logging.getLogger("test_oco")
    return OCOManager(exchange=mock_exchange, logger=logger)


@pytest.fixture
def sample_long_signal() -> Signal:
    """Sample LONG signal with SL/TP for testing"""
    return Signal(
        strategy_id="test-strategy-oco",
        symbol="BTCUSDT",
        signal_type="buy",
        action="buy",
        confidence=0.85,
        strength="strong",
        timeframe="1h",
        price=50000.0,
        quantity=0.001,
        current_price=50000.0,
        stop_loss=48000.0,  # 2% below entry
        take_profit=52000.0,  # 4% above entry
        source="test",
        strategy="test-oco-strategy",
    )


@pytest.fixture
def sample_short_signal() -> Signal:
    """Sample SHORT signal with SL/TP for testing"""
    return Signal(
        strategy_id="test-strategy-oco-short",
        symbol="ETHUSDT",
        signal_type="sell",
        action="sell",
        confidence=0.85,
        strength="strong",
        timeframe="1h",
        price=3000.0,
        quantity=0.01,
        current_price=3000.0,
        stop_loss=3060.0,  # 2% above entry for SHORT
        take_profit=2940.0,  # 2% below entry for SHORT
        source="test",
        strategy="test-oco-strategy-short",
    )


@pytest.mark.asyncio
async def test_oco_manager_initialization(oco_manager: OCOManager):
    """Test OCO manager initialization"""
    assert oco_manager is not None
    assert oco_manager.active_oco_pairs == {}
    assert oco_manager.monitoring_active is False
    assert oco_manager.monitoring_task is None


@pytest.mark.asyncio
async def test_place_oco_orders_long_position(oco_manager: OCOManager):
    """Test placing OCO orders for a LONG position"""
    result = await oco_manager.place_oco_orders(
        position_id="test_pos_long_123",
        symbol="BTCUSDT",
        position_side="LONG",
        quantity=0.001,
        stop_loss_price=48000.0,
        take_profit_price=52000.0,
    )

    # Verify the result
    assert result["status"] == "success"
    assert "sl_order_id" in result
    assert "tp_order_id" in result
    assert result["sl_order_id"] is not None
    assert result["tp_order_id"] is not None

    # Verify OCO pair was stored (key is exchange_position_key: symbol_position_side)
    exchange_key = "BTCUSDT_LONG"
    assert exchange_key in oco_manager.active_oco_pairs
    oco_list = oco_manager.active_oco_pairs[exchange_key]
    assert len(oco_list) > 0
    oco_info = oco_list[0]  # Get first OCO pair
    assert oco_info["status"] == "active"
    assert oco_info["symbol"] == "BTCUSDT"
    assert oco_info["position_side"] == "LONG"

    # Verify monitoring was started
    assert oco_manager.monitoring_active is True

    # Clean up
    await oco_manager.stop_monitoring()


@pytest.mark.asyncio
async def test_place_oco_orders_short_position(oco_manager: OCOManager):
    """Test placing OCO orders for a SHORT position"""
    result = await oco_manager.place_oco_orders(
        position_id="test_pos_short_456",
        symbol="ETHUSDT",
        position_side="SHORT",
        quantity=0.01,
        stop_loss_price=3060.0,
        take_profit_price=2940.0,
    )

    # Verify the result
    assert result["status"] == "success"
    assert "sl_order_id" in result
    assert "tp_order_id" in result

    # Verify OCO pair was stored (key is exchange_position_key: symbol_position_side)
    exchange_key = "ETHUSDT_SHORT"
    assert exchange_key in oco_manager.active_oco_pairs
    oco_list = oco_manager.active_oco_pairs[exchange_key]
    assert len(oco_list) > 0
    oco_info = oco_list[0]  # Get first OCO pair
    assert oco_info["status"] == "active"
    assert oco_info["symbol"] == "ETHUSDT"
    assert oco_info["position_side"] == "SHORT"

    # Clean up
    await oco_manager.stop_monitoring()


@pytest.mark.asyncio
async def test_cancel_oco_pair(oco_manager: OCOManager):
    """Test cancelling both SL and TP orders"""
    # First, place OCO orders
    await oco_manager.place_oco_orders(
        position_id="test_pos_cancel_789",
        symbol="BTCUSDT",
        position_side="LONG",
        quantity=0.001,
        stop_loss_price=48000.0,
        take_profit_price=52000.0,
    )

    # Cancel the OCO pair (need to pass symbol and position_side for new key structure)
    result = await oco_manager.cancel_oco_pair(
        "test_pos_cancel_789", symbol="BTCUSDT", position_side="LONG"
    )

    # Verify cancellation succeeded
    assert result is True

    # Verify the status was updated (use exchange key)
    exchange_key = "BTCUSDT_LONG"
    if exchange_key in oco_manager.active_oco_pairs:
        oco_list = oco_manager.active_oco_pairs[exchange_key]
        if len(oco_list) > 0:
            assert oco_list[0]["status"] == "cancelled"

    # Clean up
    await oco_manager.stop_monitoring()


@pytest.mark.asyncio
async def test_cancel_other_order_when_sl_fills(oco_manager: OCOManager):
    """Test that TP order is cancelled when SL fills"""
    # Place OCO orders
    result = await oco_manager.place_oco_orders(
        position_id="test_pos_sl_fill_111",
        symbol="BTCUSDT",
        position_side="LONG",
        quantity=0.001,
        stop_loss_price=48000.0,
        take_profit_price=52000.0,
    )

    sl_order_id = result["sl_order_id"]
    _tp_order_id = result["tp_order_id"]  # noqa: F841

    # Wait a moment for OCO pair to be registered
    await asyncio.sleep(0.1)

    # Simulate SL order being filled - pass symbol and position_side to help find the pair
    cancel_result = await oco_manager.cancel_other_order(
        "test_pos_sl_fill_111", sl_order_id, symbol="BTCUSDT", position_side="LONG"
    )

    # Verify TP order was cancelled
    assert (
        cancel_result[0] is True
    ), f"Expected cancel_result[0] to be True, got {cancel_result}"
    assert (
        cancel_result[1] == "stop_loss"
    ), f"Expected close_reason to be 'stop_loss', got {cancel_result[1]}"

    # Clean up
    await oco_manager.stop_monitoring()


@pytest.mark.asyncio
async def test_cancel_other_order_when_tp_fills(oco_manager: OCOManager):
    """Test that SL order is cancelled when TP fills"""
    # Place OCO orders
    result = await oco_manager.place_oco_orders(
        position_id="test_pos_tp_fill_222",
        symbol="BTCUSDT",
        position_side="LONG",
        quantity=0.001,
        stop_loss_price=48000.0,
        take_profit_price=52000.0,
    )

    _sl_order_id = result["sl_order_id"]  # noqa: F841
    tp_order_id = result["tp_order_id"]

    # Simulate TP order being filled
    cancel_result = await oco_manager.cancel_other_order(
        "test_pos_tp_fill_222", tp_order_id, symbol="BTCUSDT", position_side="LONG"
    )

    # Verify SL order was cancelled
    assert cancel_result[0] is True
    assert cancel_result[1] == "take_profit"

    # Clean up
    await oco_manager.stop_monitoring()


@pytest.mark.asyncio
async def test_oco_monitoring_detects_filled_order(
    oco_manager: OCOManager, mock_exchange
):
    """Test that monitoring system detects filled orders and cancels the other"""
    # Place OCO orders
    result = await oco_manager.place_oco_orders(
        position_id="test_pos_monitor_333",
        symbol="BTCUSDT",
        position_side="LONG",
        quantity=0.001,
        stop_loss_price=48000.0,
        take_profit_price=52000.0,
    )

    sl_order_id = result["sl_order_id"]
    tp_order_id = result["tp_order_id"]

    # Initially both orders are "open"
    mock_exchange.client.futures_get_open_orders.return_value = [
        {"orderId": sl_order_id, "status": "NEW"},
        {"orderId": tp_order_id, "status": "NEW"},
    ]

    # Verify OCO pair is active before simulation
    assert "test_pos_monitor_333" in oco_manager.active_oco_pairs
    initial_status = oco_manager.active_oco_pairs["test_pos_monitor_333"]["status"]
    assert initial_status == "active"

    # After 0.5 seconds, simulate TP order being filled (removed from open orders)
    async def simulate_tp_fill():
        await asyncio.sleep(0.5)
        mock_exchange.client.futures_get_open_orders.return_value = [
            {"orderId": sl_order_id, "status": "NEW"}  # Only SL remains
        ]

    # Start the fill simulation
    asyncio.create_task(simulate_tp_fill())

    # Wait for monitoring to detect and cancel (monitoring checks every 2 seconds)
    await asyncio.sleep(3)

    # Verify the OCO pair was completed or removed
    # Note: The monitoring loop removes completed pairs from active_oco_pairs
    # So we check if it's either completed or removed
    if "test_pos_monitor_333" in oco_manager.active_oco_pairs:
        assert (
            oco_manager.active_oco_pairs["test_pos_monitor_333"]["status"]
            == "completed"
        )
    # If removed, that's also correct behavior (completed and cleaned up)

    # Clean up
    await oco_manager.stop_monitoring()


@pytest.mark.asyncio
async def test_dispatcher_places_oco_orders_on_position_open(
    mock_exchange, mock_position_manager, sample_long_signal: Signal
):
    """Test that dispatcher places OCO orders when opening a position with SL/TP"""

    # Create dispatcher with mocked exchange
    dispatcher = Dispatcher(exchange=mock_exchange)

    # Override the position manager
    dispatcher.position_manager = mock_position_manager

    # Process the signal
    result = await dispatcher.dispatch(sample_long_signal)

    # Verify the position was created
    assert result is not None

    # Verify position manager methods were called
    mock_position_manager.update_position.assert_called()
    mock_position_manager.create_position_record.assert_called()

    # Verify OCO manager has active pairs
    assert len(dispatcher.oco_manager.active_oco_pairs) > 0

    # Clean up
    await dispatcher.oco_manager.stop_monitoring()


@pytest.mark.asyncio
async def test_full_oco_lifecycle_long_position(mock_exchange, mock_position_manager):
    """
    Integration test: Full OCO lifecycle for LONG position
    1. Open position with SL/TP
    2. Verify both orders are placed
    3. Simulate one order filling
    4. Verify the other is cancelled
    """

    # Create dispatcher with mocked exchange
    dispatcher = Dispatcher(exchange=mock_exchange)
    dispatcher.position_manager = mock_position_manager

    # Create signal with SL/TP
    signal = Signal(
        strategy_id="test-full-lifecycle",
        symbol="BTCUSDT",
        signal_type="buy",
        action="buy",
        confidence=0.85,
        strength="strong",
        timeframe="1h",
        price=50000.0,
        quantity=0.001,
        current_price=50000.0,
        stop_loss=48000.0,
        take_profit=52000.0,
        source="test",
        strategy="test-lifecycle",
    )

    # Step 1: Open position
    result = await dispatcher.dispatch(signal)
    assert result is not None

    # Step 2: Verify OCO orders were placed
    assert len(dispatcher.oco_manager.active_oco_pairs) > 0
    # OCO pairs are stored under exchange_position_key
    exchange_position_key = "BTCUSDT_LONG"
    assert exchange_position_key in dispatcher.oco_manager.active_oco_pairs
    oco_list = dispatcher.oco_manager.active_oco_pairs[exchange_position_key]
    assert len(oco_list) > 0
    oco_info = oco_list[0]  # Get first OCO pair
    position_id = oco_info.get("position_id", exchange_position_key)

    assert oco_info["status"] == "active"
    assert "sl_order_id" in oco_info
    assert "tp_order_id" in oco_info

    _sl_order_id = oco_info["sl_order_id"]  # noqa: F841
    tp_order_id = oco_info["tp_order_id"]

    # Step 3: Simulate TP order filling
    cancel_result = await dispatcher.oco_manager.cancel_other_order(
        position_id, tp_order_id, symbol="BTCUSDT", position_side="LONG"
    )

    # Step 4: Verify SL was cancelled
    assert cancel_result[0] is True
    assert cancel_result[1] == "take_profit"

    # Clean up
    await dispatcher.oco_manager.stop_monitoring()


@pytest.mark.asyncio
async def test_full_oco_lifecycle_short_position(mock_exchange, mock_position_manager):
    """
    Integration test: Full OCO lifecycle for SHORT position
    1. Open SHORT position with SL/TP
    2. Verify both orders are placed
    3. Simulate SL filling
    4. Verify TP is cancelled
    """

    # Create dispatcher with mocked exchange
    dispatcher = Dispatcher(exchange=mock_exchange)
    dispatcher.position_manager = mock_position_manager

    # Create SHORT signal with SL/TP
    signal = Signal(
        strategy_id="test-full-lifecycle-short",
        symbol="ETHUSDT",
        signal_type="sell",
        action="sell",
        confidence=0.85,
        strength="strong",
        timeframe="1h",
        price=3000.0,
        quantity=0.01,
        current_price=3000.0,
        stop_loss=3060.0,  # 2% above for SHORT
        take_profit=2940.0,  # 2% below for SHORT
        source="test",
        strategy="test-lifecycle-short",
    )

    # Step 1: Open position
    result = await dispatcher.dispatch(signal)
    assert result is not None

    # Step 2: Verify OCO orders were placed
    assert len(dispatcher.oco_manager.active_oco_pairs) > 0
    # OCO pairs are stored under exchange_position_key
    exchange_position_key = "ETHUSDT_SHORT"
    assert exchange_position_key in dispatcher.oco_manager.active_oco_pairs
    oco_list = dispatcher.oco_manager.active_oco_pairs[exchange_position_key]
    assert len(oco_list) > 0
    oco_info = oco_list[0]  # Get first OCO pair
    position_id = oco_info.get("position_id", exchange_position_key)

    assert oco_info["status"] == "active"
    assert oco_info["position_side"] == "SHORT"

    sl_order_id = oco_info["sl_order_id"]
    _tp_order_id = oco_info["tp_order_id"]  # noqa: F841

    # Step 3: Simulate SL order filling (position hits stop loss)
    cancel_result = await dispatcher.oco_manager.cancel_other_order(
        position_id, sl_order_id, symbol="ETHUSDT", position_side="SHORT"
    )

    # Step 4: Verify TP was cancelled
    assert cancel_result[0] is True
    assert cancel_result[1] == "stop_loss"

    # Clean up
    await dispatcher.oco_manager.stop_monitoring()


@pytest.mark.asyncio
async def test_multiple_concurrent_oco_positions(mock_exchange, mock_position_manager):
    """Test handling multiple OCO positions simultaneously"""

    # Create dispatcher with mocked exchange
    dispatcher = Dispatcher(exchange=mock_exchange)
    dispatcher.position_manager = mock_position_manager

    # Create multiple signals
    signals = [
        Signal(
            strategy_id=f"test-multi-{i}",
            symbol="BTCUSDT",
            signal_type="buy",
            action="buy",
            confidence=0.85,
            strength="strong",
            timeframe="1h",
            price=50000.0 + (i * 100),
            quantity=0.001,
            current_price=50000.0 + (i * 100),
            stop_loss=48000.0 + (i * 100),
            take_profit=52000.0 + (i * 100),
            source="test",
            strategy=f"test-multi-strategy-{i}",
        )
        for i in range(3)
    ]

    # Process all signals
    for signal in signals:
        await dispatcher.dispatch(signal)

    # Verify all OCO pairs were created (stored under exchange_position_key)
    exchange_position_key = "BTCUSDT_LONG"
    assert exchange_position_key in dispatcher.oco_manager.active_oco_pairs
    oco_list = dispatcher.oco_manager.active_oco_pairs[exchange_position_key]
    assert len(oco_list) == 3

    # Verify all are active
    for oco_info in oco_list:
        assert oco_info["status"] == "active"
        assert "sl_order_id" in oco_info
        assert "tp_order_id" in oco_info

    # Clean up
    await dispatcher.oco_manager.stop_monitoring()


@pytest.mark.asyncio
async def test_oco_order_placement_without_sl_or_tp():
    """Test that OCO orders are NOT placed when SL or TP is missing"""

    mock_exchange = Mock()
    mock_exchange.execute = AsyncMock(
        return_value={
            "status": "filled",
            "order_id": "test_order_123",
            "fill_price": 50000.0,
            "amount": 0.001,
        }
    )

    mock_position_manager = Mock()
    mock_position_manager.check_position_limits = AsyncMock(return_value=True)
    mock_position_manager.check_daily_loss_limits = AsyncMock(return_value=True)
    mock_position_manager.update_position = AsyncMock(return_value=None)
    mock_position_manager.create_position_record = AsyncMock(return_value=None)

    # Create dispatcher with mocked exchange
    dispatcher = Dispatcher(exchange=mock_exchange)
    dispatcher.position_manager = mock_position_manager

    # Signal without SL/TP
    signal = Signal(
        strategy_id="test-no-sltp",
        symbol="BTCUSDT",
        signal_type="buy",
        action="buy",
        confidence=0.85,
        strength="strong",
        timeframe="1h",
        price=50000.0,
        quantity=0.001,
        current_price=50000.0,
        source="test",
        strategy="test-no-sltp",
    )

    await dispatcher.dispatch(signal)

    # Verify NO OCO pairs were created
    assert len(dispatcher.oco_manager.active_oco_pairs) == 0

    # Clean up
    await dispatcher.oco_manager.stop_monitoring()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
