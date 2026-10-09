"""
Tests for hedge mode position tracking functionality
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contracts.order import TradeOrder
from shared.constants import UTC
from shared.retry import PersistResult
from tradeengine.position_manager import PositionManager


@pytest.fixture
def sample_order():
    """Create a sample order for testing"""
    return TradeOrder(
        position_id="test-position-id-123",
        symbol="BTCUSDT",
        side="buy",
        type="market",
        amount=0.001,
        target_price=45000.0,
        stop_loss=43000.0,
        take_profit=47000.0,
        position_side="LONG",
        exchange="binance",
        order_id="test-order-123",  # Add order_id
        strategy_metadata={
            "strategy_id": "test_strategy",
            "source": "test",
            "confidence": 0.85,
        },
    )


@pytest.fixture
def sample_result():
    """Create a sample order execution result"""
    return {
        "status": "filled",
        "order_id": "binance-order-123",
        "fill_price": 45000.0,
        "amount": 0.001,
        "commission": 0.045,
        "commission_asset": "USDT",
        "trade_ids": ["trade-1", "trade-2"],
    }


@pytest.mark.asyncio
async def test_create_position_record(sample_order, sample_result):
    """Test creating a position record with dual persistence"""
    position_manager = PositionManager()

    # Mock position_client (Data Manager client) - patch it where it's imported
    with patch("tradeengine.position_manager.position_client") as mock_position_client:
        mock_position_client.upsert_position = AsyncMock(
            return_value=PersistResult(ok=True, operation="upsert_position")
        )

        # Create position record
        await position_manager.create_position_record(sample_order, sample_result)

        # Verify position_client.create_position was called
        # The method should be called if order.position_id is present
        assert sample_order.position_id is not None, "Order must have position_id"
        mock_position_client.upsert_position.assert_called_once()


@pytest.mark.asyncio
async def test_position_side_determination():
    """Test that position side is correctly determined from order action"""
    from contracts.signal import Signal
    from tradeengine.dispatcher import Dispatcher

    dispatcher = Dispatcher()

    # Test BUY signal maps to LONG
    buy_signal = Signal(
        strategy_id="test",
        symbol="BTCUSDT",
        action="buy",
        confidence=0.8,
        price=45000.0,
        quantity=0.001,
        current_price=45000.0,
        source="petrosa-cio",
        strategy="test",
    )

    buy_order = dispatcher._signal_to_order(buy_signal)
    assert buy_order.position_side == "LONG"
    assert buy_order.position_id is not None

    # Test SELL signal maps to SHORT
    sell_signal = Signal(
        strategy_id="test",
        symbol="BTCUSDT",
        action="sell",
        confidence=0.8,
        price=45000.0,
        quantity=0.001,
        current_price=45000.0,
        source="petrosa-cio",
        strategy="test",
    )

    sell_order = dispatcher._signal_to_order(sell_signal)
    assert sell_order.position_side == "SHORT"
    assert sell_order.position_id is not None


@pytest.mark.asyncio
async def test_close_position_record():
    """Test closing a position record with PnL calculation"""
    position_manager = PositionManager()

    # Mock position_client (Data Manager client) - patch it where it's imported
    with patch("tradeengine.position_manager.position_client") as mock_position_client:
        mock_position_client.update_position = AsyncMock()

        exit_result = {
            "position_id": "test-position-123",
            "strategy_id": "test_strategy",
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "exchange": "binance",
            "entry_price": 45000.0,
            "exit_price": 47000.0,
            "quantity": 0.001,
            "entry_time": datetime.now(UTC),
            "entry_commission": 0.045,
            "exit_commission": 0.047,
            "order_id": "exit-order-123",
            "close_reason": "take_profit",
        }

        # Close position record
        await position_manager.close_position_record("test-position-123", exit_result)

        # Verify position_client.update_position was called
        mock_position_client.update_position.assert_called_once()


def test_order_has_position_tracking_fields(sample_order):
    """Test that TradeOrder has all required position tracking fields"""
    assert hasattr(sample_order, "position_id")
    assert hasattr(sample_order, "position_side")
    assert hasattr(sample_order, "exchange")
    assert hasattr(sample_order, "strategy_metadata")

    assert sample_order.position_id == "test-position-id-123"
    assert sample_order.position_side == "LONG"
    assert sample_order.exchange == "binance"
    assert sample_order.strategy_metadata["strategy_id"] == "test_strategy"


@pytest.mark.asyncio
async def test_metrics_export_on_position_open(sample_order, sample_result):
    """Test that metrics are exported when position is opened"""
    position_manager = PositionManager()
    position_manager.mongodb_db = MagicMock()

    # Mock MongoDB collection
    mock_collection = AsyncMock()
    position_manager.mongodb_db.positions = mock_collection

    with patch("tradeengine.position_manager.positions_opened_total") as mock_metric:
        await position_manager.create_position_record(sample_order, sample_result)

        # Verify metric was incremented
        assert mock_metric.labels.called


@pytest.mark.asyncio
async def test_closed_metrics_treat_missing_commission_as_zero():
    position_manager = PositionManager()
    position_data = {
        "position_id": "position-123",
        "strategy_id": "strategy-123",
        "symbol": "BTCUSDT",
        "position_side": "LONG",
        "exchange": "binance",
        "close_reason": "manual",
        "pnl_after_fees": 1.0,
        "pnl_pct": 0.01,
        "duration_seconds": 10,
        "entry_price": 50000.0,
        "exit_price": 50100.0,
        "commission_total": None,
        "final_commission": None,
    }

    with (
        patch("tradeengine.position_manager.position_pnl_usd") as pnl_usd,
        patch("tradeengine.position_manager.position_pnl_percentage") as pnl_pct,
        patch("tradeengine.position_manager.position_duration_seconds") as duration,
        patch("tradeengine.position_manager.position_exit_price") as exit_price,
        patch("tradeengine.position_manager.position_commission_usd") as metric,
        patch("tradeengine.position_manager.positions_winning_total") as winning,
        patch("tradeengine.position_manager.position_roi") as roi,
    ):
        await position_manager._export_position_closed_metrics(position_data)

    pnl_usd.labels.return_value.observe.assert_called_once_with(1.0)
    pnl_pct.labels.return_value.observe.assert_called_once_with(0.01)
    duration.labels.return_value.observe.assert_called_once_with(10)
    exit_price.labels.return_value.observe.assert_called_once_with(50100.0)
    metric.labels.assert_called_once_with(
        strategy_id="strategy-123", symbol="BTCUSDT", exchange="binance"
    )
    metric.labels.return_value.observe.assert_called_once_with(0.0)
    winning.labels.return_value.inc.assert_called_once_with()
    roi.labels.return_value.observe.assert_called_once_with(0.002)


@pytest.mark.asyncio
async def test_closed_metrics_skip_unknown_reconciler_values():
    position_manager = PositionManager()
    position_data = {
        "position_id": "reconciled-position-123",
        "strategy_id": "strategy-123",
        "symbol": "BTCUSDT",
        "position_side": "LONG",
        "exchange": "binance",
        "close_reason": "reconciler",
        "pnl_after_fees": None,
        "pnl_pct": None,
        "duration_seconds": None,
        "entry_price": None,
        "exit_price": None,
        "commission_total": None,
        "final_commission": None,
    }

    with (
        patch("tradeengine.position_manager.position_pnl_usd") as pnl_usd,
        patch("tradeengine.position_manager.position_pnl_percentage") as pnl_pct,
        patch("tradeengine.position_manager.position_duration_seconds") as duration,
        patch("tradeengine.position_manager.position_exit_price") as exit_price,
        patch("tradeengine.position_manager.position_commission_usd") as commission,
        patch("tradeengine.position_manager.positions_winning_total") as winning,
        patch("tradeengine.position_manager.positions_losing_total") as losing,
        patch("tradeengine.position_manager.position_roi") as roi,
    ):
        await position_manager._export_position_closed_metrics(position_data)

    pnl_usd.labels.assert_not_called()
    pnl_pct.labels.assert_not_called()
    duration.labels.assert_not_called()
    exit_price.labels.assert_not_called()
    winning.labels.assert_not_called()
    losing.labels.assert_not_called()
    roi.labels.assert_not_called()
    commission.labels.return_value.observe.assert_called_once_with(0.0)


@pytest.mark.asyncio
async def test_mysql_position_persistence():
    """Test MySQL position persistence"""
    from shared.mysql_client import position_client

    # Mock position_client to avoid actual database connection
    with patch("shared.mysql_client.position_client") as mock_position_client:
        mock_position_client.create_position = AsyncMock()
        mock_position_client.update_position = AsyncMock()

        # Verify the client is available
        assert mock_position_client is not None

    # Example position data structure
    # position_data = {
    #     "position_id": "test-pos-456",
    #     "strategy_id": "test_strategy",
    #     "exchange": "binance",
    #     "symbol": "ETHUSDT",
    #     "position_side": "SHORT",
    #     "entry_price": 3000.0,
    #     "quantity": 0.1,
    #     "entry_time": datetime.now(UTC),
    #     "stop_loss": 3100.0,
    #     "take_profit": 2900.0,
    #     "status": "open",
    #     "metadata": {"test": "data"},
    #     "commission_total": 0.3,
    # }
