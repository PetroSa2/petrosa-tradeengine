"""
Additional tests for position_manager.py to increase coverage to 61%

Focus on uncovered methods:
- _refresh_positions_from_data_manager
- _calculate_unrealized_pnl
- _calculate_realized_pnl
- _update_position_risk_orders
- _validate_position_data
- _sync_position_to_data_manager
- health_check
- get_metrics
- get_daily_pnl
- get_total_unrealized_pnl
- get_portfolio_summary
- reset_daily_pnl
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime, timedelta

from tradeengine.position_manager import PositionManager


@pytest.fixture
def position_manager():
    """Create PositionManager instance for testing"""
    with patch('tradeengine.position_manager.DataManagerPositionClient') as mock_client_class:
        mock_client = Mock()
        mock_client_class.return_value = mock_client
        pm = PositionManager()
        pm.position_client = mock_client
        return pm


@pytest.fixture
def sample_position():
    """Sample position data for testing"""
    return {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "quantity": 0.1,
        "entry_price": 50000.0,
        "current_price": 51000.0,
        "unrealized_pnl": 100.0,
        "realized_pnl": 0.0,
        "created_at": datetime.utcnow(),
    }


class TestRefreshPositions:
    """Test _refresh_positions_from_data_manager"""

    @pytest.mark.asyncio
    async def test_refresh_positions_success(self, position_manager):
        """Test successful refresh from data manager"""
        mock_positions = [
            {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "quantity": 0.1,
                "entry_price": 50000.0,
            },
            {
                "symbol": "ETHUSDT",
                "side": "SHORT",
                "quantity": 1.0,
                "entry_price": 3000.0,
            },
        ]
        position_manager.position_client.get_open_positions = AsyncMock(return_value=mock_positions)
        
        await position_manager._refresh_positions_from_data_manager()
        
        assert len(position_manager.positions) == 2
        assert "BTCUSDT" in position_manager.positions
        assert "ETHUSDT" in position_manager.positions

    @pytest.mark.asyncio
    async def test_refresh_positions_empty(self, position_manager):
        """Test refresh with no positions"""
        position_manager.position_client.get_open_positions = AsyncMock(return_value=[])
        
        await position_manager._refresh_positions_from_data_manager()
        
        assert len(position_manager.positions) == 0

    @pytest.mark.asyncio
    async def test_refresh_positions_error(self, position_manager):
        """Test refresh handles errors gracefully"""
        position_manager.position_client.get_open_positions = AsyncMock(side_effect=Exception("DB error"))
        
        # Should not raise exception
        await position_manager._refresh_positions_from_data_manager()
        # Positions should remain unchanged or empty
        assert isinstance(position_manager.positions, dict)


class TestCalculatePnL:
    """Test PnL calculation methods"""

    def test_calculate_unrealized_pnl_long_profit(self, position_manager):
        """Test unrealized PnL calculation for LONG position with profit"""
        position = {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "quantity": 0.1,
            "entry_price": 50000.0,
            "current_price": 51000.0,
        }
        
        pnl = position_manager._calculate_unrealized_pnl(position)
        expected = (51000.0 - 50000.0) * 0.1
        assert pnl == pytest.approx(expected, rel=0.01)

    def test_calculate_unrealized_pnl_long_loss(self, position_manager):
        """Test unrealized PnL calculation for LONG position with loss"""
        position = {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "quantity": 0.1,
            "entry_price": 50000.0,
            "current_price": 49000.0,
        }
        
        pnl = position_manager._calculate_unrealized_pnl(position)
        expected = (49000.0 - 50000.0) * 0.1
        assert pnl == pytest.approx(expected, rel=0.01)

    def test_calculate_unrealized_pnl_short_profit(self, position_manager):
        """Test unrealized PnL calculation for SHORT position with profit"""
        position = {
            "symbol": "BTCUSDT",
            "side": "SHORT",
            "quantity": 0.1,
            "entry_price": 50000.0,
            "current_price": 49000.0,
        }
        
        pnl = position_manager._calculate_unrealized_pnl(position)
        expected = (50000.0 - 49000.0) * 0.1
        assert pnl == pytest.approx(expected, rel=0.01)

    def test_calculate_unrealized_pnl_short_loss(self, position_manager):
        """Test unrealized PnL calculation for SHORT position with loss"""
        position = {
            "symbol": "BTCUSDT",
            "side": "SHORT",
            "quantity": 0.1,
            "entry_price": 50000.0,
            "current_price": 51000.0,
        }
        
        pnl = position_manager._calculate_unrealized_pnl(position)
        expected = (50000.0 - 51000.0) * 0.1
        assert pnl == pytest.approx(expected, rel=0.01)

    def test_calculate_unrealized_pnl_no_current_price(self, position_manager):
        """Test unrealized PnL with no current price"""
        position = {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "quantity": 0.1,
            "entry_price": 50000.0,
            "current_price": None,
        }
        
        pnl = position_manager._calculate_unrealized_pnl(position)
        assert pnl == 0.0

    def test_calculate_realized_pnl_long(self, position_manager):
        """Test realized PnL calculation for LONG position"""
        position = {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "quantity": 0.1,
            "entry_price": 50000.0,
            "exit_price": 51000.0,
        }
        
        pnl = position_manager._calculate_realized_pnl(position, 0.1, 51000.0)
        expected = (51000.0 - 50000.0) * 0.1
        assert pnl == pytest.approx(expected, rel=0.01)

    def test_calculate_realized_pnl_short(self, position_manager):
        """Test realized PnL calculation for SHORT position"""
        position = {
            "symbol": "BTCUSDT",
            "side": "SHORT",
            "quantity": 0.1,
            "entry_price": 50000.0,
            "exit_price": 49000.0,
        }
        
        pnl = position_manager._calculate_realized_pnl(position, 0.1, 49000.0)
        expected = (50000.0 - 49000.0) * 0.1
        assert pnl == pytest.approx(expected, rel=0.01)

    def test_calculate_realized_pnl_partial_close(self, position_manager):
        """Test realized PnL for partial position close"""
        position = {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "quantity": 0.2,
            "entry_price": 50000.0,
        }
        
        pnl = position_manager._calculate_realized_pnl(position, 0.1, 51000.0)
        expected = (51000.0 - 50000.0) * 0.1
        assert pnl == pytest.approx(expected, rel=0.01)


class TestUpdatePositionRiskOrders:
    """Test _update_position_risk_orders"""

    @pytest.mark.asyncio
    async def test_update_position_risk_orders_success(self, position_manager):
        """Test updating risk orders for a position"""
        position = {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "quantity": 0.1,
            "entry_price": 50000.0,
            "stop_loss": 49000.0,
            "take_profit": 51000.0,
        }
        position_manager.positions["BTCUSDT"] = position
        
        # Mock order manager
        position_manager.order_manager = Mock()
        position_manager.order_manager.update_risk_orders = AsyncMock()
        
        await position_manager._update_position_risk_orders("BTCUSDT", 49000.0, 51000.0)
        
        position_manager.order_manager.update_risk_orders.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_position_risk_orders_no_position(self, position_manager):
        """Test updating risk orders when position doesn't exist"""
        position_manager.order_manager = Mock()
        position_manager.order_manager.update_risk_orders = AsyncMock()
        
        await position_manager._update_position_risk_orders("BTCUSDT", 49000.0, 51000.0)
        
        # Should handle gracefully without error
        assert True


class TestValidatePositionData:
    """Test _validate_position_data"""

    def test_validate_position_data_valid(self, position_manager):
        """Test validation with valid position data"""
        position_data = {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "quantity": 0.1,
            "entry_price": 50000.0,
        }
        
        result = position_manager._validate_position_data(position_data)
        assert result is True

    def test_validate_position_data_missing_symbol(self, position_manager):
        """Test validation with missing symbol"""
        position_data = {
            "side": "LONG",
            "quantity": 0.1,
            "entry_price": 50000.0,
        }
        
        result = position_manager._validate_position_data(position_data)
        assert result is False

    def test_validate_position_data_missing_side(self, position_manager):
        """Test validation with missing side"""
        position_data = {
            "symbol": "BTCUSDT",
            "quantity": 0.1,
            "entry_price": 50000.0,
        }
        
        result = position_manager._validate_position_data(position_data)
        assert result is False

    def test_validate_position_data_invalid_quantity(self, position_manager):
        """Test validation with invalid quantity"""
        position_data = {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "quantity": 0,
            "entry_price": 50000.0,
        }
        
        result = position_manager._validate_position_data(position_data)
        assert result is False

    def test_validate_position_data_invalid_price(self, position_manager):
        """Test validation with invalid entry price"""
        position_data = {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "quantity": 0.1,
            "entry_price": -100.0,
        }
        
        result = position_manager._validate_position_data(position_data)
        assert result is False


class TestSyncPosition:
    """Test _sync_position_to_data_manager"""

    @pytest.mark.asyncio
    async def test_sync_position_success(self, position_manager):
        """Test successful position sync to data manager"""
        position = {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "quantity": 0.1,
            "entry_price": 50000.0,
        }
        position_manager.positions["BTCUSDT"] = position
        position_manager.position_client.upsert_position = AsyncMock()
        
        await position_manager._sync_position_to_data_manager("BTCUSDT")
        
        position_manager.position_client.upsert_position.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_position_no_position(self, position_manager):
        """Test sync when position doesn't exist"""
        position_manager.position_client.upsert_position = AsyncMock()
        
        await position_manager._sync_position_to_data_manager("BTCUSDT")
        
        # Should handle gracefully
        assert True

    @pytest.mark.asyncio
    async def test_sync_position_error(self, position_manager):
        """Test sync handles errors gracefully"""
        position = {
            "symbol": "BTCUSDT",
            "side": "LONG",
            "quantity": 0.1,
            "entry_price": 50000.0,
        }
        position_manager.positions["BTCUSDT"] = position
        position_manager.position_client.upsert_position = AsyncMock(side_effect=Exception("DB error"))
        
        # Should not raise exception
        await position_manager._sync_position_to_data_manager("BTCUSDT")


class TestHealthCheck:
    """Test health_check method"""

    @pytest.mark.asyncio
    async def test_health_check_healthy(self, position_manager):
        """Test health check when system is healthy"""
        position_manager.position_client.health_check = AsyncMock(return_value={"status": "healthy"})
        
        result = await position_manager.health_check()
        
        assert result["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_health_check_unhealthy(self, position_manager):
        """Test health check when system is unhealthy"""
        position_manager.position_client.health_check = AsyncMock(return_value={"status": "unhealthy"})
        
        result = await position_manager.health_check()
        
        assert result["status"] == "unhealthy"

    @pytest.mark.asyncio
    async def test_health_check_error(self, position_manager):
        """Test health check handles errors"""
        position_manager.position_client.health_check = AsyncMock(side_effect=Exception("Connection error"))
        
        result = await position_manager.health_check()
        
        assert result["status"] == "error" or "error" in result


class TestGetMetrics:
    """Test get_metrics method"""

    def test_get_metrics_with_positions(self, position_manager):
        """Test getting metrics with positions"""
        position_manager.positions = {
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "quantity": 0.1,
                "entry_price": 50000.0,
                "current_price": 51000.0,
                "unrealized_pnl": 100.0,
            },
        }
        position_manager.daily_pnl = 50.0
        
        metrics = position_manager.get_metrics()
        
        assert "total_positions" in metrics
        assert "daily_pnl" in metrics
        assert metrics["total_positions"] == 1

    def test_get_metrics_no_positions(self, position_manager):
        """Test getting metrics with no positions"""
        position_manager.positions = {}
        position_manager.daily_pnl = 0.0
        
        metrics = position_manager.get_metrics()
        
        assert metrics["total_positions"] == 0
        assert metrics["daily_pnl"] == 0.0


class TestPortfolioSummary:
    """Test portfolio summary methods"""

    def test_get_daily_pnl(self, position_manager):
        """Test getting daily PnL"""
        position_manager.daily_pnl = 150.0
        
        result = position_manager.get_daily_pnl()
        assert result == 150.0

    def test_get_total_unrealized_pnl(self, position_manager):
        """Test getting total unrealized PnL"""
        position_manager.positions = {
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "unrealized_pnl": 100.0,
            },
            "ETHUSDT": {
                "symbol": "ETHUSDT",
                "unrealized_pnl": 50.0,
            },
        }
        
        result = position_manager.get_total_unrealized_pnl()
        assert result == pytest.approx(150.0, rel=0.01)

    def test_get_portfolio_summary(self, position_manager):
        """Test getting portfolio summary"""
        position_manager.positions = {
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "quantity": 0.1,
                "unrealized_pnl": 100.0,
            },
        }
        position_manager.daily_pnl = 50.0
        
        summary = position_manager.get_portfolio_summary()
        
        assert "total_positions" in summary
        assert "daily_pnl" in summary
        assert "total_unrealized_pnl" in summary
        assert summary["total_positions"] == 1

    def test_reset_daily_pnl(self, position_manager):
        """Test resetting daily PnL"""
        position_manager.daily_pnl = 150.0
        
        position_manager.reset_daily_pnl()
        
        assert position_manager.daily_pnl == 0.0

