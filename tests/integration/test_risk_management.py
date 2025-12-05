"""
Integration tests for risk management enforcement.

Tests that PositionManager correctly enforces:
- Position size limits
- Daily loss limits
- Portfolio exposure limits

Uses FakeExchange and FakePositionManager to test actual risk logic
without external dependencies.
"""

from datetime import datetime
from unittest.mock import patch

import pytest

from contracts.signal import Signal
from tests.integration.fakes import FakeExchange, FakePositionManager
from tradeengine.dispatcher import Dispatcher


@pytest.fixture
def dispatcher_with_risk_limits():
    """Create dispatcher with configurable risk limits."""
    fake_exchange = FakeExchange()
    fake_position_mgr = FakePositionManager(
        max_position_size_pct=0.1,  # 10% max position size
        max_daily_loss_pct=0.05,  # 5% max daily loss
        max_portfolio_exposure_pct=0.8,  # 80% max portfolio exposure
    )

    dispatcher = Dispatcher(exchange=fake_exchange)
    dispatcher.position_manager = fake_position_mgr

    return dispatcher, fake_exchange, fake_position_mgr


@pytest.fixture(autouse=True)
def mock_distributed_lock():
    """Mock distributed lock manager to always succeed (no MongoDB needed).

    According to trade engine integration test guidelines, distributed locks
    should be mocked to avoid requiring MongoDB connections in tests.
    """

    async def mock_execute_with_lock(lock_key, func, *args, **kwargs):
        """Mock lock execution - just call the function directly."""
        return await func(*args, **kwargs)

    with patch(
        "tradeengine.dispatcher.distributed_lock_manager.execute_with_lock",
        side_effect=mock_execute_with_lock,
    ):
        yield


@pytest.mark.integration
@pytest.mark.asyncio
async def test_position_size_limit_exceeded(dispatcher_with_risk_limits):
    """Test that order exceeding position size limit is rejected."""
    dispatcher, fake_exchange, fake_position_mgr = dispatcher_with_risk_limits

    # Set portfolio value
    fake_position_mgr.total_portfolio_value = 10000.0

    # Max position is 10% = $1000
    # Try to create position > $1000
    signal = Signal(
        strategy_id="test-risk-position-size",
        symbol="BTCUSDT",
        action="buy",
        signal_type="buy",
        confidence=0.85,
        strength="strong",
        timeframe="1h",
        price=50000.0,
        quantity=0.03,  # $1500 position (exceeds 10% limit)
        current_price=50000.0,
        timestamp=datetime.utcnow(),
        source="test",
        strategy="test-strategy",
    )

    # Process signal
    result = await dispatcher.dispatch(signal)

    # Assertions - check execution_result for risk rejection
    execution_result = result.get("execution_result", {})
    assert (
        execution_result.get("status") == "rejected"
    ), f"Expected rejected status, got {execution_result.get('status')}"
    assert (
        "risk" in execution_result.get("reason", "").lower()
        or "limit" in execution_result.get("reason", "").lower()
    )

    # Verify no order was executed
    assert len(fake_exchange.get_executed_orders()) == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_daily_loss_limit_exceeded(dispatcher_with_risk_limits):
    """Test that order is rejected when daily loss limit exceeded."""
    dispatcher, fake_exchange, fake_position_mgr = dispatcher_with_risk_limits

    # Set portfolio value and daily P&L
    fake_position_mgr.total_portfolio_value = 10000.0
    fake_position_mgr.daily_pnl = -600.0  # Already lost $600 (6% loss > 5% limit)

    signal = Signal(
        strategy_id="test-risk-daily-loss",
        symbol="BTCUSDT",
        action="buy",
        signal_type="buy",
        confidence=0.85,
        strength="strong",
        timeframe="1h",
        price=50000.0,
        quantity=0.001,
        current_price=50000.0,
        timestamp=datetime.utcnow(),
        source="test",
        strategy="test-strategy",
    )

    # Process signal
    result = await dispatcher.dispatch(signal)

    # Assertions - check execution_result for risk rejection
    execution_result = result.get("execution_result", {})
    assert (
        execution_result.get("status") == "rejected"
    ), f"Expected rejected status, got {execution_result.get('status')}"
    assert (
        "daily loss" in execution_result.get("reason", "").lower()
        or "loss limit" in execution_result.get("reason", "").lower()
    )

    # Verify no order was executed
    assert len(fake_exchange.get_executed_orders()) == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_portfolio_exposure_limit_exceeded(dispatcher_with_risk_limits):
    """Test that order is rejected when portfolio exposure limit exceeded."""
    dispatcher, fake_exchange, fake_position_mgr = dispatcher_with_risk_limits

    # Set portfolio value
    fake_position_mgr.total_portfolio_value = 10000.0

    # Add existing positions totaling $8500 (85% exposure)
    fake_position_mgr.positions = {
        ("BTCUSDT", "LONG"): {
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "quantity": 0.15,
            "avg_price": 50000.0,
            "total_value": 7500.0,
        },
        ("ETHUSDT", "LONG"): {
            "symbol": "ETHUSDT",
            "position_side": "LONG",
            "quantity": 0.333,
            "avg_price": 3000.0,
            "total_value": 1000.0,
        },
    }

    # Max exposure is 80% = $8000
    # Current exposure is $8500 (already exceeds)
    # Try to add another position
    signal = Signal(
        strategy_id="test-risk-portfolio-exposure",
        symbol="ADAUSDT",
        action="buy",
        signal_type="buy",
        confidence=0.85,
        strength="strong",
        timeframe="1h",
        price=0.50,
        quantity=200,  # $100 position
        current_price=0.50,
        timestamp=datetime.utcnow(),
        source="test",
        strategy="test-strategy",
    )

    # Process signal
    result = await dispatcher.dispatch(signal)

    # Assertions - check execution_result for risk rejection
    execution_result = result.get("execution_result", {})
    assert (
        execution_result.get("status") == "rejected"
    ), f"Expected rejected status, got {execution_result.get('status')}"
    assert (
        "risk" in execution_result.get("reason", "").lower()
        or "exposure" in execution_result.get("reason", "").lower()
    )

    # Verify no order was executed
    assert len(fake_exchange.get_executed_orders()) == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_valid_order_within_all_limits(dispatcher_with_risk_limits):
    """Test that valid order within all limits is accepted."""
    dispatcher, fake_exchange, fake_position_mgr = dispatcher_with_risk_limits

    # Set portfolio value
    fake_position_mgr.total_portfolio_value = 10000.0
    fake_position_mgr.daily_pnl = 100.0  # Positive P&L (within limits)

    # Small position well within limits
    signal = Signal(
        strategy_id="test-risk-valid",
        symbol="BTCUSDT",
        action="buy",
        signal_type="buy",
        confidence=0.85,
        strength="strong",
        timeframe="1h",
        price=50000.0,
        quantity=0.001,  # $50 position (0.5% of portfolio)
        current_price=50000.0,
        timestamp=datetime.utcnow(),
        source="test",
        strategy="test-strategy",
    )

    # Process signal
    result = await dispatcher.dispatch(signal)

    # Assertions
    assert result["status"] != "rejected"

    # Verify order was executed
    assert len(fake_exchange.get_executed_orders()) == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_risk_rejection_metric_increments(dispatcher_with_risk_limits):
    """Test that risk_rejections_total metric increments correctly."""
    dispatcher, fake_exchange, fake_position_mgr = dispatcher_with_risk_limits

    from prometheus_client import REGISTRY

    # Set up scenario that will violate risk limit
    fake_position_mgr.total_portfolio_value = 10000.0
    fake_position_mgr.daily_pnl = -600.0  # Exceeds daily loss limit

    # Get initial metric value using public API
    initial_count = (
        REGISTRY.get_sample_value(
            "tradeengine_risk_rejections_total",
            labels={
                "reason": "daily_loss_limits_exceeded",
                "symbol": "BTCUSDT",
                "exchange": "binance",
            },
        )
        or 0
    )

    signal = Signal(
        strategy_id="test-risk-metric",
        symbol="BTCUSDT",
        action="buy",
        signal_type="buy",
        confidence=0.85,
        strength="strong",
        timeframe="1h",
        price=50000.0,
        quantity=0.001,
        current_price=50000.0,
        timestamp=datetime.utcnow(),
        source="test",
        strategy="test-strategy",
    )

    # Process signal (should be rejected)
    await dispatcher.dispatch(signal)

    # Check metric increased using public API
    final_count = (
        REGISTRY.get_sample_value(
            "tradeengine_risk_rejections_total",
            labels={
                "reason": "daily_loss_limits_exceeded",
                "symbol": "BTCUSDT",
                "exchange": "binance",
            },
        )
        or 0
    )

    # Verify metric actually incremented (not just >= which allows no change)
    assert (
        final_count > initial_count
    ), f"Expected metric to increment from {initial_count} to {final_count}, but it did not increase"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_multiple_orders_accumulate_towards_limits(dispatcher_with_risk_limits):
    """Test that multiple orders correctly accumulate exposure."""
    dispatcher, fake_exchange, fake_position_mgr = dispatcher_with_risk_limits

    fake_position_mgr.total_portfolio_value = 10000.0

    # First order: 8% of portfolio ($800)
    signal1 = Signal(
        strategy_id="test-accumulate-1",
        symbol="BTCUSDT",
        action="buy",
        signal_type="buy",
        confidence=0.85,
        strength="strong",
        timeframe="1h",
        price=50000.0,
        quantity=0.016,  # $800
        current_price=50000.0,
        timestamp=datetime.utcnow(),
        source="test",
        strategy="test-strategy",
    )

    # Second order: 8% of portfolio ($800)
    signal2 = Signal(
        strategy_id="test-accumulate-2",
        symbol="ETHUSDT",
        action="buy",
        signal_type="buy",
        confidence=0.85,
        strength="strong",
        timeframe="1h",
        price=3000.0,
        quantity=0.267,  # $800
        current_price=3000.0,
        timestamp=datetime.utcnow(),
        source="test",
        strategy="test-strategy",
    )

    # Process both orders
    result1 = await dispatcher.dispatch(signal1)
    result2 = await dispatcher.dispatch(signal2)

    # Both should succeed (total 16% < 80% portfolio limit)
    assert result1["status"] != "rejected"
    assert result2["status"] != "rejected"

    # Verify both orders executed
    assert len(fake_exchange.get_executed_orders()) == 2
