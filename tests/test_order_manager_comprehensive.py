"""
Comprehensive tests for OrderManager to increase coverage to 75%.

This test suite covers:
1. Order tracking (active vs completed orders)
2. Conditional orders (setup, monitoring, execution)
3. Order queries (get_order, get_orders_by_symbol, get_order_history)
4. Order cancellation
5. Price monitoring
"""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest

from contracts.order import OrderStatus, OrderType, TradeOrder
from tradeengine.order_manager import OrderManager


@pytest.fixture
def order_manager():
    """Create OrderManager instance"""
    return OrderManager()


@pytest.fixture
def sample_order():
    """Sample order for testing"""
    return TradeOrder(
        symbol="BTCUSDT",
        side="buy",
        type=OrderType.MARKET,
        amount=0.001,
        target_price=50000.0,
        order_id="test_order_123",
    )


class TestOrderTracking:
    """Test order tracking functionality"""

    @pytest.mark.asyncio
    async def test_track_order_pending(self, order_manager, sample_order):
        """Test tracking pending order"""
        result = {"status": "pending", "order_id": "test_order_123"}
        await order_manager.track_order(sample_order, result)
        assert "test_order_123" in order_manager.active_orders

    @pytest.mark.asyncio
    async def test_track_order_filled(self, order_manager, sample_order):
        """Test tracking filled order"""
        result = {"status": "filled", "order_id": "test_order_123"}
        await order_manager.track_order(sample_order, result)
        assert "test_order_123" not in order_manager.active_orders
        assert len(order_manager.order_history) > 0

    @pytest.mark.asyncio
    async def test_track_order_partial(self, order_manager, sample_order):
        """Test tracking partially filled order"""
        result = {"status": "partial", "order_id": "test_order_123"}
        await order_manager.track_order(sample_order, result)
        assert "test_order_123" in order_manager.active_orders


class TestConditionalOrders:
    """Test conditional order functionality"""

    @pytest.mark.asyncio
    async def test_setup_conditional_order(self, order_manager, sample_order):
        """Test setting up conditional order"""
        sample_order.type = OrderType.CONDITIONAL_LIMIT
        sample_order.meta = {
            "conditional_price": 51000.0,
            "conditional_direction": "above",
            "conditional_timeout": 300,
        }
        result = {"status": "pending", "order_id": "conditional_123"}
        await order_manager._setup_conditional_order(sample_order, result)
        assert "conditional_123" in order_manager.conditional_orders

    @pytest.mark.asyncio
    async def test_check_condition_above(self, order_manager):
        """Test checking condition for 'above' direction"""
        order_info = {
            "conditional_price": 50000.0,
            "conditional_direction": "above",
        }
        # Price above condition
        assert order_manager._check_condition(order_info, 51000.0) is True
        # Price below condition
        assert order_manager._check_condition(order_info, 49000.0) is False
        # Price equal to condition
        assert order_manager._check_condition(order_info, 50000.0) is True

    @pytest.mark.asyncio
    async def test_check_condition_below(self, order_manager):
        """Test checking condition for 'below' direction"""
        order_info = {
            "conditional_price": 50000.0,
            "conditional_direction": "below",
        }
        # Price below condition
        assert order_manager._check_condition(order_info, 49000.0) is True
        # Price above condition
        assert order_manager._check_condition(order_info, 51000.0) is False
        # Price equal to condition
        assert order_manager._check_condition(order_info, 50000.0) is True

    @pytest.mark.asyncio
    async def test_check_condition_no_price_or_direction(self, order_manager):
        """Test checking condition when price or direction is missing"""
        order_info = {}
        assert order_manager._check_condition(order_info, 50000.0) is False

    @pytest.mark.asyncio
    async def test_get_current_price_cached(self, order_manager):
        """Test getting current price from cache"""
        # Set up cache
        order_manager.price_cache["BTCUSDT"] = 50000.0
        order_manager.last_price_update["BTCUSDT"] = datetime.utcnow()

        price = await order_manager._get_current_price("BTCUSDT")
        assert price == 50000.0

    @pytest.mark.asyncio
    async def test_get_current_price_no_cache(self, order_manager):
        """Test getting current price when not in cache"""
        price = await order_manager._get_current_price("BTCUSDT")
        assert isinstance(price, float)
        assert price > 0
        assert "BTCUSDT" in order_manager.price_cache


class TestOrderQueries:
    """Test order query methods"""

    @pytest.mark.asyncio
    async def test_get_order_from_active(self, order_manager, sample_order):
        """Test getting order from active orders"""
        result = {"status": "pending", "order_id": "test_order_123"}
        await order_manager.track_order(sample_order, result)

        order = order_manager.get_order("test_order_123")
        assert order is not None
        assert order["order_id"] == "test_order_123"

    @pytest.mark.asyncio
    async def test_get_order_from_conditional(self, order_manager, sample_order):
        """Test getting order from conditional orders"""
        sample_order.type = OrderType.CONDITIONAL_LIMIT
        sample_order.meta = {
            "conditional_price": 51000.0,
            "conditional_direction": "above",
        }
        result = {"status": "pending", "order_id": "conditional_123"}
        await order_manager._setup_conditional_order(sample_order, result)

        order = order_manager.get_order("conditional_123")
        assert order is not None
        assert order["order_id"] == "conditional_123"

    @pytest.mark.asyncio
    async def test_get_order_not_found(self, order_manager):
        """Test getting non-existent order"""
        order = order_manager.get_order("nonexistent")
        assert order is None

    @pytest.mark.asyncio
    async def test_get_active_orders(self, order_manager):
        """Test getting all active orders"""
        order1 = TradeOrder(
            symbol="BTCUSDT",
            side="buy",
            type=OrderType.MARKET,
            amount=0.001,
            target_price=50000.0,
            order_id="order1",
        )
        order2 = TradeOrder(
            symbol="ETHUSDT",
            side="buy",
            type=OrderType.MARKET,
            amount=0.01,
            target_price=3000.0,
            order_id="order2",
        )

        await order_manager.track_order(
            order1, {"status": "pending", "order_id": "order1"}
        )
        await order_manager.track_order(
            order2, {"status": "pending", "order_id": "order2"}
        )

        active_orders = order_manager.get_active_orders()
        assert len(active_orders) == 2
        assert all(o["order_id"] in ["order1", "order2"] for o in active_orders)

    @pytest.mark.asyncio
    async def test_get_order_history(self, order_manager, sample_order):
        """Test getting order history"""
        result = {"status": "filled", "order_id": "test_order_123"}
        await order_manager.track_order(sample_order, result)

        history = order_manager.get_order_history()
        assert len(history) > 0
        assert any(o["order_id"] == "test_order_123" for o in history)


class TestOrderCancellation:
    """Test order cancellation"""

    @pytest.mark.asyncio
    async def test_cancel_order_active(self, order_manager, sample_order):
        """Test cancelling active order"""
        result = {"status": "pending", "order_id": "test_order_123"}
        await order_manager.track_order(sample_order, result)

        cancelled = order_manager.cancel_order("test_order_123")
        assert cancelled is True
        assert "test_order_123" not in order_manager.active_orders

    def test_cancel_order_not_found(self, order_manager):
        """Test cancelling non-existent order"""
        cancelled = order_manager.cancel_order("nonexistent")
        assert cancelled is False

    @pytest.mark.asyncio
    async def test_cancel_conditional_order(self, order_manager, sample_order):
        """Test cancelling conditional order"""
        sample_order.type = OrderType.CONDITIONAL_LIMIT
        sample_order.meta = {
            "conditional_price": 51000.0,
            "conditional_direction": "above",
        }
        result = {"status": "pending", "order_id": "conditional_123"}
        await order_manager._setup_conditional_order(sample_order, result)

        # Give a small delay for the monitoring task to be scheduled
        await asyncio.sleep(0.1)

        cancelled = await order_manager.cancel_conditional_order("conditional_123")
        assert cancelled is True
        assert "conditional_123" not in order_manager.conditional_orders


class TestInitialization:
    """Test OrderManager initialization and cleanup"""

    @pytest.mark.asyncio
    async def test_initialize(self, order_manager):
        """Test OrderManager initialization"""
        await order_manager.initialize()
        # Should not raise exception

    @pytest.mark.asyncio
    async def test_close(self, order_manager):
        """Test OrderManager cleanup"""
        await order_manager.close()
        # Should not raise exception
