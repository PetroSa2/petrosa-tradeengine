"""
Comprehensive tests for tradeengine/metrics.py to increase coverage
"""

from tradeengine.metrics import (
    active_oco_pairs_per_position,
    current_position_size,
    open_positions_value_usd,
    order_execution_latency_seconds,
    order_failures_total,
    order_success_rate,
    orders_executed_by_type,
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
    risk_checks_total,
    risk_rejections_total,
    strategy_oco_placed_total,
    strategy_pnl_realized,
    strategy_sl_triggered_total,
    strategy_tp_triggered_total,
    total_daily_pnl_usd,
    total_position_value_usd,
    total_realized_pnl_usd,
    total_unrealized_pnl_usd,
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
        metric = positions_opened_total.labels(
            strategy_id="test",
            symbol="BTCUSDT",
            position_side="LONG",
            exchange="binance",
        )
        initial_value = metric._value._value if hasattr(metric, "_value") else 0
        metric.inc()
        # Verify metric was incremented
        assert metric._value._value == initial_value + 1

    def test_positions_closed_total_labels(self):
        """Test positions_closed_total has correct labels"""
        # Labels: strategy_id, symbol, position_side, close_reason, exchange
        metric = positions_closed_total.labels(
            strategy_id="test",
            symbol="BTCUSDT",
            position_side="LONG",
            close_reason="signal",
            exchange="binance",
        )
        initial_value = metric._value._value if hasattr(metric, "_value") else 0
        metric.inc()
        # Verify metric was incremented
        assert metric._value._value == initial_value + 1

    def test_position_pnl_usd_labels(self):
        """Test position_pnl_usd has correct labels"""
        # Labels: strategy_id, symbol, position_side, exchange
        metric = position_pnl_usd.labels(
            strategy_id="test",
            symbol="BTCUSDT",
            position_side="LONG",
            exchange="binance",
        )
        metric.observe(100.0)
        # Verify metric recorded the observation (sample count increased)
        assert metric._sum._value > 0

    def test_position_pnl_percentage_labels(self):
        """Test position_pnl_percentage has correct labels"""
        # Labels: strategy_id, symbol, position_side, exchange
        metric = position_pnl_percentage.labels(
            strategy_id="test",
            symbol="BTCUSDT",
            position_side="LONG",
            exchange="binance",
        )
        metric.observe(5.0)
        # Verify metric recorded the observation
        assert metric._sum._value > 0

    def test_position_duration_seconds_labels(self):
        """Test position_duration_seconds has correct labels"""
        # Labels: strategy_id, symbol, position_side, close_reason, exchange
        metric = position_duration_seconds.labels(
            strategy_id="test",
            symbol="BTCUSDT",
            position_side="LONG",
            close_reason="signal",
            exchange="binance",
        )
        initial_sum = metric._sum._value
        metric.observe(300)
        # Verify metric recorded the observation (sum increased by at least observed value)
        assert metric._sum._value >= initial_sum + 300

    def test_position_roi_labels(self):
        """Test position_roi has correct labels"""
        # Labels: strategy_id, symbol, position_side, exchange
        metric = position_roi.labels(
            strategy_id="test",
            symbol="BTCUSDT",
            position_side="LONG",
            exchange="binance",
        )
        initial_sum = metric._sum._value
        metric.observe(0.05)
        # Verify metric recorded the observation (sum increased by at least observed value)
        assert metric._sum._value >= initial_sum + 0.05

    def test_open_positions_value_usd_labels(self):
        """Test open_positions_value_usd has correct labels"""
        # Labels: strategy_id, exchange
        metric = open_positions_value_usd.labels(strategy_id="test", exchange="binance")
        metric.set(5000.0)
        # Verify metric value was set
        assert metric._value._value == 5000.0

    def test_unrealized_pnl_usd_labels(self):
        """Test unrealized_pnl_usd has correct labels"""
        # Labels: strategy_id, symbol, position_side, exchange
        metric = unrealized_pnl_usd.labels(
            strategy_id="test",
            symbol="BTCUSDT",
            position_side="LONG",
            exchange="binance",
        )
        metric.set(100.0)
        # Verify metric value was set
        assert metric._value._value == 100.0

    def test_positions_winning_total_labels(self):
        """Test positions_winning_total has correct labels"""
        # Labels: strategy_id, symbol, position_side, exchange
        metric = positions_winning_total.labels(
            strategy_id="test",
            symbol="BTCUSDT",
            position_side="LONG",
            exchange="binance",
        )
        initial_value = metric._value._value if hasattr(metric, "_value") else 0
        metric.inc()
        # Verify metric was incremented
        assert metric._value._value == initial_value + 1

    def test_positions_losing_total_labels(self):
        """Test positions_losing_total has correct labels"""
        # Labels: strategy_id, symbol, position_side, exchange
        metric = positions_losing_total.labels(
            strategy_id="test",
            symbol="BTCUSDT",
            position_side="LONG",
            exchange="binance",
        )
        initial_value = metric._value._value if hasattr(metric, "_value") else 0
        metric.inc()
        # Verify metric was incremented
        assert metric._value._value == initial_value + 1

    def test_position_commission_usd_labels(self):
        """Test position_commission_usd has correct labels"""
        # Labels: strategy_id, symbol, exchange
        metric = position_commission_usd.labels(
            strategy_id="test", symbol="BTCUSDT", exchange="binance"
        )
        initial_sum = metric._sum._value
        metric.observe(0.5)
        # Verify metric recorded the observation (sum increased by at least observed value)
        assert metric._sum._value >= initial_sum + 0.5

    def test_position_entry_price_labels(self):
        """Test position_entry_price has correct labels"""
        # Labels: symbol, position_side, exchange
        metric = position_entry_price.labels(
            symbol="BTCUSDT", position_side="LONG", exchange="binance"
        )
        initial_sum = metric._sum._value
        metric.observe(45000.0)
        # Verify metric recorded the observation (sum increased by at least observed value)
        assert metric._sum._value >= initial_sum + 45000.0

    def test_position_exit_price_labels(self):
        """Test position_exit_price has correct labels"""
        # Labels: symbol, position_side, exchange
        metric = position_exit_price.labels(
            symbol="BTCUSDT", position_side="LONG", exchange="binance"
        )
        initial_sum = metric._sum._value
        metric.observe(46000.0)
        # Verify metric recorded the observation (sum increased by at least observed value)
        assert metric._sum._value >= initial_sum + 46000.0


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


class TestStrategyOCOMetrics:
    """Test Strategy OCO Attribution Metrics"""

    def test_strategy_oco_placed_total_exists(self):
        """Test strategy_oco_placed_total metric exists"""
        assert strategy_oco_placed_total is not None
        assert strategy_oco_placed_total._name == "tradeengine_strategy_oco_placed"

    def test_strategy_tp_triggered_total_exists(self):
        """Test strategy_tp_triggered_total metric exists"""
        assert strategy_tp_triggered_total is not None
        assert strategy_tp_triggered_total._name == "tradeengine_strategy_tp_triggered"

    def test_strategy_sl_triggered_total_exists(self):
        """Test strategy_sl_triggered_total metric exists"""
        assert strategy_sl_triggered_total is not None
        assert strategy_sl_triggered_total._name == "tradeengine_strategy_sl_triggered"

    def test_strategy_pnl_realized_exists(self):
        """Test strategy_pnl_realized metric exists"""
        assert strategy_pnl_realized is not None
        assert strategy_pnl_realized._name == "tradeengine_strategy_pnl_realized"

    def test_active_oco_pairs_per_position_exists(self):
        """Test active_oco_pairs_per_position metric exists"""
        assert active_oco_pairs_per_position is not None
        assert (
            active_oco_pairs_per_position._name
            == "tradeengine_active_oco_pairs_per_position"
        )

    def test_strategy_oco_placed_total_labels(self):
        """Test strategy_oco_placed_total has correct labels"""
        # Labels: strategy_id, symbol, exchange
        metric = strategy_oco_placed_total.labels(
            strategy_id="test", symbol="BTCUSDT", exchange="binance"
        )
        initial_value = metric._value._value if hasattr(metric, "_value") else 0
        metric.inc()
        assert metric._value._value == initial_value + 1

    def test_strategy_tp_triggered_total_labels(self):
        """Test strategy_tp_triggered_total has correct labels"""
        # Labels: strategy_id, symbol, exchange
        metric = strategy_tp_triggered_total.labels(
            strategy_id="test", symbol="BTCUSDT", exchange="binance"
        )
        initial_value = metric._value._value if hasattr(metric, "_value") else 0
        metric.inc()
        assert metric._value._value == initial_value + 1

    def test_strategy_sl_triggered_total_labels(self):
        """Test strategy_sl_triggered_total has correct labels"""
        # Labels: strategy_id, symbol, exchange
        metric = strategy_sl_triggered_total.labels(
            strategy_id="test", symbol="BTCUSDT", exchange="binance"
        )
        initial_value = metric._value._value if hasattr(metric, "_value") else 0
        metric.inc()
        assert metric._value._value == initial_value + 1

    def test_strategy_pnl_realized_labels(self):
        """Test strategy_pnl_realized has correct labels"""
        # Labels: strategy_id, close_reason, exchange
        metric = strategy_pnl_realized.labels(
            strategy_id="test", close_reason="tp", exchange="binance"
        )
        initial_sum = metric._sum._value
        metric.observe(50.0)
        assert metric._sum._value >= initial_sum + 50.0

    def test_active_oco_pairs_per_position_labels(self):
        """Test active_oco_pairs_per_position has correct labels"""
        # Labels: symbol, position_side, exchange
        metric = active_oco_pairs_per_position.labels(
            symbol="BTCUSDT", position_side="LONG", exchange="binance"
        )
        metric.set(2.0)
        assert metric._value._value == 2.0

    def test_strategy_pnl_realized_is_histogram(self):
        """Test strategy_pnl_realized is a Histogram"""
        from prometheus_client import Histogram

        assert isinstance(strategy_pnl_realized, Histogram)

    def test_active_oco_pairs_per_position_is_gauge(self):
        """Test active_oco_pairs_per_position is a Gauge"""
        from prometheus_client import Gauge

        assert isinstance(active_oco_pairs_per_position, Gauge)


class TestBusinessMetrics:
    """Test Business Metrics for Trade Execution Monitoring"""

    def test_orders_executed_by_type_exists(self):
        """Test orders_executed_by_type metric exists"""
        assert orders_executed_by_type is not None
        assert orders_executed_by_type._name == "tradeengine_orders_executed_by_type"

    def test_order_execution_latency_seconds_exists(self):
        """Test order_execution_latency_seconds metric exists"""
        assert order_execution_latency_seconds is not None
        assert (
            order_execution_latency_seconds._name
            == "tradeengine_order_execution_latency_seconds"
        )

    def test_risk_rejections_total_exists(self):
        """Test risk_rejections_total metric exists"""
        assert risk_rejections_total is not None
        assert risk_rejections_total._name == "tradeengine_risk_rejections"

    def test_risk_checks_total_exists(self):
        """Test risk_checks_total metric exists"""
        assert risk_checks_total is not None
        assert risk_checks_total._name == "tradeengine_risk_checks"

    def test_current_position_size_exists(self):
        """Test current_position_size metric exists"""
        assert current_position_size is not None
        assert current_position_size._name == "tradeengine_current_position_size"

    def test_total_position_value_usd_exists(self):
        """Test total_position_value_usd metric exists"""
        assert total_position_value_usd is not None
        assert total_position_value_usd._name == "tradeengine_total_position_value_usd"

    def test_total_realized_pnl_usd_exists(self):
        """Test total_realized_pnl_usd metric exists"""
        assert total_realized_pnl_usd is not None
        assert total_realized_pnl_usd._name == "tradeengine_total_realized_pnl_usd"

    def test_total_unrealized_pnl_usd_exists(self):
        """Test total_unrealized_pnl_usd metric exists"""
        assert total_unrealized_pnl_usd is not None
        assert total_unrealized_pnl_usd._name == "tradeengine_total_unrealized_pnl_usd"

    def test_total_daily_pnl_usd_exists(self):
        """Test total_daily_pnl_usd metric exists"""
        assert total_daily_pnl_usd is not None
        assert total_daily_pnl_usd._name == "tradeengine_total_daily_pnl_usd"

    def test_order_success_rate_exists(self):
        """Test order_success_rate metric exists"""
        assert order_success_rate is not None
        assert order_success_rate._name == "tradeengine_order_success_rate"

    def test_order_failures_total_exists(self):
        """Test order_failures_total metric exists"""
        assert order_failures_total is not None
        assert order_failures_total._name == "tradeengine_order_failures"

    def test_orders_executed_by_type_labels(self):
        """Test orders_executed_by_type has correct labels"""
        # Labels: order_type, side, symbol, exchange
        metric = orders_executed_by_type.labels(
            order_type="market", side="buy", symbol="BTCUSDT", exchange="binance"
        )
        initial_value = metric._value._value if hasattr(metric, "_value") else 0
        metric.inc()
        assert metric._value._value == initial_value + 1

    def test_order_execution_latency_seconds_labels(self):
        """Test order_execution_latency_seconds has correct labels"""
        # Labels: symbol, order_type, exchange
        metric = order_execution_latency_seconds.labels(
            symbol="BTCUSDT", order_type="market", exchange="binance"
        )
        initial_sum = metric._sum._value
        metric.observe(0.5)
        assert metric._sum._value >= initial_sum + 0.5

    def test_risk_rejections_total_labels(self):
        """Test risk_rejections_total has correct labels"""
        # Labels: reason, symbol, exchange
        metric = risk_rejections_total.labels(
            reason="position_limit", symbol="BTCUSDT", exchange="binance"
        )
        initial_value = metric._value._value if hasattr(metric, "_value") else 0
        metric.inc()
        assert metric._value._value == initial_value + 1

    def test_risk_checks_total_labels(self):
        """Test risk_checks_total has correct labels"""
        # Labels: check_type, result, exchange
        metric = risk_checks_total.labels(
            check_type="position_limit", result="passed", exchange="binance"
        )
        initial_value = metric._value._value if hasattr(metric, "_value") else 0
        metric.inc()
        assert metric._value._value == initial_value + 1

    def test_current_position_size_labels(self):
        """Test current_position_size has correct labels"""
        # Labels: symbol, position_side, exchange
        metric = current_position_size.labels(
            symbol="BTCUSDT", position_side="LONG", exchange="binance"
        )
        metric.set(0.1)
        assert metric._value._value == 0.1

    def test_total_position_value_usd_labels(self):
        """Test total_position_value_usd has correct labels"""
        # Labels: exchange
        metric = total_position_value_usd.labels(exchange="binance")
        metric.set(10000.0)
        assert metric._value._value == 10000.0

    def test_total_realized_pnl_usd_labels(self):
        """Test total_realized_pnl_usd has correct labels"""
        # Labels: exchange
        metric = total_realized_pnl_usd.labels(exchange="binance")
        metric.set(500.0)
        assert metric._value._value == 500.0

    def test_total_unrealized_pnl_usd_labels(self):
        """Test total_unrealized_pnl_usd has correct labels"""
        # Labels: exchange
        metric = total_unrealized_pnl_usd.labels(exchange="binance")
        metric.set(200.0)
        assert metric._value._value == 200.0

    def test_total_daily_pnl_usd_labels(self):
        """Test total_daily_pnl_usd has correct labels"""
        # Labels: exchange
        metric = total_daily_pnl_usd.labels(exchange="binance")
        metric.set(300.0)
        assert metric._value._value == 300.0

    def test_order_success_rate_labels(self):
        """Test order_success_rate has correct labels"""
        # Labels: symbol, order_type, exchange
        metric = order_success_rate.labels(
            symbol="BTCUSDT", order_type="market", exchange="binance"
        )
        metric.set(0.95)
        assert metric._value._value == 0.95

    def test_order_failures_total_labels(self):
        """Test order_failures_total has correct labels"""
        # Labels: symbol, order_type, failure_reason, exchange
        metric = order_failures_total.labels(
            symbol="BTCUSDT",
            order_type="market",
            failure_reason="insufficient_balance",
            exchange="binance",
        )
        initial_value = metric._value._value if hasattr(metric, "_value") else 0
        metric.inc()
        assert metric._value._value == initial_value + 1

    def test_order_execution_latency_seconds_is_histogram(self):
        """Test order_execution_latency_seconds is a Histogram"""
        from prometheus_client import Histogram

        assert isinstance(order_execution_latency_seconds, Histogram)

    def test_current_position_size_is_gauge(self):
        """Test current_position_size is a Gauge"""
        from prometheus_client import Gauge

        assert isinstance(current_position_size, Gauge)

    def test_total_position_value_usd_is_gauge(self):
        """Test total_position_value_usd is a Gauge"""
        from prometheus_client import Gauge

        assert isinstance(total_position_value_usd, Gauge)

    def test_total_realized_pnl_usd_is_gauge(self):
        """Test total_realized_pnl_usd is a Gauge"""
        from prometheus_client import Gauge

        assert isinstance(total_realized_pnl_usd, Gauge)

    def test_total_unrealized_pnl_usd_is_gauge(self):
        """Test total_unrealized_pnl_usd is a Gauge"""
        from prometheus_client import Gauge

        assert isinstance(total_unrealized_pnl_usd, Gauge)

    def test_total_daily_pnl_usd_is_gauge(self):
        """Test total_daily_pnl_usd is a Gauge"""
        from prometheus_client import Gauge

        assert isinstance(total_daily_pnl_usd, Gauge)

    def test_order_success_rate_is_gauge(self):
        """Test order_success_rate is a Gauge"""
        from prometheus_client import Gauge

        assert isinstance(order_success_rate, Gauge)
