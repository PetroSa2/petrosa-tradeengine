import pytest

from tradeengine.consumer import SignalConsumer


@pytest.mark.asyncio
async def test_signal_consumer_initialization():
    """Test SignalConsumer initialization"""
    consumer = SignalConsumer()

    assert consumer.nc is None
    assert consumer.running is False
    assert consumer.subscription is None


def test_signal_consumer_structure():
    """Test SignalConsumer basic structure"""
    consumer = SignalConsumer()
    consumer.running = True

    # Test that the consumer has the expected structure
    assert hasattr(consumer, "initialize")
    assert hasattr(consumer, "start_consuming")
    assert hasattr(consumer, "stop_consuming")
    assert consumer.running is True
