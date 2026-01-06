"""Tests for OrderManager class."""

from unittest.mock import patch

import pytest

from contracts.order import OrderStatus, TradeOrder
from tradeengine.order_manager import OrderManager


@pytest.fixture
def order_manager() -> OrderManager:
    """Create an OrderManager instance for testing."""
    return OrderManager()


@pytest.fixture
def sample_order() -> TradeOrder:
    """Create a sample trade order for testing."""
    return TradeOrder(
        symbol="BTCUSDT",
        type="market",
        side="buy",
        amount=0.1,
        order_id="test-order-1",
        status=OrderStatus.PENDING,
        time_in_force="GTC",
        target_price=50000.0,
    )


@pytest.fixture
def sample_limit_order() -> TradeOrder:
    """Create a sample limit order for testing."""
    return TradeOrder(
        symbol="ETHUSDT",
        type="limit",
        side="sell",
        amount=1.0,
        order_id="test-order-2",
        status=OrderStatus.PENDING,
        time_in_force="GTC",
        target_price=3000.0,
    )


# ============================================================================
# Basic Methods Tests
# ============================================================================


@pytest.mark.asyncio
async def test_order_manager_initialize(order_manager: OrderManager) -> None:
    """Test OrderManager initialization."""
    await order_manager.initialize()
    # Should not raise any exceptions
    assert order_manager.active_orders == {}
    assert order_manager.conditional_orders == {}
    assert order_manager.order_history == []


@pytest.mark.asyncio
async def test_order_manager_close(order_manager: OrderManager) -> None:
    """Test OrderManager close method."""
    await order_manager.close()
    # Should not raise any exceptions


def test_order_manager_log_event(order_manager: OrderManager) -> None:
    """Test OrderManager log_event method."""
    with patch("tradeengine.order_manager.audit_logger") as mock_audit:
        order_manager.log_event("test_event", {"key": "value"})
        mock_audit.log_event.assert_called_once_with("test_event", {"key": "value"})


@pytest.mark.asyncio
async def test_order_manager_get_account_info(order_manager: OrderManager) -> None:
    """Test OrderManager get_account_info method."""
    result = await order_manager.get_account_info()
    assert result == {}


@pytest.mark.asyncio
async def test_order_manager_get_price(order_manager: OrderManager) -> None:
    """Test OrderManager get_price method."""
    price = await order_manager.get_price("BTCUSDT")
    assert price == 0.0


def test_order_manager_get_metrics(order_manager: OrderManager) -> None:
    """Test OrderManager get_metrics method."""
    metrics = order_manager.get_metrics()
    assert metrics == {}


# ============================================================================
# Order Tracking Tests
# ============================================================================


@pytest.mark.asyncio
async def test_track_order_filled(
    order_manager: OrderManager, sample_order: TradeOrder
) -> None:
    """Test tracking a filled order."""
    result = {
        "order_id": "test-order-1",
        "status": "filled",
        "fill_price": 50000.0,
        "amount": 0.1,
    }

    await order_manager.track_order(sample_order, result)

    # Should be in history, not active orders
    assert len(order_manager.order_history) == 1
    assert len(order_manager.active_orders) == 0
    assert order_manager.order_history[0]["order_id"] == "test-order-1"
    assert order_manager.order_history[0]["status"] == "filled"


@pytest.mark.asyncio
async def test_track_order_pending(
    order_manager: OrderManager, sample_order: TradeOrder
) -> None:
    """Test tracking a pending order."""
    result = {
        "order_id": "test-order-1",
        "status": "pending",
        "amount": 0.1,
    }

    await order_manager.track_order(sample_order, result)

    # Should be in active orders, not history
    assert len(order_manager.active_orders) == 1
    assert len(order_manager.order_history) == 0
    assert "test-order-1" in order_manager.active_orders
    assert order_manager.active_orders["test-order-1"]["status"] == "pending"


@pytest.mark.asyncio
async def test_track_order_partial(
    order_manager: OrderManager, sample_order: TradeOrder
) -> None:
    """Test tracking a partially filled order."""
    result = {
        "order_id": "test-order-1",
        "status": "partial",
        "fill_price": 50000.0,
        "amount": 0.05,  # Partially filled
    }

    await order_manager.track_order(sample_order, result)

    # Should be in active orders
    assert len(order_manager.active_orders) == 1
    assert order_manager.active_orders["test-order-1"]["status"] == "partial"


@pytest.mark.asyncio
async def test_track_order_without_order_id(
    order_manager: OrderManager, sample_order: TradeOrder
) -> None:
    """Test tracking an order without explicit order_id in result."""
    result = {
        "status": "filled",
        "fill_price": 50000.0,
    }

    await order_manager.track_order(sample_order, result)

    # Should generate order_id from timestamp
    assert len(order_manager.order_history) == 1
    assert "order_id" in order_manager.order_history[0]


# ============================================================================
# Order Retrieval Tests
# ============================================================================


@pytest.mark.asyncio
async def test_get_active_orders_empty(order_manager: OrderManager) -> None:
    """Test getting active orders when none exist."""
    active = order_manager.get_active_orders()
    assert active == []


@pytest.mark.asyncio
async def test_get_active_orders_with_orders(
    order_manager: OrderManager, sample_order: TradeOrder
) -> None:
    """Test getting active orders."""
    result = {"order_id": "test-order-1", "status": "pending"}
    await order_manager.track_order(sample_order, result)

    active = order_manager.get_active_orders()
    assert len(active) == 1
    assert active[0]["order_id"] == "test-order-1"


@pytest.mark.asyncio
async def test_get_conditional_orders_empty(order_manager: OrderManager) -> None:
    """Test getting conditional orders when none exist."""
    conditional = order_manager.get_conditional_orders()
    assert conditional == []


@pytest.mark.asyncio
async def test_get_order_history_empty(order_manager: OrderManager) -> None:
    """Test getting order history when empty."""
    history = order_manager.get_order_history()
    assert history == []


@pytest.mark.asyncio
async def test_get_order_history_with_orders(
    order_manager: OrderManager, sample_order: TradeOrder
) -> None:
    """Test getting order history."""
    result = {"order_id": "test-order-1", "status": "filled"}
    await order_manager.track_order(sample_order, result)

    history = order_manager.get_order_history()
    assert len(history) == 1
    assert history[0]["order_id"] == "test-order-1"


@pytest.mark.asyncio
async def test_get_order_from_active(
    order_manager: OrderManager, sample_order: TradeOrder
) -> None:
    """Test getting an order from active orders."""
    result = {"order_id": "test-order-1", "status": "pending"}
    await order_manager.track_order(sample_order, result)

    order = order_manager.get_order("test-order-1")
    assert order is not None
    assert order["order_id"] == "test-order-1"
    assert order["status"] == "pending"


@pytest.mark.asyncio
async def test_get_order_from_history(
    order_manager: OrderManager, sample_order: TradeOrder
) -> None:
    """Test getting an order from history."""
    result = {"order_id": "test-order-1", "status": "filled"}
    await order_manager.track_order(sample_order, result)

    order = order_manager.get_order("test-order-1")
    assert order is not None
    assert order["order_id"] == "test-order-1"
    assert order["status"] == "filled"


@pytest.mark.asyncio
async def test_get_order_not_found(order_manager: OrderManager) -> None:
    """Test getting a non-existent order."""
    order = order_manager.get_order("non-existent")
    assert order is None


# ============================================================================
# Order Cancellation Tests
# ============================================================================


@pytest.mark.asyncio
async def test_cancel_active_order(
    order_manager: OrderManager, sample_order: TradeOrder
) -> None:
    """Test cancelling an active order."""
    result = {"order_id": "test-order-1", "status": "pending"}
    await order_manager.track_order(sample_order, result)

    cancelled = order_manager.cancel_order("test-order-1")
    assert cancelled is True
    assert len(order_manager.active_orders) == 0
    assert len(order_manager.order_history) == 1
    assert order_manager.order_history[0]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_order_not_found(order_manager: OrderManager) -> None:
    """Test cancelling a non-existent order."""
    cancelled = order_manager.cancel_order("non-existent")
    assert cancelled is False


# ============================================================================
# Order Summary Tests
# ============================================================================


def test_get_order_summary_empty(order_manager: OrderManager) -> None:
    """Test getting order summary when no orders exist."""
    summary = order_manager.get_order_summary()
    assert summary["active_orders"] == 0
    assert summary["conditional_orders"] == 0
    assert summary["total_orders"] == 0
    assert summary["status_distribution"] == {}


@pytest.mark.asyncio
async def test_get_order_summary_with_orders(
    order_manager: OrderManager,
    sample_order: TradeOrder,
    sample_limit_order: TradeOrder,
) -> None:
    """Test getting order summary with multiple orders."""
    # Add a filled order
    result1 = {"order_id": "test-order-1", "status": "filled"}
    await order_manager.track_order(sample_order, result1)

    # Add a pending order
    result2 = {"order_id": "test-order-2", "status": "pending"}
    await order_manager.track_order(sample_limit_order, result2)

    summary = order_manager.get_order_summary()
    assert summary["active_orders"] == 1
    assert summary["conditional_orders"] == 0
    assert summary["total_orders"] == 1
    assert "filled" in summary["status_distribution"]
    assert summary["status_distribution"]["filled"] == 1


@pytest.mark.asyncio
async def test_get_order_summary_status_distribution(
    order_manager: OrderManager, sample_order: TradeOrder
) -> None:
    """Test order summary status distribution."""
    # Add multiple orders with different statuses
    for i, status in enumerate(["filled", "cancelled", "filled", "failed"]):
        order = TradeOrder(
            symbol="BTCUSDT",
            type="market",
            side="buy",
            amount=0.1,
            order_id=f"test-order-{i}",
            status=OrderStatus.PENDING,
            time_in_force="GTC",
        )
        result = {"order_id": f"test-order-{i}", "status": status}
        await order_manager.track_order(order, result)

    summary = order_manager.get_order_summary()
    assert summary["status_distribution"]["filled"] == 2
    assert summary["status_distribution"]["cancelled"] == 1
    assert summary["status_distribution"]["failed"] == 1


@pytest.mark.asyncio
async def test_track_conditional_order(
    order_manager: OrderManager, sample_order: TradeOrder
) -> None:
    """Test tracking a conditional order."""
    conditional_order = TradeOrder(
        symbol="BTCUSDT",
        type="conditional_limit",
        side="buy",
        amount=0.1,
        order_id="conditional-order-1",
        status=OrderStatus.PENDING,
        time_in_force="GTC",
        target_price=50000.0,
        meta={"conditional_price": 49000.0, "conditional_direction": "below"},
    )

    result = {"order_id": "conditional-order-1", "status": "pending"}

    with patch("asyncio.create_task"):
        await order_manager.track_order(conditional_order, result)

        # Should have set up conditional order
        assert len(order_manager.conditional_orders) == 1
        assert "conditional-order-1" in order_manager.conditional_orders


@pytest.mark.asyncio
async def test_get_conditional_orders_with_orders(
    order_manager: OrderManager, sample_order: TradeOrder
) -> None:
    """Test getting conditional orders when they exist."""
    conditional_order = TradeOrder(
        symbol="BTCUSDT",
        type="conditional_stop",
        side="sell",
        amount=0.1,
        order_id="conditional-order-1",
        status=OrderStatus.PENDING,
        time_in_force="GTC",
        meta={"conditional_price": 51000.0, "conditional_direction": "above"},
    )

    result = {"order_id": "conditional-order-1", "status": "pending"}

    with patch("asyncio.create_task"):
        await order_manager.track_order(conditional_order, result)

        conditional_orders = order_manager.get_conditional_orders()
        assert len(conditional_orders) == 1
        assert conditional_orders[0]["order_id"] == "conditional-order-1"


@pytest.mark.asyncio
async def test_get_order_from_conditional(
    order_manager: OrderManager, sample_order: TradeOrder
) -> None:
    """Test getting an order from conditional orders."""
    conditional_order = TradeOrder(
        symbol="BTCUSDT",
        type="conditional_limit",
        side="buy",
        amount=0.1,
        order_id="conditional-order-1",
        status=OrderStatus.PENDING,
        time_in_force="GTC",
        meta={"conditional_price": 49000.0, "conditional_direction": "below"},
    )

    result = {"order_id": "conditional-order-1", "status": "pending"}

    with patch("asyncio.create_task"):
        await order_manager.track_order(conditional_order, result)

        # get_order checks active_orders first, then conditional_orders
        # For conditional orders, check conditional_orders dict directly
        assert "conditional-order-1" in order_manager.conditional_orders
        conditional_order_info = order_manager.conditional_orders["conditional-order-1"]
        assert conditional_order_info["order_id"] == "conditional-order-1"
        # According to _setup_conditional_order (line 92), status should be "waiting_for_condition"
        assert conditional_order_info["status"] == "waiting_for_condition"


@pytest.mark.asyncio
async def test_cancel_conditional_order(
    order_manager: OrderManager, sample_order: TradeOrder
) -> None:
    """Test cancelling a conditional order."""
    conditional_order = TradeOrder(
        symbol="BTCUSDT",
        type="conditional_limit",
        side="buy",
        amount=0.1,
        order_id="conditional-order-1",
        status=OrderStatus.PENDING,
        time_in_force="GTC",
        meta={"conditional_price": 49000.0, "conditional_direction": "below"},
    )

    result = {"order_id": "conditional-order-1", "status": "pending"}

    with patch("asyncio.create_task"):
        await order_manager.track_order(conditional_order, result)

        cancelled = order_manager.cancel_order("conditional-order-1")
        assert cancelled is True
        assert len(order_manager.conditional_orders) == 0
        assert len(order_manager.order_history) == 1
        assert order_manager.order_history[0]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_track_order_unknown_status(
    order_manager: OrderManager, sample_order: TradeOrder
) -> None:
    """Test tracking an order with unknown status."""
    result = {
        "order_id": "test-order-1",
        "status": "unknown",
        "fill_price": 50000.0,
    }

    await order_manager.track_order(sample_order, result)

    # Should go to history for unknown status
    assert len(order_manager.order_history) == 1
    assert order_manager.order_history[0]["status"] == "unknown"


@pytest.mark.asyncio
async def test_track_order_with_meta(
    order_manager: OrderManager,
) -> None:
    """Test tracking an order with metadata."""
    order = TradeOrder(
        symbol="BTCUSDT",
        type="market",
        side="buy",
        amount=0.1,
        order_id="test-order-1",
        status=OrderStatus.PENDING,
        time_in_force="GTC",
        meta={"test_key": "test_value"},
    )

    result = {
        "order_id": "test-order-1",
        "status": "filled",
        "fill_price": 50000.0,
    }

    await order_manager.track_order(order, result)

    assert len(order_manager.order_history) == 1
    assert "original_order" in order_manager.order_history[0]
