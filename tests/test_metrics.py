"""
Comprehensive tests for tradeengine/metrics.py to increase coverage
"""

from tradeengine.metrics import (
    open_positions_value_usd,
    position_commission_usd,
    position_duration_seconds,
    position_entry_price,
    position_exit_price,
    position_pnl_percentage,
    position_pnl_usd,
    position_roi,
    positions_closed_total,
    positions_losing_total,
    positions_opened_total,
    positions_winning_total,
    unrealized_pnl_usd,
)


class TestMetricsExist:
    """Test that all metrics are properly defined"""

    def test_positions_opened_total_exists(self):
        """Test positions_opened_total metric exists"""
        assert positions_opened_total is not None
        assert positions_opened_total._name == "tradeengine_positions_opened"

    def test_positions_closed_total_exists(self):
        """Test positions_closed_total metric exists"""
        assert positions_closed_total is not None
        assert positions_closed_total._name == "tradeengine_positions_closed"

    def test_position_pnl_usd_exists(self):
        """Test position_pnl_usd metric exists"""
        assert position_pnl_usd is not None
        assert position_pnl_usd._name == "tradeengine_position_pnl_usd"

    def test_position_pnl_percentage_exists(self):
        """Test position_pnl_percentage metric exists"""
        assert position_pnl_percentage is not None
        assert position_pnl_percentage._name == "tradeengine_position_pnl_percentage"

    def test_position_duration_seconds_exists(self):
        """Test position_duration_seconds metric exists"""
        assert position_duration_seconds is not None
        assert (
            position_duration_seconds._name == "tradeengine_position_duration_seconds"
        )

    def test_position_roi_exists(self):
        """Test position_roi metric exists"""
        assert position_roi is not None
        assert position_roi._name == "tradeengine_position_roi"

    def test_open_positions_value_usd_exists(self):
        """Test open_positions_value_usd metric exists"""
        assert open_positions_value_usd is not None
        assert open_positions_value_usd._name == "tradeengine_open_positions_value_usd"

    def test_unrealized_pnl_usd_exists(self):
        """Test unrealized_pnl_usd metric exists"""
        assert unrealized_pnl_usd is not None
        assert unrealized_pnl_usd._name == "tradeengine_unrealized_pnl_usd"

    def test_positions_winning_total_exists(self):
        """Test positions_winning_total metric exists"""
        assert positions_winning_total is not None
        assert positions_winning_total._name == "tradeengine_positions_winning"

    def test_positions_losing_total_exists(self):
        """Test positions_losing_total metric exists"""
        assert positions_losing_total is not None
        assert positions_losing_total._name == "tradeengine_positions_losing"

    def test_position_commission_usd_exists(self):
        """Test position_commission_usd metric exists"""
        assert position_commission_usd is not None
        assert position_commission_usd._name == "tradeengine_position_commission_usd"

    def test_position_entry_price_exists(self):
        """Test position_entry_price metric exists"""
        assert position_entry_price is not None
        assert position_entry_price._name == "tradeengine_position_entry_price_usd"

    def test_position_exit_price_exists(self):
        """Test position_exit_price metric exists"""
        assert position_exit_price is not None
        assert position_exit_price._name == "tradeengine_position_exit_price_usd"


class TestMetricLabels:
    """Test that metrics have correct labels"""

    def test_positions_opened_total_labels(self):
        """Test positions_opened_total has correct labels"""
        # Labels: strategy_id, symbol, position_side, exchange
        positions_opened_total.labels(
            strategy_id="test",
            symbol="BTCUSDT",
            position_side="LONG",
            exchange="binance",
        ).inc()

    def test_positions_closed_total_labels(self):
        """Test positions_closed_total has correct labels"""
        # Labels: strategy_id, symbol, position_side, close_reason, exchange
        positions_closed_total.labels(
            strategy_id="test",
            symbol="BTCUSDT",
            position_side="LONG",
            close_reason="signal",
            exchange="binance",
        ).inc()

    def test_position_pnl_usd_labels(self):
        """Test position_pnl_usd has correct labels"""
        # Labels: strategy_id, symbol, position_side, exchange
        position_pnl_usd.labels(
            strategy_id="test",
            symbol="BTCUSDT",
            position_side="LONG",
            exchange="binance",
        ).observe(100.0)

    def test_position_pnl_percentage_labels(self):
        """Test position_pnl_percentage has correct labels"""
        # Labels: strategy_id, symbol, position_side, exchange
        position_pnl_percentage.labels(
            strategy_id="test",
            symbol="BTCUSDT",
            position_side="LONG",
            exchange="binance",
        ).observe(5.0)

    def test_position_duration_seconds_labels(self):
        """Test position_duration_seconds has correct labels"""
        # Labels: strategy_id, symbol, position_side, close_reason, exchange
        position_duration_seconds.labels(
            strategy_id="test",
            symbol="BTCUSDT",
            position_side="LONG",
            close_reason="signal",
            exchange="binance",
        ).observe(300)

    def test_position_roi_labels(self):
        """Test position_roi has correct labels"""
        # Labels: strategy_id, symbol, position_side, exchange
        position_roi.labels(
            strategy_id="test",
            symbol="BTCUSDT",
            position_side="LONG",
            exchange="binance",
        ).observe(0.05)

    def test_open_positions_value_usd_labels(self):
        """Test open_positions_value_usd has correct labels"""
        # Labels: strategy_id, exchange
        open_positions_value_usd.labels(strategy_id="test", exchange="binance").set(
            5000.0
        )

    def test_unrealized_pnl_usd_labels(self):
        """Test unrealized_pnl_usd has correct labels"""
        # Labels: strategy_id, symbol, position_side, exchange
        unrealized_pnl_usd.labels(
            strategy_id="test",
            symbol="BTCUSDT",
            position_side="LONG",
            exchange="binance",
        ).set(100.0)

    def test_positions_winning_total_labels(self):
        """Test positions_winning_total has correct labels"""
        # Labels: strategy_id, symbol, position_side, exchange
        positions_winning_total.labels(
            strategy_id="test",
            symbol="BTCUSDT",
            position_side="LONG",
            exchange="binance",
        ).inc()

    def test_positions_losing_total_labels(self):
        """Test positions_losing_total has correct labels"""
        # Labels: strategy_id, symbol, position_side, exchange
        positions_losing_total.labels(
            strategy_id="test",
            symbol="BTCUSDT",
            position_side="LONG",
            exchange="binance",
        ).inc()

    def test_position_commission_usd_labels(self):
        """Test position_commission_usd has correct labels"""
        # Labels: strategy_id, symbol, exchange
        position_commission_usd.labels(
            strategy_id="test", symbol="BTCUSDT", exchange="binance"
        ).observe(0.5)

    def test_position_entry_price_labels(self):
        """Test position_entry_price has correct labels"""
        # Labels: symbol, position_side, exchange
        position_entry_price.labels(
            symbol="BTCUSDT", position_side="LONG", exchange="binance"
        ).observe(45000.0)

    def test_position_exit_price_labels(self):
        """Test position_exit_price has correct labels"""
        # Labels: symbol, position_side, exchange
        position_exit_price.labels(
            symbol="BTCUSDT", position_side="LONG", exchange="binance"
        ).observe(46000.0)


class TestMetricBuckets:
    """Test that histogram metrics have appropriate buckets"""

    def test_position_pnl_usd_is_histogram(self):
        """Test position_pnl_usd is a Histogram"""
        from prometheus_client import Histogram

        assert isinstance(position_pnl_usd, Histogram)

    def test_position_pnl_percentage_is_histogram(self):
        """Test position_pnl_percentage is a Histogram"""
        from prometheus_client import Histogram

        assert isinstance(position_pnl_percentage, Histogram)

    def test_position_duration_seconds_is_histogram(self):
        """Test position_duration_seconds is a Histogram"""
        from prometheus_client import Histogram

        assert isinstance(position_duration_seconds, Histogram)

    def test_position_roi_is_histogram(self):
        """Test position_roi is a Histogram"""
        from prometheus_client import Histogram

        assert isinstance(position_roi, Histogram)

    def test_position_commission_usd_is_histogram(self):
        """Test position_commission_usd is a Histogram"""
        from prometheus_client import Histogram

        assert isinstance(position_commission_usd, Histogram)

    def test_position_entry_price_is_histogram(self):
        """Test position_entry_price is a Histogram"""
        from prometheus_client import Histogram

        assert isinstance(position_entry_price, Histogram)

    def test_position_exit_price_is_histogram(self):
        """Test position_exit_price is a Histogram"""
        from prometheus_client import Histogram

        assert isinstance(position_exit_price, Histogram)
