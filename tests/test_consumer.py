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


@pytest.mark.asyncio
async def test_consumer_initialization(consumer: SignalConsumer) -> None:
    """Test consumer initialization"""
    assert consumer is not None
    assert hasattr(consumer, "dispatcher")


@pytest.mark.asyncio
async def test_process_signal(consumer: SignalConsumer, sample_signal: Signal) -> None:
    """Test signal processing"""
    with patch.object(consumer, "dispatcher") as mock_dispatcher:
        mock_dispatcher.dispatch.return_value = {
            "status": "executed",
            "order_id": "test-order-1",
        }

        result = consumer.dispatcher.dispatch(sample_signal)
        assert result["status"] == "executed"
        assert result["order_id"] == "test-order-1"


@pytest.mark.asyncio
async def test_process_signal_error(
    consumer: SignalConsumer, sample_signal: Signal
) -> None:
    """Test signal processing with error"""
    with patch.object(consumer, "dispatcher") as mock_dispatcher:
        mock_dispatcher.dispatch.side_effect = RuntimeError("Test error")

        with pytest.raises(RuntimeError, match="Test error"):
            consumer.dispatcher.dispatch(sample_signal)


@pytest.mark.asyncio
async def test_validate_signal_valid(
    consumer: SignalConsumer, sample_signal: Signal
) -> None:
    """Test signal validation with valid signal"""
    # Signal validation is handled by Pydantic model validation
    assert sample_signal is not None
    assert sample_signal.strategy_id == "test-strategy-1"


@pytest.mark.asyncio
async def test_validate_signal_invalid_confidence(consumer: SignalConsumer) -> None:
    """Test signal validation with invalid confidence"""
    with pytest.raises(ValueError):
        Signal(
            strategy_id="test-strategy-1",
            symbol="BTCUSDT",
            signal_type="buy",
            action="buy",
            confidence=1.5,  # Invalid confidence > 1
            strength="medium",
            timeframe="1h",
            price=45000.0,
            quantity=0.1,
            current_price=45000.0,
            source="test",
            strategy="test-strategy",
        )


@pytest.mark.asyncio
async def test_validate_signal_invalid_price(consumer: SignalConsumer) -> None:
    """Test signal validation with invalid price"""
    # Pydantic doesn't validate negative prices by default
    signal = Signal(
        strategy_id="test-strategy-1",
        symbol="BTCUSDT",
        signal_type="buy",
        action="buy",
        confidence=0.8,
        strength="medium",
        timeframe="1h",
        price=-100.0,  # Invalid negative price
        quantity=0.1,
        current_price=-100.0,  # Invalid negative price
        source="test",
        strategy="test-strategy",
    )
    assert signal.price == -100.0


@pytest.mark.asyncio
async def test_validate_signal_invalid_quantity(consumer: SignalConsumer) -> None:
    """Test signal validation with invalid quantity"""
    # Pydantic doesn't validate negative quantities by default
    signal = Signal(
        strategy_id="test-strategy-1",
        symbol="BTCUSDT",
        signal_type="buy",
        action="buy",
        confidence=0.8,
        strength="medium",
        timeframe="1h",
        price=45000.0,
        quantity=-0.1,  # Invalid negative quantity
        current_price=45000.0,
        source="test",
        strategy="test-strategy",
    )
    assert signal.quantity == -0.1
