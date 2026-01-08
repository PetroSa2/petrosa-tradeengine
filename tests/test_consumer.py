"""Test consumer module"""

import asyncio
import json

# Mock petrosa_otel before importing consumer
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.modules["petrosa_otel"] = MagicMock()
from petrosa_otel import extract_trace_context  # noqa: E402

extract_trace_context = MagicMock(return_value=None)

from contracts.order import TradeOrder  # noqa: E402
from contracts.signal import Signal  # noqa: E402
from tradeengine.consumer import SignalConsumer  # noqa: E402


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


class TestConsumerInitialization:
    """Test consumer initialization scenarios"""

    @pytest.mark.asyncio
    async def test_initialize_with_provided_dispatcher(
        self, consumer: SignalConsumer
    ) -> None:
        """Test initialization with provided dispatcher"""
        mock_dispatcher = AsyncMock()
        mock_dispatcher.initialize = AsyncMock()

        with patch("tradeengine.consumer.settings") as mock_settings:
            mock_settings.nats_enabled = True
            mock_settings.nats_servers = "nats://localhost:4222"

            with patch("nats.connect") as mock_nats_connect:
                mock_nats_client = AsyncMock()
                mock_nats_connect.return_value = mock_nats_client

                result = await consumer.initialize(dispatcher=mock_dispatcher)

                assert result is True
                assert consumer.dispatcher == mock_dispatcher
                # Should not call initialize on provided dispatcher
                mock_dispatcher.initialize.assert_not_called()

    @pytest.mark.asyncio
    async def test_initialize_nats_disabled(self, consumer: SignalConsumer) -> None:
        """Test initialization when NATS is disabled"""
        with patch("tradeengine.consumer.settings") as mock_settings:
            mock_settings.nats_enabled = False

            result = await consumer.initialize()

            assert result is False

    @pytest.mark.asyncio
    async def test_initialize_no_servers(self, consumer: SignalConsumer) -> None:
        """Test initialization when no NATS servers configured"""
        with patch("tradeengine.consumer.settings") as mock_settings:
            mock_settings.nats_enabled = True
            mock_settings.nats_servers = None

            result = await consumer.initialize()

            assert result is False

    @pytest.mark.asyncio
    async def test_initialize_creates_dispatcher(
        self, consumer: SignalConsumer
    ) -> None:
        """Test initialization creates dispatcher if not provided"""
        with patch("tradeengine.consumer.settings") as mock_settings:
            mock_settings.nats_enabled = True
            mock_settings.nats_servers = "nats://localhost:4222"

            with patch("nats.connect") as mock_nats_connect:
                mock_nats_client = AsyncMock()
                mock_nats_connect.return_value = mock_nats_client

                with patch("tradeengine.consumer.Dispatcher") as mock_dispatcher_class:
                    mock_dispatcher = AsyncMock()
                    mock_dispatcher.initialize = AsyncMock()
                    mock_dispatcher_class.return_value = mock_dispatcher

                    result = await consumer.initialize()

                    assert result is True
                    assert consumer.dispatcher is not None
                    mock_dispatcher.initialize.assert_called_once()


class TestConsumerStartConsuming:
    """Test start_consuming scenarios"""

    @pytest.mark.asyncio
    async def test_start_consuming_nats_disabled(
        self, consumer: SignalConsumer
    ) -> None:
        """Test start_consuming when NATS is disabled"""
        with patch("tradeengine.consumer.settings") as mock_settings:
            mock_settings.nats_enabled = False

            await consumer.start_consuming()

            assert consumer.running is False

    @pytest.mark.asyncio
    async def test_start_consuming_initializes_if_needed(
        self, consumer: SignalConsumer
    ) -> None:
        """Test start_consuming initializes if not already initialized"""
        with patch("tradeengine.consumer.settings") as mock_settings:
            mock_settings.nats_enabled = True
            mock_settings.nats_servers = "nats://localhost:4222"
            mock_settings.nats_signal_subject = "signals.trading"

            with patch("nats.connect") as mock_nats_connect:
                mock_nats_client = AsyncMock()
                mock_nats_client.subscribe = AsyncMock(return_value=AsyncMock())
                mock_nats_client.is_connected = True
                mock_nats_connect.return_value = mock_nats_client

                with patch.object(consumer, "stop_consuming", new_callable=AsyncMock):
                    # Start consuming in background and stop quickly
                    consumer.running = True
                    task = asyncio.create_task(consumer.start_consuming())
                    await asyncio.sleep(0.1)
                    consumer.running = False
                    await asyncio.sleep(0.1)
                    await task

                assert consumer.nc is not None

    @pytest.mark.asyncio
    async def test_start_consuming_handles_exception(
        self, consumer: SignalConsumer
    ) -> None:
        """Test start_consuming handles exceptions"""
        consumer.nc = AsyncMock()
        consumer.nc.subscribe = AsyncMock(side_effect=Exception("Subscribe failed"))

        with patch("tradeengine.consumer.settings") as mock_settings:
            mock_settings.nats_enabled = True
            mock_settings.nats_signal_subject = "signals.trading"

            with patch.object(
                consumer, "stop_consuming", new_callable=AsyncMock
            ) as mock_stop:
                await consumer.start_consuming()

                mock_stop.assert_called_once()


class TestMessageHandler:
    """Test message handler scenarios"""

    @pytest.mark.asyncio
    async def test_message_handler_json_decode_error(
        self, consumer: SignalConsumer
    ) -> None:
        """Test message handler with invalid JSON"""
        mock_msg = AsyncMock()
        mock_msg.data = b"invalid json"
        mock_msg.subject = "test.subject"
        mock_msg.reply = None

        await consumer._message_handler(mock_msg)

        # Should not raise exception, just log error

    @pytest.mark.asyncio
    async def test_message_handler_missing_timestamp(
        self, consumer: SignalConsumer
    ) -> None:
        """Test message handler with missing timestamp"""
        mock_dispatcher = AsyncMock()
        mock_dispatcher.dispatch = AsyncMock(return_value={"status": "success"})
        consumer.dispatcher = mock_dispatcher

        mock_msg = AsyncMock()
        signal_data = {
            "strategy_id": "test",
            "symbol": "BTCUSDT",
            "signal_type": "buy",
            "action": "buy",
            "confidence": 0.8,
            "strength": "medium",
            "timeframe": "1h",
            "price": 45000.0,
            "quantity": 0.1,
            "current_price": 45000.0,
            "source": "test",
            "strategy": "test-strategy",
            # No timestamp
        }
        mock_msg.data = json.dumps(signal_data).encode()
        mock_msg.subject = "test.subject"
        mock_msg.reply = None

        await consumer._message_handler(mock_msg)

        mock_dispatcher.dispatch.assert_called_once()

    @pytest.mark.asyncio
    async def test_message_handler_invalid_timestamp(
        self, consumer: SignalConsumer
    ) -> None:
        """Test message handler with invalid timestamp format"""
        mock_dispatcher = AsyncMock()
        mock_dispatcher.dispatch = AsyncMock(return_value={"status": "success"})
        consumer.dispatcher = mock_dispatcher

        mock_msg = AsyncMock()
        signal_data = {
            "strategy_id": "test",
            "symbol": "BTCUSDT",
            "signal_type": "buy",
            "action": "buy",
            "confidence": 0.8,
            "strength": "medium",
            "timeframe": "1h",
            "price": 45000.0,
            "quantity": 0.1,
            "current_price": 45000.0,
            "source": "test",
            "strategy": "test-strategy",
            "timestamp": "invalid-timestamp",
        }
        mock_msg.data = json.dumps(signal_data).encode()
        mock_msg.subject = "test.subject"
        mock_msg.reply = None

        await consumer._message_handler(mock_msg)

        mock_dispatcher.dispatch.assert_called_once()

    @pytest.mark.asyncio
    async def test_message_handler_with_trace_context(
        self, consumer: SignalConsumer
    ) -> None:
        """Test message handler with trace context"""
        mock_dispatcher = AsyncMock()
        mock_dispatcher.dispatch = AsyncMock(return_value={"status": "success"})
        consumer.dispatcher = mock_dispatcher

        mock_msg = AsyncMock()
        signal_data = {
            "strategy_id": "test",
            "symbol": "BTCUSDT",
            "signal_type": "buy",
            "action": "buy",
            "confidence": 0.8,
            "strength": "medium",
            "timeframe": "1h",
            "price": 45000.0,
            "quantity": 0.1,
            "current_price": 45000.0,
            "source": "test",
            "strategy": "test-strategy",
            "timestamp": "2025-07-16T03:19:53.609036",
            "_otel_trace_context": {"trace_id": "123", "span_id": "456"},
        }
        mock_msg.data = json.dumps(signal_data).encode()
        mock_msg.subject = "test.subject"
        mock_msg.reply = None

        await consumer._message_handler(mock_msg)

        mock_dispatcher.dispatch.assert_called_once()

    @pytest.mark.asyncio
    async def test_message_handler_with_legacy_trace_headers(
        self, consumer: SignalConsumer
    ) -> None:
        """Test message handler with legacy trace headers"""
        mock_dispatcher = AsyncMock()
        mock_dispatcher.dispatch = AsyncMock(return_value={"status": "success"})
        consumer.dispatcher = mock_dispatcher

        mock_msg = AsyncMock()
        signal_data = {
            "strategy_id": "test",
            "symbol": "BTCUSDT",
            "signal_type": "buy",
            "action": "buy",
            "confidence": 0.8,
            "strength": "medium",
            "timeframe": "1h",
            "price": 45000.0,
            "quantity": 0.1,
            "current_price": 45000.0,
            "source": "test",
            "strategy": "test-strategy",
            "timestamp": "2025-07-16T03:19:53.609036",
            "_otel_trace_headers": {"traceparent": "00-123-456-01"},
        }
        mock_msg.data = json.dumps(signal_data).encode()
        mock_msg.subject = "test.subject"
        mock_msg.reply = None

        await consumer._message_handler(mock_msg)

        mock_dispatcher.dispatch.assert_called_once()

    @pytest.mark.asyncio
    async def test_message_handler_with_reply(self, consumer: SignalConsumer) -> None:
        """Test message handler with reply subject"""
        mock_dispatcher = AsyncMock()
        mock_dispatcher.dispatch = AsyncMock(return_value={"status": "success"})
        consumer.dispatcher = mock_dispatcher

        mock_nc = AsyncMock()
        mock_nc.publish = AsyncMock()
        consumer.nc = mock_nc

        mock_msg = AsyncMock()
        signal_data = {
            "strategy_id": "test",
            "symbol": "BTCUSDT",
            "signal_type": "buy",
            "action": "buy",
            "confidence": 0.8,
            "strength": "medium",
            "timeframe": "1h",
            "price": 45000.0,
            "quantity": 0.1,
            "current_price": 45000.0,
            "source": "test",
            "strategy": "test-strategy",
            "timestamp": "2025-07-16T03:19:53.609036",
        }
        mock_msg.data = json.dumps(signal_data).encode()
        mock_msg.subject = "test.subject"
        mock_msg.reply = "reply.subject"

        await consumer._message_handler(mock_msg)

        mock_dispatcher.dispatch.assert_called_once()
        mock_nc.publish.assert_called_once()

    @pytest.mark.asyncio
    async def test_message_handler_no_dispatcher(
        self, consumer: SignalConsumer
    ) -> None:
        """Test message handler when dispatcher is not initialized"""
        consumer.dispatcher = None

        mock_msg = AsyncMock()
        signal_data = {
            "strategy_id": "test",
            "symbol": "BTCUSDT",
            "signal_type": "buy",
            "action": "buy",
            "confidence": 0.8,
            "strength": "medium",
            "timeframe": "1h",
            "price": 45000.0,
            "quantity": 0.1,
            "current_price": 45000.0,
            "source": "test",
            "strategy": "test-strategy",
            "timestamp": "2025-07-16T03:19:53.609036",
        }
        mock_msg.data = json.dumps(signal_data).encode()
        mock_msg.subject = "test.subject"
        mock_msg.reply = None

        await consumer._message_handler(mock_msg)

        # Should handle error gracefully

    @pytest.mark.asyncio
    async def test_message_handler_ack_timeout(self, consumer: SignalConsumer) -> None:
        """Test message handler with ACK timeout"""
        mock_dispatcher = AsyncMock()
        mock_dispatcher.dispatch = AsyncMock(return_value={"status": "success"})
        consumer.dispatcher = mock_dispatcher

        mock_nc = AsyncMock()
        mock_nc.publish = AsyncMock(side_effect=asyncio.TimeoutError())
        consumer.nc = mock_nc

        mock_msg = AsyncMock()
        signal_data = {
            "strategy_id": "test",
            "symbol": "BTCUSDT",
            "signal_type": "buy",
            "action": "buy",
            "confidence": 0.8,
            "strength": "medium",
            "timeframe": "1h",
            "price": 45000.0,
            "quantity": 0.1,
            "current_price": 45000.0,
            "source": "test",
            "strategy": "test-strategy",
            "timestamp": "2025-07-16T03:19:53.609036",
        }
        mock_msg.data = json.dumps(signal_data).encode()
        mock_msg.subject = "test.subject"
        mock_msg.reply = "reply.subject"

        await consumer._message_handler(mock_msg)

        mock_dispatcher.dispatch.assert_called_once()

    @pytest.mark.asyncio
    async def test_message_handler_error_with_reply(
        self, consumer: SignalConsumer
    ) -> None:
        """Test message handler sends error response on failure"""
        mock_dispatcher = AsyncMock()
        mock_dispatcher.dispatch = AsyncMock(side_effect=Exception("Dispatch failed"))
        consumer.dispatcher = mock_dispatcher

        mock_nc = AsyncMock()
        mock_nc.publish = AsyncMock()
        consumer.nc = mock_nc

        mock_msg = AsyncMock()
        signal_data = {
            "strategy_id": "test",
            "symbol": "BTCUSDT",
            "signal_type": "buy",
            "action": "buy",
            "confidence": 0.8,
            "strength": "medium",
            "timeframe": "1h",
            "price": 45000.0,
            "quantity": 0.1,
            "current_price": 45000.0,
            "source": "test",
            "strategy": "test-strategy",
            "timestamp": "2025-07-16T03:19:53.609036",
        }
        mock_msg.data = json.dumps(signal_data).encode()
        mock_msg.subject = "test.subject"
        mock_msg.reply = "reply.subject"

        await consumer._message_handler(mock_msg)

        # Should have attempted to send error response
        assert mock_nc.publish.called


class TestConsumerStopConsuming:
    """Test stop_consuming scenarios"""

    @pytest.mark.asyncio
    async def test_stop_consuming_no_subscription(
        self, consumer: SignalConsumer
    ) -> None:
        """Test stop_consuming when no subscription exists"""
        consumer.running = True
        consumer.subscription = None
        consumer.nc = AsyncMock()

        await consumer.stop_consuming()

        assert consumer.running is False

    @pytest.mark.asyncio
    async def test_stop_consuming_no_nc(self, consumer: SignalConsumer) -> None:
        """Test stop_consuming when no NATS client exists"""
        consumer.running = True
        consumer.subscription = AsyncMock()
        consumer.nc = None

        await consumer.stop_consuming()

        assert consumer.running is False


class TestRunConsumer:
    """Test run_consumer function"""

    @pytest.mark.asyncio
    async def test_run_consumer_nats_disabled(self) -> None:
        """Test run_consumer when NATS is disabled"""
        with patch("tradeengine.consumer.settings") as mock_settings:
            mock_settings.nats_enabled = False

            from tradeengine.consumer import run_consumer

            await run_consumer()

            # Should return early

    @pytest.mark.asyncio
    async def test_run_consumer_nats_enabled(self) -> None:
        """Test run_consumer when NATS is enabled"""
        with patch("tradeengine.consumer.settings") as mock_settings:
            mock_settings.nats_enabled = True

            with patch("tradeengine.consumer.signal_consumer") as mock_consumer:
                mock_consumer.start_consuming = AsyncMock()

                from tradeengine.consumer import run_consumer

                await run_consumer()

                mock_consumer.start_consuming.assert_called_once()
