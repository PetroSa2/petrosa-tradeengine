from unittest.mock import patch

import pytest

from contracts.signal import Signal
from tradeengine.consumer import SignalConsumer


@pytest.fixture
def consumer() -> SignalConsumer:
    return SignalConsumer()


@pytest.fixture
def sample_signal() -> Signal:
    return Signal(
        strategy_id="test-strategy-1",
        symbol="BTCUSDT",
        action="buy",
        confidence=0.8,
        strength="medium",
        timeframe="1h",
        current_price=45000.0,
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
        mock_dispatcher.process_signal.return_value = {
            "status": "executed",
            "order_id": "test-order-1",
        }

        result = await consumer.process_signal(sample_signal)
        assert result["status"] == "executed"
        assert result["order_id"] == "test-order-1"


@pytest.mark.asyncio
async def test_process_signal_error(
    consumer: SignalConsumer, sample_signal: Signal
) -> None:
    """Test signal processing with error"""
    with patch.object(consumer, "dispatcher") as mock_dispatcher:
        mock_dispatcher.process_signal.side_effect = Exception("Test error")

        result = await consumer.process_signal(sample_signal)
        assert result["status"] == "error"
        assert "error" in result


@pytest.mark.asyncio
async def test_validate_signal_valid(
    consumer: SignalConsumer, sample_signal: Signal
) -> None:
    """Test signal validation with valid signal"""
    is_valid = consumer.validate_signal(sample_signal)
    assert is_valid is True


@pytest.mark.asyncio
async def test_validate_signal_invalid_confidence(consumer: SignalConsumer) -> None:
    """Test signal validation with invalid confidence"""
    invalid_signal = Signal(
        strategy_id="test-strategy-1",
        symbol="BTCUSDT",
        action="buy",
        confidence=1.5,  # Invalid confidence > 1
        strength="medium",
        timeframe="1h",
        current_price=45000.0,
    )

    is_valid = consumer.validate_signal(invalid_signal)
    assert is_valid is False


@pytest.mark.asyncio
async def test_validate_signal_invalid_price(consumer: SignalConsumer) -> None:
    """Test signal validation with invalid price"""
    invalid_signal = Signal(
        strategy_id="test-strategy-1",
        symbol="BTCUSDT",
        action="buy",
        confidence=0.8,
        strength="medium",
        timeframe="1h",
        current_price=-100.0,  # Invalid negative price
    )

    is_valid = consumer.validate_signal(invalid_signal)
    assert is_valid is False


@pytest.mark.asyncio
async def test_validate_signal_invalid_quantity(consumer: SignalConsumer) -> None:
    """Test signal validation with invalid quantity"""
    invalid_signal = Signal(
        strategy_id="test-strategy-1",
        symbol="BTCUSDT",
        action="buy",
        confidence=0.8,
        strength="medium",
        timeframe="1h",
        current_price=45000.0,
        # Note: quantity is not a required field in the new model, so this test may need to be rethought
    )

    is_valid = consumer.validate_signal(invalid_signal)
    assert is_valid is False
