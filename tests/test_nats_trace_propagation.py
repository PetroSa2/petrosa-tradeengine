"""
Unit tests for NATS trace context propagation in Trade Engine consumer.
"""

import json
import os
import sys
from datetime import datetime
from shared.constants import UTC
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nats.aio.msg import Msg
from opentelemetry import (
    context as otel_context,
    trace,
)
from opentelemetry.propagate import extract
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

# Force Enable OTEL for this test file before any imports might check it
os.environ["OTEL_SDK_DISABLED"] = "false"
os.environ["OTEL_NO_AUTO_INIT"] = "1"

# Mock petrosa_otel before importing consumer
if "petrosa_otel" not in sys.modules:
    mock_otel = MagicMock()
    sys.modules["petrosa_otel"] = mock_otel
    # Use a real context as default return value
    mock_otel.extract_trace_context = MagicMock(return_value=otel_context.get_current())

from tradeengine.consumer import SignalConsumer  # noqa: E402
from tradeengine.dispatcher import Dispatcher  # noqa: E402


@pytest.fixture(scope="module")
def span_exporter():
    """In-memory span exporter for testing"""
    return InMemorySpanExporter()


@pytest.fixture(scope="module")
def tracer_provider(span_exporter):
    """Configure tracer provider with in-memory exporter"""
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    return provider


@pytest.fixture(autouse=True)
def clear_spans(span_exporter):
    """Clear spans before each test"""
    span_exporter.clear()
    yield
    span_exporter.clear()


@pytest.fixture
def mock_dispatcher():
    """Mock dispatcher for testing"""
    dispatcher = MagicMock(spec=Dispatcher)
    dispatcher.dispatch = AsyncMock(
        return_value={"status": "executed", "order_id": "test-123"}
    )
    return dispatcher


@pytest.fixture
def consumer(mock_dispatcher, tracer_provider):
    """Create consumer instance with mock dispatcher and a REAL tracer"""
    # Get a real tracer from our test provider
    test_tracer = tracer_provider.get_tracer("test_tracer")

    # Patch the tracer used in the consumer module to ensure spans are recorded
    with patch("tradeengine.consumer.tracer", test_tracer):
        consumer = SignalConsumer(dispatcher=mock_dispatcher)
        consumer.nc = MagicMock()  # Mock NATS client
        consumer.running = True
        yield consumer


def create_nats_message(
    data: dict, subject: str = "signals.trading", reply: str = ""
) -> Msg:
    """Helper to create mock NATS message"""
    msg = MagicMock(spec=Msg)
    msg.subject = subject
    msg.data = json.dumps(data).encode()
    msg.reply = reply
    return msg


def get_complete_signal_data(overrides=None):
    """Helper to get a complete signal dictionary that passes Pydantic validation"""
    data = {
        "strategy_id": "test_strat",
        "symbol": "BTCUSDT",
        "action": "buy",
        "price": 50000.0,
        "quantity": 0.001,
        "current_price": 50000.0,
        "confidence": 0.85,
        "source": "ta-bot",
        "strategy": "test",
        "timestamp": datetime.now(UTC).isoformat(),
    }
    if overrides:
        data.update(overrides)
    return data


@pytest.mark.asyncio
async def test_extract_trace_context_with_valid_context(consumer, span_exporter):
    """Test that trace context is extracted from signal with traceparent"""
    trace_id = "0af7651916cd43dd8448eb211c80319c"
    span_id = "b7ad6b7169203331"
    traceparent = f"00-{trace_id}-{span_id}-01"

    data = get_complete_signal_data(
        {
            "_otel_trace_context": {
                "traceparent": traceparent,
            },
        }
    )
    msg = create_nats_message(data)

    parent_context = extract({"traceparent": traceparent})

    with patch(
        "tradeengine.consumer.extract_trace_context", return_value=parent_context
    ):
        await consumer._message_handler(msg)

    # Verify span was created
    spans = span_exporter.get_finished_spans()
    assert len(spans) > 0, "No spans were recorded!"

    consumer_span = next((s for s in spans if s.name == "process_trading_signal"), None)
    assert consumer_span is not None

    # Verify trace ID matches
    actual_trace_id = format(consumer_span.context.trace_id, "032x")
    assert actual_trace_id == trace_id


@pytest.mark.asyncio
async def test_extract_trace_context_without_context(consumer, span_exporter):
    """Test graceful fallback when trace context is missing"""
    data = get_complete_signal_data({"strategy_id": "no_ctx_strat"})
    msg = create_nats_message(data)

    await consumer._message_handler(msg)

    spans = span_exporter.get_finished_spans()
    assert len(spans) > 0

    consumer_span = next((s for s in spans if s.name == "process_trading_signal"), None)
    assert consumer_span is not None
    assert consumer_span.attributes.get("signal.strategy_id") == "no_ctx_strat"


@pytest.mark.asyncio
async def test_span_marked_as_error_on_exception(
    consumer, span_exporter, mock_dispatcher
):
    """Test that span is marked as error when exception occurs"""
    mock_dispatcher.dispatch.side_effect = Exception("Test dispatch error")
    data = get_complete_signal_data({"strategy_id": "err_strat"})
    msg = create_nats_message(data)

    await consumer._message_handler(msg)

    spans = span_exporter.get_finished_spans()
    assert len(spans) > 0
    consumer_span = next((s for s in spans if s.name == "process_trading_signal"), None)
    assert consumer_span is not None
    assert consumer_span.status.status_code == trace.StatusCode.ERROR


@pytest.mark.asyncio
async def test_extract_trace_context_with_legacy_headers_field(consumer, span_exporter):
    """Test that trace context is extracted from legacy _otel_trace_headers field"""
    trace_id = "0af7651916cd43dd8448eb211c80319c"
    traceparent = f"00-{trace_id}-b7ad6b7169203331-01"

    data = get_complete_signal_data(
        {
            "_otel_trace_headers": {
                "traceparent": traceparent,
            },
        }
    )
    msg = create_nats_message(data)

    await consumer._message_handler(msg)

    spans = span_exporter.get_finished_spans()
    assert len(spans) > 0

    consumer_span = next((s for s in spans if s.name == "process_trading_signal"), None)
    assert consumer_span is not None
    actual_trace_id = format(consumer_span.context.trace_id, "032x")
    assert actual_trace_id == trace_id


@pytest.mark.asyncio
async def test_missing_timestamp_handled_within_span(consumer, span_exporter):
    """Test that missing timestamp is handled within trace context"""
    data = get_complete_signal_data()
    del data["timestamp"]  # Explicitly remove timestamp

    msg = create_nats_message(data)

    await consumer._message_handler(msg)

    spans = span_exporter.get_finished_spans()
    assert len(spans) > 0
    consumer_span = next((s for s in spans if s.name == "process_trading_signal"), None)
    assert consumer_span is not None
    # Should be OK because handler provides a default timestamp
    assert consumer_span.status.status_code == trace.StatusCode.OK


@pytest.mark.asyncio
async def test_trace_context_with_malformed_traceparent(consumer, span_exporter):
    """Test handling of malformed traceparent header"""
    data = get_complete_signal_data(
        {
            "_otel_trace_context": {
                "traceparent": "invalid-format",
            },
        }
    )
    msg = create_nats_message(data)

    await consumer._message_handler(msg)

    spans = span_exporter.get_finished_spans()
    assert len(spans) > 0
    consumer_span = next((s for s in spans if s.name == "process_trading_signal"), None)
    assert consumer_span is not None
