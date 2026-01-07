"""
Comprehensive tests for tradeengine/signal_aggregator.py to increase coverage
"""

import pytest
from contracts.signal import Signal
from tradeengine.signal_aggregator import SignalAggregator


@pytest.fixture
def signal_aggregator():
    """Create a SignalAggregator instance for testing"""
    return SignalAggregator()


@pytest.fixture
def sample_signal():
    """Create a sample signal for testing"""
    from datetime import datetime
    return Signal(
        strategy_id="test-strategy",
        symbol="BTCUSDT",
        action="buy",
        confidence=0.8,
        current_price=50000.0,
        price=50000.0,
        timeframe="1h",
        quantity=0.001,
        timestamp=datetime.utcnow(),
    )


class TestSignalAggregatorBasic:
    """Test basic SignalAggregator functionality"""

    def test_add_signal(self, signal_aggregator, sample_signal):
        """Test adding a signal"""
        signal_aggregator.add_signal(sample_signal)
        # Should not raise exception

    def test_get_signal_summary(self, signal_aggregator):
        """Test getting signal summary"""
        summary = signal_aggregator.get_signal_summary()
        assert isinstance(summary, dict)

    def test_set_strategy_weight(self, signal_aggregator):
        """Test setting strategy weight"""
        signal_aggregator.set_strategy_weight("test-strategy", 0.5)
        # Should not raise exception

    @pytest.mark.asyncio
    async def test_process_signal(self, signal_aggregator, sample_signal):
        """Test processing a signal"""
        result = await signal_aggregator.process_signal(sample_signal)
        assert isinstance(result, dict)
        assert "status" in result

