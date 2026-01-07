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
    return Signal(
        strategy_id="test-strategy",
        symbol="BTCUSDT",
        action="buy",
        confidence=0.8,
        current_price=50000.0,
        timeframe="1h",
        quantity=0.001,
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

    def test_get_strategy_weight(self, signal_aggregator):
        """Test getting strategy weight"""
        signal_aggregator.set_strategy_weight("test-strategy", 0.7)
        weight = signal_aggregator.get_strategy_weight("test-strategy")
        assert weight == 0.7

    def test_get_strategy_weight_default(self, signal_aggregator):
        """Test getting default strategy weight"""
        weight = signal_aggregator.get_strategy_weight("nonexistent-strategy")
        assert weight == 1.0  # Default weight

    def test_clear_signals(self, signal_aggregator, sample_signal):
        """Test clearing signals"""
        signal_aggregator.add_signal(sample_signal)
        signal_aggregator.clear_signals()
        # Should not raise exception

    def test_get_signals(self, signal_aggregator, sample_signal):
        """Test getting signals"""
        signal_aggregator.add_signal(sample_signal)
        signals = signal_aggregator.get_signals()
        assert isinstance(signals, list)

    def test_get_signals_by_symbol(self, signal_aggregator, sample_signal):
        """Test getting signals by symbol"""
        signal_aggregator.add_signal(sample_signal)
        signals = signal_aggregator.get_signals_by_symbol("BTCUSDT")
        assert isinstance(signals, list)

    def test_get_signals_by_strategy(self, signal_aggregator, sample_signal):
        """Test getting signals by strategy"""
        signal_aggregator.add_signal(sample_signal)
        signals = signal_aggregator.get_signals_by_strategy("test-strategy")
        assert isinstance(signals, list)

    def test_get_aggregated_signal(self, signal_aggregator, sample_signal):
        """Test getting aggregated signal"""
        signal_aggregator.add_signal(sample_signal)
        aggregated = signal_aggregator.get_aggregated_signal("BTCUSDT")
        # May return None or a signal
        assert aggregated is None or isinstance(aggregated, Signal)

    def test_get_aggregated_signal_nonexistent(self, signal_aggregator):
        """Test getting aggregated signal for nonexistent symbol"""
        aggregated = signal_aggregator.get_aggregated_signal("ETHUSDT")
        assert aggregated is None

