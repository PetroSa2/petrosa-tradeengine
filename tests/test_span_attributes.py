"""
Tests for custom span attributes in OpenTelemetry traces.

Tests verify that business context attributes are correctly set on spans
for signals and orders across dispatcher, API, and consumer modules.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from opentelemetry import trace

from contracts.order import OrderStatus, TradeOrder
from contracts.signal import Signal
from tradeengine.api import app
from tradeengine.consumer import SignalConsumer
from tradeengine.dispatcher import Dispatcher


@pytest.fixture
def mock_tracer():
    """Create a mock tracer with span context manager."""
    from unittest.mock import MagicMock

    mock_tracer_obj = MagicMock()
    mock_span = MagicMock()
    mock_span.__enter__ = MagicMock(return_value=mock_span)
    mock_span.__exit__ = MagicMock(return_value=False)
    mock_tracer_obj.start_as_current_span.return_value = mock_span
    return mock_tracer_obj, mock_span


@pytest.fixture
def sample_signal():
    """Create a sample signal for testing."""
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
        target_price=45100.0,
        source="test",
        strategy="test-strategy",
    )


@pytest.fixture
def sample_order():
    """Create a sample order for testing."""
    return TradeOrder(
        symbol="BTCUSDT",
        type="market",
        side="buy",
        amount=0.1,
        order_id="test-order-1",
        status=OrderStatus.PENDING,
        time_in_force="GTC",
        position_size_pct=0.1,
        target_price=45000.0,
        position_side="LONG",
    )


@pytest.fixture
def dispatcher():
    """Create a dispatcher instance for testing."""
    return Dispatcher()


@pytest.fixture
def consumer():
    """Create a consumer instance for testing."""
    return SignalConsumer()


class TestDispatcherSpanAttributes:
    """Test span attributes in dispatcher.dispatch method."""

    @pytest.mark.asyncio
    async def test_dispatch_creates_span_with_signal_attributes(
        self, dispatcher, sample_signal, mock_tracer
    ):
        """Test that dispatch creates span with signal attributes."""
        mock_tracer_obj, mock_span = mock_tracer

        with patch("tradeengine.dispatcher.tracer", mock_tracer_obj):
            with patch.object(
                dispatcher, "process_signal", new_callable=AsyncMock
            ) as mock_process:
                mock_process.return_value = {
                    "status": "executed",
                    "order_id": "test-order-1",
                }
                with patch.object(dispatcher, "position_manager") as mock_pm:
                    mock_pm.positions = {}
                    with patch(
                        "shared.distributed_lock.distributed_lock_manager"
                    ) as mock_dlm:
                        mock_dlm.execute_with_lock = AsyncMock(
                            return_value={
                                "status": "filled",
                                "order_id": "test-order-1",
                            }
                        )

                        await dispatcher.dispatch(sample_signal)

        # Verify span was created
        mock_tracer_obj.start_as_current_span.assert_called_once_with(
            "dispatcher.dispatch"
        )

        # Verify signal attributes were set
        mock_span.set_attribute.assert_any_call("signal.symbol", "BTCUSDT")
        mock_span.set_attribute.assert_any_call("signal.timeframe", "1h")
        mock_span.set_attribute.assert_any_call("signal.action", "buy")
        mock_span.set_attribute.assert_any_call("signal.type", "buy")
        mock_span.set_attribute.assert_any_call("signal.confidence", 0.8)
        mock_span.set_attribute.assert_any_call("strategy.id", "test-strategy-1")
        mock_span.set_attribute.assert_any_call("strategy.name", "test-strategy")
        mock_span.set_attribute.assert_any_call("signal.current_price", 45000.0)
        mock_span.set_attribute.assert_any_call("signal.target_price", 45100.0)

    @pytest.mark.asyncio
    async def test_execute_order_creates_span_with_order_attributes(
        self, dispatcher, sample_order, mock_tracer
    ):
        """Test that execute_order creates span with order attributes."""
        mock_tracer_obj, mock_span = mock_tracer

        with patch("tradeengine.dispatcher.tracer", mock_tracer_obj):
            with patch.object(dispatcher, "exchange") as mock_exchange:
                mock_exchange.execute = AsyncMock(
                    return_value={
                        "status": "filled",
                        "order_id": "test-order-1",
                        "fill_price": 45000.0,
                    }
                )
                with patch.object(dispatcher, "order_manager") as mock_om:
                    mock_om.track_order = AsyncMock()

                    result = await dispatcher.execute_order(sample_order)

        # Verify span was created
        mock_tracer_obj.start_as_current_span.assert_called_once_with(
            "dispatcher.execute_order"
        )

        # Verify order attributes were set
        mock_span.set_attribute.assert_any_call("order.id", "test-order-1")
        mock_span.set_attribute.assert_any_call("order.symbol", "BTCUSDT")
        mock_span.set_attribute.assert_any_call("order.side", "buy")
        mock_span.set_attribute.assert_any_call("order.type", "market")
        mock_span.set_attribute.assert_any_call("order.quantity", 0.1)
        mock_span.set_attribute.assert_any_call("order.price", 45000.0)
        mock_span.set_attribute.assert_any_call("order.position_side", "LONG")
        mock_span.set_attribute.assert_any_call("order.status", "filled")
        mock_span.set_attribute.assert_any_call("order.exchange_id", "test-order-1")
        mock_span.set_attribute.assert_any_call("order.fill_price", 45000.0)

    @pytest.mark.asyncio
    async def test_dispatch_sets_error_status_on_exception(
        self, dispatcher, sample_signal, mock_tracer
    ):
        """Test that dispatch sets error status when exception occurs."""
        mock_tracer_obj, mock_span = mock_tracer

        with patch("tradeengine.dispatcher.tracer", mock_tracer_obj):
            with patch.object(
                dispatcher, "process_signal", new_callable=AsyncMock
            ) as mock_process:
                mock_process.side_effect = Exception("Test error")
                with patch.object(dispatcher, "position_manager") as mock_pm:
                    mock_pm.positions = {}

                    result = await dispatcher.dispatch(sample_signal)

        # Verify error status was set
        mock_span.set_status.assert_called_once()
        status_call = mock_span.set_status.call_args[0][0]
        assert status_call.status_code == trace.StatusCode.ERROR
        mock_span.record_exception.assert_called_once()


class TestAPISpanAttributes:
    """Test span attributes in API endpoints."""

    @pytest.mark.asyncio
    async def test_process_trade_creates_span_with_request_attributes(
        self, sample_signal, mock_tracer
    ):
        """Test that process_trade creates span with request attributes."""
        mock_tracer_obj, mock_span = mock_tracer

        with patch("tradeengine.api.tracer", mock_tracer_obj):
            with patch("tradeengine.api.dispatcher") as mock_dispatcher:
                mock_dispatcher.dispatch = AsyncMock(
                    return_value={"status": "executed", "order_id": "test-order-1"}
                )
                with patch(
                    "shared.distributed_lock.distributed_lock_manager"
                ) as mock_dlm:
                    mock_dlm.get_leader_info = AsyncMock(
                        return_value={"pod_id": "test-pod"}
                    )
                    mock_dlm.pod_id = "test-pod"
                    mock_dlm.is_leader = True

                    client = TestClient(app)
                    signal_dict = sample_signal.model_dump()
                    signal_dict["timestamp"] = signal_dict["timestamp"].isoformat()

                    response = client.post(
                        "/trade",
                        json={"signals": [signal_dict], "audit_logging": True},
                    )

        # Verify span was created
        assert mock_tracer_obj.start_as_current_span.called
        span_calls = [
            call[0][0] for call in mock_tracer_obj.start_as_current_span.call_args_list
        ]
        assert "api.process_trade" in span_calls

        # Verify request attributes were set
        set_attribute_calls = [
            call[0] for call in mock_span.set_attribute.call_args_list
        ]
        attribute_names = [call[0] for call in set_attribute_calls]
        assert "request.signal_count" in attribute_names
        assert "request.audit_logging" in attribute_names
        assert "signals.processed_count" in attribute_names

    @pytest.mark.asyncio
    async def test_process_single_signal_creates_span_with_signal_attributes(
        self, sample_signal, mock_tracer
    ):
        """Test that process_single_signal creates span with signal attributes."""
        mock_tracer_obj, mock_span = mock_tracer

        with patch("tradeengine.api.tracer", mock_tracer_obj):
            with patch("tradeengine.api.dispatcher") as mock_dispatcher:
                mock_dispatcher.dispatch = AsyncMock(
                    return_value={"status": "executed", "order_id": "test-order-1"}
                )

                client = TestClient(app)
                signal_dict = sample_signal.model_dump()
                signal_dict["timestamp"] = signal_dict["timestamp"].isoformat()

                response = client.post("/trade/signal", json=signal_dict)

        # Verify span was created
        assert mock_tracer_obj.start_as_current_span.called
        span_calls = [
            call[0][0] for call in mock_tracer_obj.start_as_current_span.call_args_list
        ]
        assert "api.process_single_signal" in span_calls

        # Verify signal attributes were set
        set_attribute_calls = [
            call[0] for call in mock_span.set_attribute.call_args_list
        ]
        attribute_names = [call[0] for call in set_attribute_calls]
        assert "signal.symbol" in attribute_names
        assert "signal.timeframe" in attribute_names
        assert "signal.action" in attribute_names
        assert "strategy.id" in attribute_names
        assert "strategy.name" in attribute_names

    @pytest.mark.asyncio
    async def test_place_advanced_order_creates_span_with_order_attributes(
        self, sample_order, mock_tracer
    ):
        """Test that place_advanced_order creates span with order attributes."""
        mock_tracer_obj, mock_span = mock_tracer

        with patch("tradeengine.api.tracer", mock_tracer_obj):
            with patch("tradeengine.api.dispatcher") as mock_dispatcher:
                mock_dispatcher.execute_order = AsyncMock(
                    return_value={
                        "status": "filled",
                        "order_id": "test-order-1",
                        "fill_price": 45000.0,
                    }
                )

                client = TestClient(app)
                order_dict = sample_order.model_dump()
                if "timestamp" in order_dict and hasattr(
                    order_dict["timestamp"], "isoformat"
                ):
                    order_dict["timestamp"] = order_dict["timestamp"].isoformat()

                response = client.post("/order", json=order_dict)

        # Verify span was created
        assert mock_tracer_obj.start_as_current_span.called
        span_calls = [
            call[0][0] for call in mock_tracer_obj.start_as_current_span.call_args_list
        ]
        assert "api.place_advanced_order" in span_calls

        # Verify order attributes were set
        set_attribute_calls = [
            call[0] for call in mock_span.set_attribute.call_args_list
        ]
        attribute_names = [call[0] for call in set_attribute_calls]
        assert "order.id" in attribute_names
        assert "order.symbol" in attribute_names
        assert "order.side" in attribute_names
        assert "order.type" in attribute_names
        assert "order.quantity" in attribute_names


class TestConsumerSpanAttributes:
    """Test span attributes in consumer._message_handler method."""

    @pytest.mark.asyncio
    async def test_message_handler_creates_span_with_timeframe_attribute(
        self, consumer, sample_signal, mock_tracer
    ):
        """Test that _message_handler creates span with timeframe attribute."""
        mock_tracer_obj, mock_span = mock_tracer

        # Create a mock NATS message
        from unittest.mock import MagicMock

        from nats.aio.msg import Msg

        mock_msg = MagicMock(spec=Msg)
        mock_msg.data = sample_signal.model_dump_json().encode()
        mock_msg.headers = {}

        with patch("tradeengine.consumer.tracer", mock_tracer_obj):
            with patch.object(consumer, "dispatcher") as mock_dispatcher:
                mock_dispatcher.dispatch = AsyncMock(
                    return_value={"status": "executed", "order_id": "test-order-1"}
                )

                await consumer._message_handler(mock_msg)

        # Verify span was created (the consumer uses a different span name)
        assert mock_tracer_obj.start_as_current_span.called

        # Verify timeframe attribute was set
        set_attribute_calls = [
            call[0] for call in mock_span.set_attribute.call_args_list
        ]
        attribute_names = [call[0] for call in set_attribute_calls]
        assert "signal.timeframe" in attribute_names
