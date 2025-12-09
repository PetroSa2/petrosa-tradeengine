"""Tests for fake collaborators used in integration testing."""

import pytest

from contracts.order import OrderStatus, TradeOrder
from tests.integration.fakes import FakeExchange, FakeOrderManager, FakePositionManager


@pytest.fixture
def fake_exchange() -> FakeExchange:
    """Create a fake exchange for testing."""
    return FakeExchange()


@pytest.fixture
def fake_position_manager() -> FakePositionManager:
    """Create a fake position manager for testing."""
    return FakePositionManager()


@pytest.fixture
def fake_order_manager() -> FakeOrderManager:
    """Create a fake order manager for testing."""
    return FakeOrderManager()


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
        position_size_pct=0.1,
        target_price=50000.0,
    )


# ============================================================================
# FakeExchange Tests
# ============================================================================


@pytest.mark.asyncio
async def test_fake_exchange_initialization(fake_exchange: FakeExchange) -> None:
    """Test fake exchange initialization."""
    await fake_exchange.initialize()
    assert len(fake_exchange.get_executed_orders()) == 0


@pytest.mark.asyncio
async def test_fake_exchange_execute_order(
    fake_exchange: FakeExchange, sample_order: TradeOrder
) -> None:
    """Test fake exchange order execution."""
    result = await fake_exchange.execute(sample_order)

    assert result["status"] == "filled"
    assert result["symbol"] == "BTCUSDT"
    assert "order_id" in result
    assert len(fake_exchange.get_executed_orders()) == 1


@pytest.mark.asyncio
async def test_fake_exchange_execute_order_alias(
    fake_exchange: FakeExchange, sample_order: TradeOrder
) -> None:
    """Test fake exchange execute_order alias."""
    result = await fake_exchange.execute_order(sample_order)

    assert result["status"] == "filled"
    assert len(fake_exchange.get_executed_orders()) == 1


@pytest.mark.asyncio
async def test_fake_exchange_cancel_order(
    fake_exchange: FakeExchange, sample_order: TradeOrder
) -> None:
    """Test fake exchange order cancellation."""
    result = await fake_exchange.execute(sample_order)
    order_id = result["order_id"]

    cancel_result = await fake_exchange.cancel_order("BTCUSDT", order_id)

    assert cancel_result["status"] == "CANCELED"
    assert fake_exchange.was_cancelled(order_id)


@pytest.mark.asyncio
async def test_fake_exchange_get_open_orders(
    fake_exchange: FakeExchange, sample_order: TradeOrder
) -> None:
    """Test fake exchange open orders tracking."""
    # Create a limit order (should be tracked as open)
    limit_order = TradeOrder(
        symbol="BTCUSDT",
        type="limit",
        side="buy",
        amount=0.1,
        order_id="limit-order-1",
        status=OrderStatus.PENDING,
        time_in_force="GTC",
        target_price=50000.0,
    )

    await fake_exchange.execute(limit_order)
    open_orders = await fake_exchange.get_open_orders("BTCUSDT")

    assert len(open_orders) == 1
    assert open_orders[0]["orderId"].startswith("fake_order_")


@pytest.mark.asyncio
async def test_fake_exchange_clear_executed_orders(
    fake_exchange: FakeExchange, sample_order: TradeOrder
) -> None:
    """Test fake exchange clearing executed orders."""
    await fake_exchange.execute(sample_order)
    assert len(fake_exchange.get_executed_orders()) == 1

    fake_exchange.clear_executed_orders()
    assert len(fake_exchange.get_executed_orders()) == 0


# ============================================================================
# FakePositionManager Tests
# ============================================================================


@pytest.mark.asyncio
async def test_fake_position_manager_initialization(
    fake_position_manager: FakePositionManager,
) -> None:
    """Test fake position manager initialization."""
    await fake_position_manager.initialize()
    assert len(fake_position_manager.get_positions()) == 0
    assert fake_position_manager.daily_pnl == 0.0


@pytest.mark.asyncio
async def test_fake_position_manager_check_position_limits(
    fake_position_manager: FakePositionManager, sample_order: TradeOrder
) -> None:
    """Test fake position manager position limit checking."""
    # Order should pass limits (0.1 BTC at 50000 = 5000 USD, which is 50% of 10000 portfolio)
    # But max_position_size_pct is 0.1 (10%), so 5000/10000 = 50% > 10% = should fail
    # Actually wait, let me recalculate: 0.1 BTC * 50000 = 5000 USD, portfolio = 10000, so 50% > 10% limit
    # So this should fail the position size check
    passes = await fake_position_manager.check_position_limits(sample_order)
    # This should fail because 5000/10000 = 50% > 10% max position size
    assert not passes


@pytest.mark.asyncio
async def test_fake_position_manager_check_position_limits_small_order(
    fake_position_manager: FakePositionManager,
) -> None:
    """Test fake position manager with small order that passes limits."""
    small_order = TradeOrder(
        symbol="BTCUSDT",
        type="market",
        side="buy",
        amount=0.01,  # 0.01 BTC = 500 USD = 5% of portfolio
        order_id="small-order",
        status=OrderStatus.PENDING,
        time_in_force="GTC",
        target_price=50000.0,
    )

    passes = await fake_position_manager.check_position_limits(small_order)
    # 0.01 * 50000 = 500 USD, which is 5% of 10000 portfolio, < 10% limit
    assert passes


@pytest.mark.asyncio
async def test_fake_position_manager_check_daily_loss_limits(
    fake_position_manager: FakePositionManager,
) -> None:
    """Test fake position manager daily loss limit checking."""
    # Initially should pass (no loss)
    passes = await fake_position_manager.check_daily_loss_limits()
    assert passes

    # Set daily PnL to exceed limit
    fake_position_manager.daily_pnl = -600.0  # -6% of 10000 portfolio, > 5% limit
    passes = await fake_position_manager.check_daily_loss_limits()
    assert not passes


@pytest.mark.asyncio
async def test_fake_position_manager_update_position(
    fake_position_manager: FakePositionManager, sample_order: TradeOrder
) -> None:
    """Test fake position manager position updating."""
    result = {
        "order_id": "test-order-1",
        "status": "filled",
        "fill_price": 50000.0,
        "amount": 0.1,
    }

    await fake_position_manager.update_position(sample_order, result)

    positions = fake_position_manager.get_positions()
    assert len(positions) == 1
    position_key = ("BTCUSDT", "LONG")
    assert position_key in positions
    assert positions[position_key]["quantity"] == 0.1
    assert positions[position_key]["avg_price"] == 50000.0


@pytest.mark.asyncio
async def test_fake_position_manager_set_risk_limits(
    fake_position_manager: FakePositionManager,
) -> None:
    """Test fake position manager risk limit configuration."""
    fake_position_manager.set_risk_limits(
        max_position_size_pct=0.2,
        max_daily_loss_pct=0.1,
        max_portfolio_exposure_pct=0.9,
    )

    assert fake_position_manager.max_position_size_pct == 0.2
    assert fake_position_manager.max_daily_loss_pct == 0.1
    assert fake_position_manager.max_portfolio_exposure_pct == 0.9


# ============================================================================
# FakeOrderManager Tests
# ============================================================================


@pytest.mark.asyncio
async def test_fake_order_manager_initialization(
    fake_order_manager: FakeOrderManager,
) -> None:
    """Test fake order manager initialization."""
    await fake_order_manager.initialize()
    assert len(fake_order_manager.get_active_orders()) == 0
    assert len(fake_order_manager.get_order_history()) == 0


@pytest.mark.asyncio
async def test_fake_order_manager_track_order(
    fake_order_manager: FakeOrderManager, sample_order: TradeOrder
) -> None:
    """Test fake order manager order tracking."""
    result = {
        "order_id": "test-order-1",
        "status": "filled",
        "fill_price": 50000.0,
        "amount": 0.1,
    }

    await fake_order_manager.track_order(sample_order, result)

    history = fake_order_manager.get_order_history()
    assert len(history) == 1
    assert history[0]["order_id"] == "test-order-1"
    assert history[0]["status"] == "filled"


@pytest.mark.asyncio
async def test_fake_order_manager_track_pending_order(
    fake_order_manager: FakeOrderManager, sample_order: TradeOrder
) -> None:
    """Test fake order manager tracking pending orders."""
    result = {
        "order_id": "pending-order-1",
        "status": "pending",
        "amount": 0.1,
    }

    await fake_order_manager.track_order(sample_order, result)

    active_orders = fake_order_manager.get_active_orders()
    assert len(active_orders) == 1
    assert active_orders[0]["order_id"] == "pending-order-1"
    assert len(fake_order_manager.get_order_history()) == 0


@pytest.mark.asyncio
async def test_fake_order_manager_get_order(
    fake_order_manager: FakeOrderManager, sample_order: TradeOrder
) -> None:
    """Test fake order manager retrieving specific order."""
    result = {
        "order_id": "test-order-1",
        "status": "filled",
        "fill_price": 50000.0,
    }

    await fake_order_manager.track_order(sample_order, result)

    order = fake_order_manager.get_order("test-order-1")
    assert order is not None
    assert order["order_id"] == "test-order-1"
    assert order["symbol"] == "BTCUSDT"


@pytest.mark.asyncio
async def test_fake_order_manager_cancel_order(
    fake_order_manager: FakeOrderManager, sample_order: TradeOrder
) -> None:
    """Test fake order manager order cancellation."""
    result = {
        "order_id": "active-order-1",
        "status": "pending",
    }

    await fake_order_manager.track_order(sample_order, result)
    assert len(fake_order_manager.get_active_orders()) == 1

    cancelled = fake_order_manager.cancel_order("active-order-1")
    assert cancelled
    assert len(fake_order_manager.get_active_orders()) == 0
    assert len(fake_order_manager.get_order_history()) == 1
    assert fake_order_manager.get_order_history()[0]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_fake_order_manager_get_order_summary(
    fake_order_manager: FakeOrderManager, sample_order: TradeOrder
) -> None:
    """Test fake order manager order summary."""
    # Track some orders
    await fake_order_manager.track_order(
        sample_order, {"order_id": "order-1", "status": "filled"}
    )
    await fake_order_manager.track_order(
        sample_order, {"order_id": "order-2", "status": "pending"}
    )

    summary = fake_order_manager.get_order_summary()
    assert summary["active_orders"] == 1
    assert summary["total_orders"] == 1  # Only filled orders go to history
    assert "status_distribution" in summary


@pytest.mark.asyncio
async def test_fake_order_manager_get_price(
    fake_order_manager: FakeOrderManager,
) -> None:
    """Test fake order manager price retrieval."""
    btc_price = await fake_order_manager.get_price("BTCUSDT")
    assert btc_price == 50000.0

    eth_price = await fake_order_manager.get_price("ETHUSDT")
    assert eth_price == 3000.0

    unknown_price = await fake_order_manager.get_price("UNKNOWN")
    assert unknown_price == 100.0  # Default price
