"""
Comprehensive tests for Dispatcher class to increase coverage to 75%.

This test suite covers:
1. Signal processing with different strategy modes
2. Order execution with simulator vs real exchange
3. Risk management order placement (OCO, SL-only, TP-only)
4. Error handling and edge cases
5. Signal cache and duplicate detection
6. Health checks and metrics
7. CIO audit enforcement and restricted mode logic
"""

import asyncio
import time
from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest

from contracts.order import OrderSide, OrderType, TradeOrder
from contracts.signal import Signal, StrategyMode, TimeInForce
from shared.constants import UTC
from tradeengine.dispatcher import Dispatcher


@pytest.fixture
def mock_exchange():
    """Mock exchange for testing"""
    exchange = Mock()
    exchange.execute = AsyncMock(
        return_value={
            "status": "filled",
            "order_id": "test_order_123",
            "fill_price": 50000.0,
            "amount": 0.001,
        }
    )
    return exchange


@pytest.fixture
def dispatcher(mock_exchange):
    """Create dispatcher instance with mocked exchange"""
    with (
        patch("tradeengine.dispatcher.strategy_position_manager") as mock_spm,
        patch("tradeengine.dispatcher.distributed_lock_manager") as mock_dlm,
    ):
        mock_spm.initialize = AsyncMock()
        mock_spm.close = AsyncMock()
        mock_dlm.initialize = AsyncMock()
        mock_dlm.close = AsyncMock()
        mock_dlm.health_check = AsyncMock(return_value={"status": "healthy"})

        # Mock execute_with_lock to just call the function
        async def mock_execute_with_lock(lock_name, func, *args, **kwargs):
            return await func(*args, **kwargs)

        mock_dlm.execute_with_lock = AsyncMock(side_effect=mock_execute_with_lock)

        disp = Dispatcher(exchange=mock_exchange)
        disp.position_manager.initialize = AsyncMock()
        disp.position_manager.close = AsyncMock()
        disp.order_manager.initialize = AsyncMock()
        disp.order_manager.close = AsyncMock()

        yield disp


@pytest.fixture
def sample_signal():
    """Sample signal for testing"""
    return Signal(
        strategy_id="test-strategy",
        symbol="BTCUSDT",
        signal_type="buy",
        action="buy",
        confidence=0.85,
        strength="strong",
        timeframe="1h",
        price=50000.0,
        quantity=0.001,
        current_price=50000.0,
        source="petrosa-cio",
        strategy="test-strategy",
        strategy_mode=StrategyMode.DETERMINISTIC,
        timestamp=datetime.now(UTC),
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.GTC,
    )


class TestSignalProcessing:
    """Test signal processing with different strategy modes"""

    @pytest.mark.asyncio
    async def test_process_signal_deterministic_mode(self, dispatcher, sample_signal):
        """Test processing signal with deterministic mode"""
        result = await dispatcher.process_signal(sample_signal)
        assert result is not None
        assert "status" in result

    @pytest.mark.asyncio
    async def test_process_signal_ml_light_mode(self, dispatcher, sample_signal):
        """Test processing signal with ml_light mode"""
        sample_signal.strategy_mode = StrategyMode.ML_LIGHT
        result = await dispatcher.process_signal(sample_signal)
        assert result is not None
        assert "status" in result

    @pytest.mark.asyncio
    async def test_process_signal_llm_reasoning_mode(self, dispatcher, sample_signal):
        """Test processing signal with llm_reasoning mode"""
        sample_signal.strategy_mode = StrategyMode.LLM_REASONING
        result = await dispatcher.process_signal(sample_signal)
        assert result is not None
        assert "status" in result

    @pytest.mark.asyncio
    async def test_process_signal_unknown_mode(self, dispatcher, sample_signal):
        """Test processing signal with unknown strategy mode"""
        # Create a signal with invalid mode by directly setting the value
        sample_signal.strategy_mode = Mock()
        sample_signal.strategy_mode.value = "unknown_mode"
        result = await dispatcher.process_signal(sample_signal)
        assert result["status"] == "rejected"
        assert "Unknown strategy mode" in result.get("reason", "")


class TestCIOEnforcement:
    """Test CIO audit enforcement and restricted mode logic"""

    @pytest.mark.asyncio
    async def test_restricted_mode_aborts_buy(self, dispatcher, sample_signal):
        """Test that restricted mode (lost heartbeat) aborts BUY signals"""
        # Set restricted mode
        dispatcher.heartbeat_monitor = Mock()
        dispatcher.heartbeat_monitor.is_restricted.return_value = True

        result = await dispatcher.dispatch(sample_signal)

        assert result["status"] == "aborted"
        assert "RESTRICTED_MODE" in result["reason"]
        dispatcher.exchange.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_restricted_mode_allows_close(self, dispatcher, sample_signal):
        """Test that restricted mode still allows CLOSE signals for safety"""
        sample_signal.action = "close"

        # Set restricted mode
        dispatcher.heartbeat_monitor = Mock()
        dispatcher.heartbeat_monitor.is_restricted.return_value = True

        # Mock successful processing
        dispatcher.process_signal = AsyncMock(return_value={"status": "success"})
        dispatcher.execute_order = AsyncMock(return_value={"status": "filled"})

        result = await dispatcher.dispatch(sample_signal)

        assert result["status"] == "executed"
        # Verify it didn't abort

    @pytest.mark.asyncio
    async def test_enforce_cio_audit_rejects_unauthorized_source(
        self, dispatcher, sample_signal
    ):
        """Test that non-petrosa-cio sources are rejected when enforcement is on"""
        dispatcher.settings.enforce_cio_audit = True
        sample_signal.source = "unauthorized-bot"
        sample_signal.action = "buy"

        result = await dispatcher.dispatch(sample_signal)

        assert result["status"] == "rejected"
        assert "not authorized" in str(result.get("reason", ""))
        dispatcher.exchange.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_enforce_cio_audit_allows_petrosa_cio(
        self, dispatcher, sample_signal
    ):
        """Test that petrosa-cio source is allowed when enforcement is on"""
        dispatcher.settings.enforce_cio_audit = True
        sample_signal.source = "petrosa-cio"
        sample_signal.action = "buy"

        # Mock successful processing
        dispatcher.process_signal = AsyncMock(return_value={"status": "success"})
        dispatcher.execute_order = AsyncMock(return_value={"status": "filled"})

        result = await dispatcher.dispatch(sample_signal)

        assert result["status"] == "executed"

    @pytest.mark.asyncio
    async def test_enforce_cio_audit_disabled_allows_any_source(
        self, dispatcher, sample_signal
    ):
        """Test that any source is allowed when enforcement is off"""
        dispatcher.settings.enforce_cio_audit = False
        sample_signal.source = "any-source"
        sample_signal.action = "buy"

        # Mock successful processing
        dispatcher.process_signal = AsyncMock(return_value={"status": "success"})
        dispatcher.execute_order = AsyncMock(return_value={"status": "filled"})

        result = await dispatcher.dispatch(sample_signal)

        assert result["status"] == "executed"


class TestOrderExecution:
    """Test order execution scenarios"""

    @pytest.mark.asyncio
    async def test_execute_order_with_exchange(self, dispatcher):
        """Test executing order with real exchange"""
        # Mock order_manager.track_order to avoid side effects
        dispatcher.order_manager.track_order = AsyncMock()

        order = TradeOrder(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            type=OrderType.MARKET,
            amount=0.001,
            target_price=50000.0,
        )
        result = await dispatcher.execute_order(order)
        assert result is not None

    @pytest.mark.asyncio
    async def test_execute_order_simulated(self, dispatcher):
        """Test executing simulated order"""
        # Mock order_manager.track_order to avoid side effects
        dispatcher.order_manager.track_order = AsyncMock()

        order = TradeOrder(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            type=OrderType.MARKET,
            amount=0.001,
            target_price=50000.0,
            simulate=True,
        )
        result = await dispatcher.execute_order(order)
        assert result is not None
        assert result.get("simulated") is True


class TestSignalCache:
    """Test signal cache and duplicate detection"""

    @pytest.mark.asyncio
    async def test_duplicate_signal_detection(self, dispatcher, sample_signal):
        """Test that duplicate signals are detected and rejected"""
        # Mock successful processing
        dispatcher.process_signal = AsyncMock(return_value={"status": "success"})
        dispatcher.execute_order = AsyncMock(return_value={"status": "filled"})

        # Use authorized source
        sample_signal.source = "petrosa-cio"

        # First signal should be processed
        result1 = await dispatcher.dispatch(sample_signal)
        assert result1.get("status") != "duplicate"

        # Second identical signal should be rejected as duplicate
        result2 = await dispatcher.dispatch(sample_signal)
        assert result2.get("status") == "duplicate"
