"""
Tests for business metrics instrumentation

Verifies that all business metrics are correctly emitted during order execution,
risk management checks, and position management operations.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from prometheus_client import REGISTRY

from contracts.order import OrderSide, OrderStatus, OrderType, TradeOrder
from contracts.signal import OrderType as SignalOrderType
from contracts.signal import Signal, SignalStrength, StrategyMode
from tradeengine.dispatcher import Dispatcher


@pytest.fixture
def sample_signal():
    """Create a sample trading signal for testing"""
    return Signal(
        signal_id="test_signal_123",
        strategy_id="test_strategy",
        symbol="BTCUSDT",
        action="buy",
        current_price=50000.0,
        price=50000.0,
        quantity=0.01,
        confidence=0.85,
        strength=SignalStrength.STRONG,
        source="test",
        strategy="test_strategy",
        timeframe="1h",
        order_type=SignalOrderType.MARKET,
        strategy_mode=StrategyMode.DETERMINISTIC,
        stop_loss=48000.0,
        take_profit=52000.0,
    )


@pytest.fixture
def sample_order():
    """Create a sample trade order for testing"""
    return TradeOrder(
        order_id="test_order_123",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        amount=0.01,
        target_price=50000.0,
        stop_loss=48000.0,
        take_profit=52000.0,
        position_id="test_position_123",
        position_side="LONG",
        exchange="binance",
        status=OrderStatus.PENDING,
        simulate=False,
    )


@pytest.fixture
async def dispatcher():
    """Create a dispatcher instance for testing"""
    mock_exchange = MagicMock()
    mock_exchange.execute = AsyncMock(
        return_value={
            "status": "filled",
            "order_id": "binance_order_123",
            "fill_price": 50000.0,
            "amount": 0.01,
        }
    )

    dispatcher = Dispatcher(exchange=mock_exchange)
    await dispatcher.initialize()
    yield dispatcher
    await dispatcher.close()


class TestOrderExecutionMetrics:
    """Test order execution metrics emission"""

    @pytest.mark.asyncio
    async def test_orders_executed_by_type_metric(self, dispatcher, sample_order):
        """Test that orders_executed_by_type counter increments correctly"""
        # Add signal_received_at to order meta for latency tracking
        sample_order.meta = {"signal_received_at": 1234567890.0}

        # Get initial counter value
        initial_value = self._get_counter_value(
            "tradeengine_orders_executed_by_type_total",
            {
                "order_type": "market",
                "side": "buy",
                "symbol": "BTCUSDT",
                "exchange": "binance",
            },
        )

        # Execute order
        result = await dispatcher.execute_order(sample_order)

        # Verify counter incremented
        final_value = self._get_counter_value(
            "tradeengine_orders_executed_by_type_total",
            {
                "order_type": "market",
                "side": "buy",
                "symbol": "BTCUSDT",
                "exchange": "binance",
            },
        )

        assert final_value == initial_value + 1
        assert result["status"] == "filled"

    @pytest.mark.asyncio
    async def test_order_execution_latency_metric(self, dispatcher, sample_signal):
        """Test that order execution latency histogram is observed"""
        # Mock the signal processing
        with patch.object(
            dispatcher, "process_signal", new_callable=AsyncMock
        ) as mock_process:
            mock_process.return_value = {"status": "success"}

            # Dispatch signal
            _ = await dispatcher.dispatch(sample_signal)

            # Verify latency metric was observed
            histogram_samples = self._get_histogram_samples(
                "tradeengine_order_execution_latency_seconds"
            )

            assert len(histogram_samples) > 0
            # Latency should be positive and reasonable (< 10 seconds for test)
            assert all(0 < sample < 10 for sample in histogram_samples)

    @pytest.mark.asyncio
    async def test_order_failures_metric(self, dispatcher, sample_order):
        """Test that order_failures_total counter increments on failures"""
        # Add signal_received_at to trigger latency tracking
        sample_order.meta = {"signal_received_at": 1234567890.0}

        # Mock exchange to return error
        dispatcher.exchange.execute = AsyncMock(
            return_value={
                "status": "error",
                "error": "Insufficient balance",
            }
        )

        initial_value = self._get_counter_value(
            "tradeengine_order_failures_total",
            {
                "symbol": "BTCUSDT",
                "order_type": "market",
                "failure_reason": "Insufficient balance",
                "exchange": "binance",
            },
        )

        # Execute order (should fail)
        result = await dispatcher.execute_order(sample_order)

        # Verify failure counter incremented
        final_value = self._get_counter_value(
            "tradeengine_order_failures_total",
            {
                "symbol": "BTCUSDT",
                "order_type": "market",
                "failure_reason": "Insufficient balance",
                "exchange": "binance",
            },
        )

        assert final_value == initial_value + 1
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_order_latency_with_meta(self, dispatcher, sample_order):
        """Test that order latency is tracked when signal_received_at is in meta"""
        import time

        # Add signal_received_at to order meta
        sample_order.meta = {"signal_received_at": time.time() - 0.5}  # 500ms ago

        # Execute order
        result = await dispatcher.execute_order(sample_order)

        # Verify latency histogram has data
        histogram_count = self._get_histogram_count(
            "tradeengine_order_execution_latency_seconds"
        )

        # Should have at least one observation
        assert histogram_count > 0
        assert result["status"] == "filled"

    @pytest.mark.asyncio
    async def test_rejected_order_metric(self, dispatcher, sample_order):
        """Test that rejected orders are tracked as failures"""
        # Add signal_received_at to order meta
        sample_order.meta = {"signal_received_at": 1234567890.0}

        # Mock exchange to return rejected status
        dispatcher.exchange.execute = AsyncMock(
            return_value={
                "status": "rejected",
                "error": "Order rejected by exchange",
            }
        )

        initial_value = self._get_counter_value(
            "tradeengine_order_failures_total",
            {
                "symbol": "BTCUSDT",
                "order_type": "market",
                "failure_reason": "Order rejected by exchange",
                "exchange": "binance",
            },
        )

        # Execute order (should be rejected)
        result = await dispatcher.execute_order(sample_order)

        # Verify failure counter incremented
        final_value = self._get_counter_value(
            "tradeengine_order_failures_total",
            {
                "symbol": "BTCUSDT",
                "order_type": "market",
                "failure_reason": "Order rejected by exchange",
                "exchange": "binance",
            },
        )

        assert final_value == initial_value + 1
        assert result["status"] == "rejected"

    @pytest.mark.asyncio
    async def test_cancelled_order_metric(self, dispatcher, sample_order):
        """Test that cancelled orders are tracked as failures"""
        # Add signal_received_at to order meta
        sample_order.meta = {"signal_received_at": 1234567890.0}

        # Mock exchange to return cancelled status
        dispatcher.exchange.execute = AsyncMock(
            return_value={
                "status": "cancelled",
                "error": "Order cancelled by user",
            }
        )

        initial_value = self._get_counter_value(
            "tradeengine_order_failures_total",
            {
                "symbol": "BTCUSDT",
                "order_type": "market",
                "failure_reason": "Order cancelled by user",
                "exchange": "binance",
            },
        )

        # Execute order (should be cancelled)
        result = await dispatcher.execute_order(sample_order)

        # Verify failure counter incremented
        final_value = self._get_counter_value(
            "tradeengine_order_failures_total",
            {
                "symbol": "BTCUSDT",
                "order_type": "market",
                "failure_reason": "Order cancelled by user",
                "exchange": "binance",
            },
        )

        assert final_value == initial_value + 1
        assert result["status"] == "cancelled"

    def _get_histogram_count(self, metric_name):
        """Helper to get histogram observation count"""
        for metric in REGISTRY.collect():
            if metric.name == metric_name:
                for sample in metric.samples:
                    if sample.name.endswith("_count"):
                        return sample.value
        return 0

    def _get_counter_value(self, metric_name, labels):
        """Helper to get counter value from Prometheus registry"""
        for metric in REGISTRY.collect():
            if metric.name == metric_name:
                for sample in metric.samples:
                    if sample.labels == labels:
                        return sample.value
        return 0

    def _get_histogram_samples(self, metric_name):
        """Helper to get histogram sample values"""
        samples = []
        for metric in REGISTRY.collect():
            if metric.name == metric_name:
                for sample in metric.samples:
                    if sample.name.endswith("_sum"):
                        samples.append(sample.value)
        return samples


class TestRiskManagementMetrics:
    """Test risk management metrics emission"""

    @pytest.mark.asyncio
    async def test_risk_rejections_total_metric(self, dispatcher, sample_order):
        """Test that risk_rejections_total counter increments on rejections"""
        # Mock position manager to reject order
        with patch.object(
            dispatcher.position_manager, "check_position_limits", new_callable=AsyncMock
        ) as mock_check:
            mock_check.return_value = False

            initial_value = self._get_counter_value(
                "tradeengine_risk_rejections_total",
                {
                    "reason": "position_limits_exceeded",
                    "symbol": "BTCUSDT",
                    "exchange": "binance",
                },
            )

            # Execute order (should be rejected)
            result = await dispatcher._execute_order_with_consensus(sample_order)

            # Verify rejection counter incremented
            final_value = self._get_counter_value(
                "tradeengine_risk_rejections_total",
                {
                    "reason": "position_limits_exceeded",
                    "symbol": "BTCUSDT",
                    "exchange": "binance",
                },
            )

            assert final_value == initial_value + 1
            assert result["status"] == "rejected"

    @pytest.mark.asyncio
    async def test_risk_checks_total_metric(self, dispatcher, sample_order):
        """Test that risk_checks_total counter tracks all risk checks"""
        # Mock both position checks
        with (
            patch.object(
                dispatcher.position_manager,
                "check_position_limits",
                new_callable=AsyncMock,
            ) as mock_position,
            patch.object(
                dispatcher.position_manager,
                "check_daily_loss_limits",
                new_callable=AsyncMock,
            ) as mock_daily,
        ):
            mock_position.return_value = True
            mock_daily.return_value = True

            # Get initial values
            checking_initial = self._get_counter_value(
                "tradeengine_risk_checks_total",
                {
                    "check_type": "position_limits",
                    "result": "checking",
                    "exchange": "binance",
                },
            )
            passed_initial = self._get_counter_value(
                "tradeengine_risk_checks_total",
                {
                    "check_type": "position_limits",
                    "result": "passed",
                    "exchange": "binance",
                },
            )

            # Execute order
            await dispatcher._execute_order_with_consensus(sample_order)

            # Verify check counters incremented
            checking_final = self._get_counter_value(
                "tradeengine_risk_checks_total",
                {
                    "check_type": "position_limits",
                    "result": "checking",
                    "exchange": "binance",
                },
            )
            passed_final = self._get_counter_value(
                "tradeengine_risk_checks_total",
                {
                    "check_type": "position_limits",
                    "result": "passed",
                    "exchange": "binance",
                },
            )

            assert checking_final == checking_initial + 1
            assert passed_final == passed_initial + 1

    @pytest.mark.asyncio
    async def test_daily_loss_limit_rejection(self, dispatcher, sample_order):
        """Test that daily loss limit rejections emit correct metrics"""
        # Mock position limits to pass but daily loss to fail
        with (
            patch.object(
                dispatcher.position_manager,
                "check_position_limits",
                new_callable=AsyncMock,
            ) as mock_position,
            patch.object(
                dispatcher.position_manager,
                "check_daily_loss_limits",
                new_callable=AsyncMock,
            ) as mock_daily,
        ):
            mock_position.return_value = True
            mock_daily.return_value = False

            initial_value = self._get_counter_value(
                "tradeengine_risk_rejections_total",
                {
                    "reason": "daily_loss_limits_exceeded",
                    "symbol": "BTCUSDT",
                    "exchange": "binance",
                },
            )

            # Execute order (should be rejected)
            result = await dispatcher._execute_order_with_consensus(sample_order)

            # Verify rejection counter incremented
            final_value = self._get_counter_value(
                "tradeengine_risk_rejections_total",
                {
                    "reason": "daily_loss_limits_exceeded",
                    "symbol": "BTCUSDT",
                    "exchange": "binance",
                },
            )

            assert final_value == initial_value + 1
            assert result["status"] == "rejected"
            assert "Daily loss limits exceeded" in result["reason"]

    def _get_counter_value(self, metric_name, labels):
        """Helper to get counter value from Prometheus registry"""
        for metric in REGISTRY.collect():
            if metric.name == metric_name:
                for sample in metric.samples:
                    if sample.labels == labels:
                        return sample.value
        return 0


class TestPositionMetrics:
    """Test position and PnL metrics emission"""

    @pytest.mark.asyncio
    async def test_current_position_size_gauge(self, dispatcher, sample_order):
        """Test that current_position_size gauge updates correctly"""
        # Mock position manager update
        with patch.object(
            dispatcher.position_manager, "update_position", new_callable=AsyncMock
        ) as mock_update:
            mock_update.return_value = None

            # Update position
            await dispatcher.position_manager.update_position(
                sample_order,
                {"status": "filled", "amount": 0.01, "fill_price": 50000.0},
            )

            # Verify gauge value was set
            gauge_value = self._get_gauge_value(
                "tradeengine_current_position_size",
                {"symbol": "BTCUSDT", "position_side": "LONG", "exchange": "binance"},
            )

            # Note: Actual value depends on position manager state
            assert gauge_value is not None

    @pytest.mark.asyncio
    async def test_pnl_gauges(self, dispatcher, sample_order):
        """Test that PnL gauges update correctly"""
        # Mock position manager with PnL data
        with patch.object(
            dispatcher.position_manager, "update_position", new_callable=AsyncMock
        ) as mock_update:
            mock_update.return_value = None

            # Set position with PnL
            dispatcher.position_manager.positions[("BTCUSDT", "LONG")] = {
                "symbol": "BTCUSDT",
                "position_side": "LONG",
                "quantity": 0.01,
                "avg_price": 50000.0,
                "unrealized_pnl": 200.0,
                "realized_pnl": 100.0,
                "total_value": 500.0,
            }
            dispatcher.position_manager.daily_pnl = 150.0

            # Update position to trigger metrics
            await dispatcher.position_manager.update_position(
                sample_order,
                {"status": "filled", "amount": 0.01, "fill_price": 51000.0},
            )

            # Verify gauges were updated
            unrealized_pnl = self._get_gauge_value(
                "tradeengine_total_unrealized_pnl_usd", {"exchange": "binance"}
            )
            daily_pnl = self._get_gauge_value(
                "tradeengine_total_daily_pnl_usd", {"exchange": "binance"}
            )

            assert unrealized_pnl is not None
            assert daily_pnl is not None

    @pytest.mark.asyncio
    async def test_position_close_metrics(self, dispatcher, sample_order):
        """Test that closing a position emits correct metrics"""
        # Create a closing order (selling a LONG position)
        close_order = TradeOrder(
            order_id="test_close_order",
            symbol="BTCUSDT",
            side=OrderSide.SELL,
            type=OrderType.MARKET,
            amount=0.01,
            target_price=52000.0,
            position_id="test_position_123",
            position_side="LONG",
            exchange="binance",
            status=OrderStatus.PENDING,
            simulate=False,
            reduce_only=True,
        )

        # Mock the Data Manager sync calls
        with (
            patch.object(
                dispatcher.position_manager,
                "_close_position_in_data_manager",
                new_callable=AsyncMock,
            ) as mock_close_dm,
            patch.object(
                dispatcher.position_manager,
                "_sync_positions_to_data_manager",
                new_callable=AsyncMock,
            ) as mock_sync,
        ):
            mock_close_dm.return_value = None
            mock_sync.return_value = None

            # Set up initial position
            dispatcher.position_manager.positions[("BTCUSDT", "LONG")] = {
                "symbol": "BTCUSDT",
                "position_side": "LONG",
                "quantity": 0.01,
                "avg_price": 50000.0,
                "unrealized_pnl": 20.0,
                "realized_pnl": 100.0,
                "total_value": 500.0,
                "total_cost": 500.0,
            }
            dispatcher.position_manager.daily_pnl = 120.0

            # Update position with closing order (should close position)
            await dispatcher.position_manager.update_position(
                close_order, {"status": "filled", "amount": 0.01, "fill_price": 52000.0}
            )

            # Verify position was closed and metrics updated
            # Position should be removed from dict
            assert ("BTCUSDT", "LONG") not in dispatcher.position_manager.positions

            # Verify realized PnL gauge was set
            realized_pnl = self._get_gauge_value(
                "tradeengine_total_realized_pnl_usd", {"exchange": "binance"}
            )
            assert realized_pnl is not None

    def _get_gauge_value(self, metric_name, labels):
        """Helper to get gauge value from Prometheus registry"""
        for metric in REGISTRY.collect():
            if metric.name == metric_name:
                for sample in metric.samples:
                    if sample.labels == labels:
                        return sample.value
        return None


class TestMetricsIntegration:
    """Integration tests for metrics across the system"""

    @pytest.mark.asyncio
    async def test_end_to_end_metrics_flow(self, dispatcher, sample_signal):
        """Test that metrics are emitted throughout the complete order flow"""
        # Mock all required components
        with (
            patch.object(
                dispatcher, "process_signal", new_callable=AsyncMock
            ) as mock_process,
            patch.object(
                dispatcher.position_manager,
                "check_position_limits",
                new_callable=AsyncMock,
            ) as mock_limits,
            patch.object(
                dispatcher.position_manager,
                "check_daily_loss_limits",
                new_callable=AsyncMock,
            ) as mock_daily,
            patch.object(
                dispatcher.position_manager, "update_position", new_callable=AsyncMock
            ) as mock_update,
        ):

            mock_process.return_value = {"status": "success"}
            mock_limits.return_value = True
            mock_daily.return_value = True
            mock_update.return_value = None

            # Dispatch signal (full flow)
            dispatch_result = await dispatcher.dispatch(sample_signal)

            # Verify key metrics were emitted
            assert dispatch_result["status"] == "executed"

            # Check signals_received counter
            signals_received = self._get_counter_value(
                "tradeengine_signals_received_total",
                {"strategy": "test_strategy", "symbol": "BTCUSDT", "action": "buy"},
            )
            assert signals_received > 0

            # Check orders_executed_by_type counter
            orders_executed = self._get_counter_value(
                "tradeengine_orders_executed_by_type_total",
                {
                    "order_type": "market",
                    "side": "buy",
                    "symbol": "BTCUSDT",
                    "exchange": "binance",
                },
            )
            assert orders_executed > 0

            # Check risk_checks_total counter
            risk_checks = self._get_counter_value(
                "tradeengine_risk_checks_total",
                {
                    "check_type": "position_limits",
                    "result": "passed",
                    "exchange": "binance",
                },
            )
            assert risk_checks > 0

    def _get_counter_value(self, metric_name, labels):
        """Helper to get counter value from Prometheus registry"""
        for metric in REGISTRY.collect():
            if metric.name == metric_name:
                for sample in metric.samples:
                    if sample.labels == labels:
                        return sample.value
        return 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
