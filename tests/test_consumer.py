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
    return TradeOrder(
        id="test-order-1",
        symbol="BTCUSDT",
        side="buy",
        type="market",
        quantity=0.1,
        price=45000.0,
        status="pending",
        timestamp=1234567890,
    )


@pytest.mark.asyncio
async def test_consumer_initialization(consumer: SignalConsumer) -> None:
    """Test consumer initialization"""
    assert consumer is not None
    assert hasattr(consumer, "dispatcher")
    assert hasattr(consumer, "initialize")
    assert hasattr(consumer, "start_consuming")


@pytest.mark.asyncio
async def test_consumer_initialize_success(consumer: SignalConsumer) -> None:
    """Test successful NATS initialization"""
    with patch.object(consumer, "dispatcher") as mock_dispatcher:
        mock_dispatcher.initialize = AsyncMock(return_value=None)

        # Mock NATS settings
        with patch("tradeengine.consumer.settings") as mock_settings:
            mock_settings.nats_enabled = True
            mock_settings.nats_servers = "nats://localhost:4222"

            # Mock NATS connection
            with patch("nats.connect") as mock_nats_connect:
                mock_nats_client = AsyncMock()
                mock_nats_connect.return_value = mock_nats_client

                result = await consumer.initialize()

                assert result is True
                mock_dispatcher.initialize.assert_called_once()


@pytest.mark.asyncio
async def test_consumer_initialize_failure(consumer: SignalConsumer) -> None:
    """Test failed NATS initialization"""
    with patch("nats.connect") as mock_nats_connect:
        mock_nats_connect.side_effect = Exception("Connection failed")

        result = await consumer.initialize()

        assert result is False


@pytest.mark.asyncio
async def test_message_handler_success(
    consumer: SignalConsumer, sample_signal: Signal
) -> None:
    """Test successful message handling"""
    with patch.object(consumer, "dispatcher") as mock_dispatcher:
        mock_dispatcher.dispatch = AsyncMock(return_value={"status": "success"})

        # Create a mock message
        mock_msg = AsyncMock()
        mock_msg.data = b'{"strategy_id": "test", "symbol": "BTCUSDT", "signal_type": "buy", "action": "buy", "confidence": 0.8, "strength": "medium", "timeframe": "1h", "price": 45000.0, "quantity": 0.1, "current_price": 45000.0, "source": "test", "strategy": "test-strategy", "timestamp": "2025-07-16T03:19:53.609036"}'
        mock_msg.subject = "test.subject"
        mock_msg.reply = None

        await consumer._message_handler(mock_msg)

        mock_dispatcher.dispatch.assert_called_once()


@pytest.mark.asyncio
async def test_message_handler_error(consumer: SignalConsumer) -> None:
    """Test message handling with error"""
    with patch.object(consumer, "dispatcher") as mock_dispatcher:
        mock_dispatcher.dispatch = AsyncMock(side_effect=Exception("Test error"))

        # Create a mock message
        mock_msg = AsyncMock()
        mock_msg.data = b'{"strategy_id": "test", "symbol": "BTCUSDT", "signal_type": "buy", "action": "buy", "confidence": 0.8, "strength": "medium", "timeframe": "1h", "price": 45000.0, "quantity": 0.1, "current_price": 45000.0, "source": "test", "strategy": "test-strategy", "timestamp": "2025-07-16T03:19:53.609036"}'
        mock_msg.subject = "test.subject"
        mock_msg.reply = None

        await consumer._message_handler(mock_msg)

        mock_dispatcher.dispatch.assert_called_once()


@pytest.mark.asyncio
async def test_consumer_stop_consuming(consumer: SignalConsumer) -> None:
    """Test stopping the consumer"""
    # Mock subscription and NATS client
    mock_subscription = AsyncMock()
    mock_nc = AsyncMock()
    consumer.subscription = mock_subscription
    consumer.nc = mock_nc

    await consumer.stop_consuming()

    assert consumer.running is False
    mock_subscription.unsubscribe.assert_called_once()
    mock_nc.close.assert_called_once()
