"""Test consumer module"""

from unittest.mock import AsyncMock, patch

import pytest

from contracts.order import TradeOrder
from contracts.signal import Signal
from tradeengine.consumer import SignalConsumer


@pytest.fixture
def consumer() -> SignalConsumer:
    """Create a test consumer instance"""
    return SignalConsumer()


@pytest.fixture
def sample_signal() -> Signal:
    """Create a sample signal for testing"""
    return Signal(
        strategy_id="test-strategy-1",
        symbol="BTCUSDT",
        signal_type="buy",
        action="buy",
        confidence=0.8,
        strength="medium",
        timeframe="1h",
        price=45000.0,
        quantity=0.1,
        current_price=45000.0,
        source="test",
        strategy="test-strategy",
    )


@pytest.fixture
def sample_order() -> TradeOrder:
    """Create a sample order for testing"""
    from contracts.order import OrderStatus

    return TradeOrder(
        id="test-order-1",
        symbol="BTCUSDT",
        side="buy",
        type="market",
        quantity=0.1,
        price=45000.0,
        status=OrderStatus.PENDING,
        timestamp=1234567890,
    )


@pytest.mark.asyncio
async def test_consumer_initialization(consumer: SignalConsumer) -> None:
    """Test consumer initialization"""
    assert consumer is not None
    assert hasattr(consumer, "process_signal")


@pytest.mark.asyncio
async def test_process_signal(consumer: SignalConsumer, sample_signal: Signal) -> None:
    """Test signal processing"""
    with patch.object(consumer, "dispatcher") as mock_dispatcher:
        mock_dispatcher.dispatch = AsyncMock(return_value={"status": "success"})

        result = await consumer.process_signal(sample_signal)  # type: ignore[attr-defined]

        assert result["status"] == "success"
        mock_dispatcher.dispatch.assert_called_once_with(sample_signal)


@pytest.mark.asyncio
async def test_process_signal_error(
    consumer: SignalConsumer, sample_signal: Signal
) -> None:
    """Test signal processing with error"""
    with patch.object(consumer, "dispatcher") as mock_dispatcher:
        mock_dispatcher.dispatch = AsyncMock(side_effect=Exception("Test error"))

        result = await consumer.process_signal(sample_signal)  # type: ignore[attr-defined]

        assert result["status"] == "error"
        assert "Test error" in result["message"]


@pytest.mark.asyncio
async def test_validate_signal_valid(
    consumer: SignalConsumer, sample_signal: Signal
) -> None:
    """Test signal validation with valid signal"""
    result = consumer.validate_signal(sample_signal)  # type: ignore[attr-defined]
    assert result is True


@pytest.mark.asyncio
async def test_validate_signal_invalid_confidence(consumer: SignalConsumer) -> None:
    """Test signal validation with invalid confidence"""
    invalid_signal = Signal(
        strategy_id="test-strategy-1",
        symbol="BTCUSDT",
        signal_type="buy",
        action="buy",
        confidence=1.5,  # Invalid confidence > 1.0
        strength="medium",
        timeframe="1h",
        price=45000.0,
        quantity=0.1,
        current_price=45000.0,
        source="test",
        strategy="test-strategy",
    )

    result = consumer.validate_signal(invalid_signal)  # type: ignore[attr-defined]
    assert result is False


@pytest.mark.asyncio
async def test_validate_signal_invalid_price(consumer: SignalConsumer) -> None:
    """Test signal validation with invalid price"""
    invalid_signal = Signal(
        strategy_id="test-strategy-1",
        symbol="BTCUSDT",
        signal_type="buy",
        action="buy",
        confidence=0.8,
        strength="medium",
        timeframe="1h",
        price=-100.0,  # Invalid negative price
        quantity=0.1,
        current_price=45000.0,
        source="test",
        strategy="test-strategy",
    )

    result = consumer.validate_signal(invalid_signal)  # type: ignore[attr-defined]
    assert result is False


@pytest.mark.asyncio
async def test_validate_signal_invalid_quantity(consumer: SignalConsumer) -> None:
    """Test signal validation with invalid quantity"""
    invalid_signal = Signal(
        strategy_id="test-strategy-1",
        symbol="BTCUSDT",
        signal_type="buy",
        action="buy",
        confidence=0.8,
        strength="medium",
        timeframe="1h",
        price=45000.0,
        quantity=0.0,  # Invalid zero quantity
        current_price=45000.0,
        source="test",
        strategy="test-strategy",
    )

    result = consumer.validate_signal(invalid_signal)  # type: ignore[attr-defined]
    assert result is False
