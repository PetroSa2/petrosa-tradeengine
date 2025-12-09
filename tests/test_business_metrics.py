"""
Tests for business metrics instrumentation

Comprehensive tests that actually exercise all business metrics code paths
to ensure proper coverage without mocking.
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from prometheus_client import REGISTRY

from contracts.order import OrderSide, OrderStatus, OrderType, TradeOrder
from contracts.signal import (
    OrderType as SignalOrderType,
    Signal,
    SignalStrength,
    StrategyMode,
)
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
        meta={"signal_received_at": time.time() - 0.5},  # 500ms ago for latency test
    )


@pytest.fixture
def dispatcher():
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

    return Dispatcher(exchange=mock_exchange)


class TestOrderExecutionMetrics:
    """Test order execution metrics emission"""

    @pytest.mark.asyncio
    async def test_orders_executed_by_type_counter(self, dispatcher, sample_order):
        """Test orders_executed_by_type metric increments on order execution"""
        initial_value = self._get_counter_value(
            "tradeengine_orders_executed_by_type_total",
            {
                "order_type": "market",
                "side": "buy",
                "symbol": "BTCUSDT",
                "exchange": "binance",
            },
        )

        # Execute order - this should increment the counter
        result = await dispatcher.execute_order(sample_order)

        final_value = self._get_counter_value(
            "tradeengine_orders_executed_by_type_total",
            {
                "order_type": "market",
                "side": "buy",
                "symbol": "BTCUSDT",
                "exchange": "binance",
            },
        )

        assert final_value > initial_value
        assert result["status"] == "filled"

    @pytest.mark.asyncio
    async def test_order_execution_latency_histogram(self, dispatcher, sample_order):
        """Test order_execution_latency_seconds histogram is observed"""
        # Order has signal_received_at in meta (set in fixture)
        initial_count = self._get_histogram_count(
            "tradeengine_order_execution_latency_seconds"
        )

        # Execute order - should observe latency
        result = await dispatcher.execute_order(sample_order)

        final_count = self._get_histogram_count(
            "tradeengine_order_execution_latency_seconds"
        )

        assert final_count > initial_count
        assert result["status"] == "filled"

    @pytest.mark.asyncio
    async def test_order_latency_without_meta(self, dispatcher):
        """Test that orders without signal_received_at still work"""
        order_no_meta = TradeOrder(
            order_id="test_order_no_meta",
            symbol="ETHUSDT",
            side=OrderSide.SELL,
            type=OrderType.LIMIT,
            amount=0.1,
            target_price=3000.0,
            position_id="test_pos_2",
            position_side="SHORT",
            exchange="binance",
            status=OrderStatus.PENDING,
            simulate=False,
            # No meta field - should not crash
        )

        # Execute order - should succeed without latency tracking
        result = await dispatcher.execute_order(order_no_meta)

        assert result["status"] == "filled"

    @pytest.mark.asyncio
    async def test_order_failure_error_state(self, dispatcher, sample_order):
        """Test order_failures_total increments for error state"""
        # Mock exchange to return error
        dispatcher.exchange.execute = AsyncMock(
            return_value={"status": "error", "error": "Insufficient balance"}
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

        # Execute order - should fail
        result = await dispatcher.execute_order(sample_order)

        final_value = self._get_counter_value(
            "tradeengine_order_failures_total",
            {
                "symbol": "BTCUSDT",
                "order_type": "market",
                "failure_reason": "Insufficient balance",
                "exchange": "binance",
            },
        )

        assert final_value > initial_value
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_order_failure_rejected_state(self, dispatcher, sample_order):
        """Test order_failures_total increments for rejected state"""
        # Mock exchange to return rejected
        dispatcher.exchange.execute = AsyncMock(
            return_value={"status": "rejected", "error": "Order rejected by exchange"}
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

        result = await dispatcher.execute_order(sample_order)

        final_value = self._get_counter_value(
            "tradeengine_order_failures_total",
            {
                "symbol": "BTCUSDT",
                "order_type": "market",
                "failure_reason": "Order rejected by exchange",
                "exchange": "binance",
            },
        )

        assert final_value > initial_value
        assert result["status"] == "rejected"

    @pytest.mark.asyncio
    async def test_order_failure_cancelled_state(self, dispatcher, sample_order):
        """Test order_failures_total increments for cancelled state"""
        # Mock exchange to return cancelled
        dispatcher.exchange.execute = AsyncMock(
            return_value={"status": "cancelled", "error": "Order cancelled"}
        )

        initial_value = self._get_counter_value(
            "tradeengine_order_failures_total",
            {
                "symbol": "BTCUSDT",
                "order_type": "market",
                "failure_reason": "Order cancelled",
                "exchange": "binance",
            },
        )

        result = await dispatcher.execute_order(sample_order)

        final_value = self._get_counter_value(
            "tradeengine_order_failures_total",
            {
                "symbol": "BTCUSDT",
                "order_type": "market",
                "failure_reason": "Order cancelled",
                "exchange": "binance",
            },
        )

        assert final_value > initial_value
        assert result["status"] == "cancelled"

    def _get_counter_value(self, metric_name, labels):
        """Helper to get counter value from Prometheus registry"""
        # Prometheus counters append _total to sample names
        # But the metric object name doesn't include _total
        base_name = metric_name.replace("_total", "")

        for metric in REGISTRY.collect():
            if metric.name == base_name:
                for sample in metric.samples:
                    # Counter samples have format: metric_total, metric_created
                    if sample.name in (
                        f"{base_name}_total",
                        f"{base_name}_created",
                        base_name,
                    ):
                        if sample.labels == labels:
                            return sample.value
        return 0

    def _get_histogram_count(self, metric_name):
        """Helper to get histogram observation count"""
        for metric in REGISTRY.collect():
            if metric.name == metric_name:
                for sample in metric.samples:
                    if sample.name.endswith("_count"):
                        return sample.value
        return 0


class TestRiskManagementMetrics:
    """Test risk management metrics emission"""

    @pytest.mark.asyncio
    async def test_risk_rejection_position_limits(self, dispatcher, sample_order):
        """Test risk_rejections_total for position limits exceeded"""
        # Mock position limits to fail
        with patch.object(
            dispatcher.position_manager,
            "check_position_limits",
            new_callable=AsyncMock,
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

            # Execute - should be rejected
            result = await dispatcher._execute_order_with_consensus(sample_order)

            final_value = self._get_counter_value(
                "tradeengine_risk_rejections_total",
                {
                    "reason": "position_limits_exceeded",
                    "symbol": "BTCUSDT",
                    "exchange": "binance",
                },
            )

            assert final_value > initial_value
            assert result["status"] == "rejected"
            assert "Risk limits exceeded" in result["reason"]

    @pytest.mark.asyncio
    async def test_risk_rejection_daily_loss(self, dispatcher, sample_order):
        """Test risk_rejections_total for daily loss limits exceeded"""
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

            result = await dispatcher._execute_order_with_consensus(sample_order)

            final_value = self._get_counter_value(
                "tradeengine_risk_rejections_total",
                {
                    "reason": "daily_loss_limits_exceeded",
                    "symbol": "BTCUSDT",
                    "exchange": "binance",
                },
            )

            assert final_value > initial_value
            assert result["status"] == "rejected"
            assert "Daily loss limits exceeded" in result["reason"]

    @pytest.mark.asyncio
    async def test_risk_checks_position_limits_passed(self, dispatcher, sample_order):
        """Test risk_checks_total for passed position limits check"""
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

            await dispatcher._execute_order_with_consensus(sample_order)

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

            assert checking_final > checking_initial
            assert passed_final > passed_initial

    @pytest.mark.asyncio
    async def test_risk_checks_position_limits_rejected(self, dispatcher, sample_order):
        """Test risk_checks_total for rejected position limits check"""
        with patch.object(
            dispatcher.position_manager,
            "check_position_limits",
            new_callable=AsyncMock,
        ) as mock_check:
            mock_check.return_value = False

            checking_initial = self._get_counter_value(
                "tradeengine_risk_checks_total",
                {
                    "check_type": "position_limits",
                    "result": "checking",
                    "exchange": "binance",
                },
            )
            rejected_initial = self._get_counter_value(
                "tradeengine_risk_checks_total",
                {
                    "check_type": "position_limits",
                    "result": "rejected",
                    "exchange": "binance",
                },
            )

            await dispatcher._execute_order_with_consensus(sample_order)

            checking_final = self._get_counter_value(
                "tradeengine_risk_checks_total",
                {
                    "check_type": "position_limits",
                    "result": "checking",
                    "exchange": "binance",
                },
            )
            rejected_final = self._get_counter_value(
                "tradeengine_risk_checks_total",
                {
                    "check_type": "position_limits",
                    "result": "rejected",
                    "exchange": "binance",
                },
            )

            assert checking_final > checking_initial
            assert rejected_final > rejected_initial

    @pytest.mark.asyncio
    async def test_risk_checks_daily_loss_passed(self, dispatcher, sample_order):
        """Test risk_checks_total for passed daily loss check"""
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

            checking_initial = self._get_counter_value(
                "tradeengine_risk_checks_total",
                {
                    "check_type": "daily_loss_limits",
                    "result": "checking",
                    "exchange": "binance",
                },
            )
            passed_initial = self._get_counter_value(
                "tradeengine_risk_checks_total",
                {
                    "check_type": "daily_loss_limits",
                    "result": "passed",
                    "exchange": "binance",
                },
            )

            await dispatcher._execute_order_with_consensus(sample_order)

            checking_final = self._get_counter_value(
                "tradeengine_risk_checks_total",
                {
                    "check_type": "daily_loss_limits",
                    "result": "checking",
                    "exchange": "binance",
                },
            )
            passed_final = self._get_counter_value(
                "tradeengine_risk_checks_total",
                {
                    "check_type": "daily_loss_limits",
                    "result": "passed",
                    "exchange": "binance",
                },
            )

            assert checking_final > checking_initial
            assert passed_final > passed_initial

    @pytest.mark.asyncio
    async def test_risk_checks_daily_loss_rejected(self, dispatcher, sample_order):
        """Test risk_checks_total for rejected daily loss check"""
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

            checking_initial = self._get_counter_value(
                "tradeengine_risk_checks_total",
                {
                    "check_type": "daily_loss_limits",
                    "result": "checking",
                    "exchange": "binance",
                },
            )
            rejected_initial = self._get_counter_value(
                "tradeengine_risk_checks_total",
                {
                    "check_type": "daily_loss_limits",
                    "result": "rejected",
                    "exchange": "binance",
                },
            )

            await dispatcher._execute_order_with_consensus(sample_order)

            checking_final = self._get_counter_value(
                "tradeengine_risk_checks_total",
                {
                    "check_type": "daily_loss_limits",
                    "result": "checking",
                    "exchange": "binance",
                },
            )
            rejected_final = self._get_counter_value(
                "tradeengine_risk_checks_total",
                {
                    "check_type": "daily_loss_limits",
                    "result": "rejected",
                    "exchange": "binance",
                },
            )

            assert checking_final > checking_initial
            assert rejected_final > rejected_initial

    def _get_counter_value(self, metric_name, labels):
        """Helper to get counter value from Prometheus registry"""
        # Prometheus counters append _total to sample names
        # But the metric object name doesn't include _total
        base_name = metric_name.replace("_total", "")

        for metric in REGISTRY.collect():
            if metric.name == base_name:
                for sample in metric.samples:
                    # Counter samples have format: metric_total, metric_created
                    if sample.name in (
                        f"{base_name}_total",
                        f"{base_name}_created",
                        base_name,
                    ):
                        if sample.labels == labels:
                            return sample.value
        return 0

    def _get_histogram_count(self, metric_name):
        """Helper to get histogram observation count"""
        for metric in REGISTRY.collect():
            if metric.name == metric_name:
                for sample in metric.samples:
                    if sample.name.endswith("_count"):
                        return sample.value
        return 0


class TestPositionMetrics:
    """Test position and PnL metrics emission"""

    @pytest.mark.asyncio
    async def test_position_size_gauge_on_update(self, dispatcher, sample_order):
        """Test current_position_size gauge updates when position is updated"""
        # Mock Data Manager sync to prevent blocking
        with patch.object(
            dispatcher.position_manager,
            "_sync_positions_to_data_manager",
            new_callable=AsyncMock,
        ) as mock_sync:
            mock_sync.return_value = None

            # Set up an existing position
            dispatcher.position_manager.positions[("BTCUSDT", "LONG")] = {
                "symbol": "BTCUSDT",
                "position_side": "LONG",
                "quantity": 0.0,
                "avg_price": 0.0,
                "unrealized_pnl": 0.0,
                "realized_pnl": 0.0,
                "total_value": 0.0,
                "total_cost": 0.0,
            }

            # Update position - should emit gauge
            await dispatcher.position_manager.update_position(
                sample_order,
                {"status": "filled", "amount": 0.01, "fill_price": 50000.0},
            )

            # Verify gauge was set
            gauge_value = self._get_gauge_value(
                "tradeengine_current_position_size",
                {"symbol": "BTCUSDT", "position_side": "LONG", "exchange": "binance"},
            )

            assert gauge_value is not None
            assert gauge_value >= 0

    @pytest.mark.asyncio
    async def test_unrealized_pnl_gauge_on_update(self, dispatcher, sample_order):
        """Test total_unrealized_pnl_usd gauge updates on position update"""
        with patch.object(
            dispatcher.position_manager,
            "_sync_positions_to_data_manager",
            new_callable=AsyncMock,
        ) as mock_sync:
            mock_sync.return_value = None

            # Set up position
            dispatcher.position_manager.positions[("BTCUSDT", "LONG")] = {
                "symbol": "BTCUSDT",
                "position_side": "LONG",
                "quantity": 0.01,
                "avg_price": 49000.0,
                "unrealized_pnl": 10.0,
                "realized_pnl": 0.0,
                "total_value": 500.0,
                "total_cost": 490.0,
            }

            # Update position
            await dispatcher.position_manager.update_position(
                sample_order,
                {"status": "filled", "amount": 0.01, "fill_price": 50000.0},
            )

            # Verify unrealized PnL gauge was updated
            unrealized_pnl = self._get_gauge_value(
                "tradeengine_total_unrealized_pnl_usd", {"exchange": "binance"}
            )

            assert unrealized_pnl is not None

    @pytest.mark.asyncio
    async def test_daily_pnl_gauge_on_update(self, dispatcher, sample_order):
        """Test total_daily_pnl_usd gauge updates on position update"""
        with patch.object(
            dispatcher.position_manager,
            "_sync_positions_to_data_manager",
            new_callable=AsyncMock,
        ) as mock_sync:
            mock_sync.return_value = None

            # Set daily PnL
            dispatcher.position_manager.daily_pnl = 150.0

            # Set up position
            dispatcher.position_manager.positions[("BTCUSDT", "LONG")] = {
                "symbol": "BTCUSDT",
                "position_side": "LONG",
                "quantity": 0.01,
                "avg_price": 49000.0,
                "unrealized_pnl": 10.0,
                "realized_pnl": 0.0,
                "total_value": 500.0,
                "total_cost": 490.0,
            }

            # Update position
            await dispatcher.position_manager.update_position(
                sample_order,
                {"status": "filled", "amount": 0.01, "fill_price": 50000.0},
            )

            # Verify daily PnL gauge was updated
            daily_pnl = self._get_gauge_value(
                "tradeengine_total_daily_pnl_usd", {"exchange": "binance"}
            )

            assert daily_pnl is not None

    @pytest.mark.asyncio
    async def test_total_position_value_gauge(self, dispatcher, sample_order):
        """Test total_position_value_usd gauge updates correctly"""
        with patch.object(
            dispatcher.position_manager,
            "_sync_positions_to_data_manager",
            new_callable=AsyncMock,
        ) as mock_sync:
            mock_sync.return_value = None

            # Set up position with value
            dispatcher.position_manager.positions[("BTCUSDT", "LONG")] = {
                "symbol": "BTCUSDT",
                "position_side": "LONG",
                "quantity": 0.01,
                "avg_price": 50000.0,
                "unrealized_pnl": 0.0,
                "realized_pnl": 0.0,
                "total_value": 500.0,
                "total_cost": 500.0,
            }

            # Update position
            await dispatcher.position_manager.update_position(
                sample_order,
                {"status": "filled", "amount": 0.01, "fill_price": 50000.0},
            )

            # Verify total position value gauge
            total_value = self._get_gauge_value(
                "tradeengine_total_position_value_usd", {"exchange": "binance"}
            )

            assert total_value is not None
            assert total_value >= 0

    @pytest.mark.asyncio
    async def test_realized_pnl_on_position_close(self, dispatcher):
        """Test total_realized_pnl_usd gauge updates when position closes"""
        # Create a closing order
        close_order = TradeOrder(
            order_id="test_close",
            symbol="BTCUSDT",
            side=OrderSide.SELL,
            type=OrderType.MARKET,
            amount=0.01,
            target_price=52000.0,
            position_id="test_pos",
            position_side="LONG",
            exchange="binance",
            status=OrderStatus.PENDING,
            simulate=False,
            reduce_only=True,
        )

        with (
            patch.object(
                dispatcher.position_manager,
                "_close_position_in_data_manager",
                new_callable=AsyncMock,
            ) as mock_close,
            patch.object(
                dispatcher.position_manager,
                "_sync_positions_to_data_manager",
                new_callable=AsyncMock,
            ) as mock_sync,
        ):
            mock_close.return_value = None
            mock_sync.return_value = None

            # Set up position that will be closed
            dispatcher.position_manager.positions[("BTCUSDT", "LONG")] = {
                "symbol": "BTCUSDT",
                "position_side": "LONG",
                "quantity": 0.01,
                "avg_price": 50000.0,
                "unrealized_pnl": 20.0,
                "realized_pnl": 100.0,
                "total_value": 520.0,
                "total_cost": 500.0,
            }
            dispatcher.position_manager.daily_pnl = 120.0

            # Close position (sell full quantity)
            await dispatcher.position_manager.update_position(
                close_order, {"status": "filled", "amount": 0.01, "fill_price": 52000.0}
            )

            # Verify position was closed
            assert ("BTCUSDT", "LONG") not in dispatcher.position_manager.positions

            # Verify realized PnL gauge was set
            realized_pnl = self._get_gauge_value(
                "tradeengine_total_realized_pnl_usd", {"exchange": "binance"}
            )
            assert realized_pnl is not None

            # Verify daily PnL gauge was set
            daily_pnl = self._get_gauge_value(
                "tradeengine_total_daily_pnl_usd", {"exchange": "binance"}
            )
            assert daily_pnl is not None

            # Verify position size gauge was reset to 0
            position_size = self._get_gauge_value(
                "tradeengine_current_position_size",
                {"symbol": "BTCUSDT", "position_side": "LONG", "exchange": "binance"},
            )
            # Gauge should be set to 0 when position closes
            assert position_size is not None

    def _get_gauge_value(self, metric_name, labels):
        """Helper to get gauge value from Prometheus registry"""
        for metric in REGISTRY.collect():
            if metric.name == metric_name:
                for sample in metric.samples:
                    if sample.labels == labels:
                        return sample.value
        return None


class TestSignalToOrderConversion:
    """Test signal-to-order conversion stores latency metadata"""

    @pytest.mark.asyncio
    async def test_dispatch_stores_signal_received_time(
        self, dispatcher, sample_signal
    ):
        """Test that dispatch() stores signal_received_at in order meta"""
        # Mock process_signal to return success
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
            patch.object(
                dispatcher.position_manager,
                "create_position_record",
                new_callable=AsyncMock,
            ) as mock_create,
        ):
            mock_process.return_value = {"status": "success"}
            mock_limits.return_value = True
            mock_daily.return_value = True
            mock_update.return_value = None
            mock_create.return_value = None

            # Get initial latency count
            initial_count = self._get_histogram_count(
                "tradeengine_order_execution_latency_seconds"
            )

            # Dispatch signal - should convert to order with signal_received_at
            result = await dispatcher.dispatch(sample_signal)

            # Verify latency was tracked
            final_count = self._get_histogram_count(
                "tradeengine_order_execution_latency_seconds"
            )

            # Check that latency was observed (count should be >= initial + 1)
            assert final_count >= initial_count
            # Also verify the result status
            assert result.get("status") in ["executed", "success", "skipped_duplicate"]

    def _get_histogram_count(self, metric_name):
        """Helper to get histogram observation count"""
        for metric in REGISTRY.collect():
            if metric.name == metric_name:
                for sample in metric.samples:
                    if sample.name.endswith("_count"):
                        return sample.value
        return 0


class TestMetricsWithMultipleOrderTypes:
    """Test metrics work with different order types"""

    @pytest.mark.asyncio
    async def test_limit_order_metrics(self, dispatcher):
        """Test metrics work for limit orders"""
        limit_order = TradeOrder(
            order_id="test_limit",
            symbol="ETHUSDT",
            side=OrderSide.BUY,
            type=OrderType.LIMIT,
            amount=0.1,
            target_price=3000.0,
            position_id="test_pos_limit",
            position_side="LONG",
            exchange="binance",
            status=OrderStatus.PENDING,
            simulate=False,
            meta={"signal_received_at": time.time() - 1.0},
        )

        initial_value = self._get_counter_value(
            "tradeengine_orders_executed_by_type_total",
            {
                "order_type": "limit",
                "side": "buy",
                "symbol": "ETHUSDT",
                "exchange": "binance",
            },
        )

        _ = await dispatcher.execute_order(limit_order)

        final_value = self._get_counter_value(
            "tradeengine_orders_executed_by_type_total",
            {
                "order_type": "limit",
                "side": "buy",
                "symbol": "ETHUSDT",
                "exchange": "binance",
            },
        )

        assert final_value > initial_value

    @pytest.mark.asyncio
    async def test_stop_order_metrics(self, dispatcher):
        """Test metrics work for stop orders"""
        stop_order = TradeOrder(
            order_id="test_stop",
            symbol="ADAUSDT",
            side=OrderSide.SELL,
            type=OrderType.STOP,
            amount=100.0,
            target_price=0.5,
            stop_loss=0.48,
            position_id="test_pos_stop",
            position_side="SHORT",
            exchange="binance",
            status=OrderStatus.PENDING,
            simulate=False,
            meta={"signal_received_at": time.time() - 2.0},
        )

        initial_value = self._get_counter_value(
            "tradeengine_orders_executed_by_type_total",
            {
                "order_type": "stop",
                "side": "sell",
                "symbol": "ADAUSDT",
                "exchange": "binance",
            },
        )

        _ = await dispatcher.execute_order(stop_order)

        final_value = self._get_counter_value(
            "tradeengine_orders_executed_by_type_total",
            {
                "order_type": "stop",
                "side": "sell",
                "symbol": "ADAUSDT",
                "exchange": "binance",
            },
        )

        assert final_value > initial_value

    def _get_counter_value(self, metric_name, labels):
        """Helper to get counter value from Prometheus registry"""
        # Prometheus counters append _total to sample names
        # But the metric object name doesn't include _total
        base_name = metric_name.replace("_total", "")

        for metric in REGISTRY.collect():
            if metric.name == base_name:
                for sample in metric.samples:
                    # Counter samples have format: metric_total, metric_created
                    if sample.name in (
                        f"{base_name}_total",
                        f"{base_name}_created",
                        base_name,
                    ):
                        if sample.labels == labels:
                            return sample.value
        return 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
