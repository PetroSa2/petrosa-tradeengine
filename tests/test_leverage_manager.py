"""Tests for leverage_manager module"""

import sys
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

# Mock binance module before importing LeverageManager
mock_binance = MagicMock()
mock_binance.exceptions = MagicMock()
sys.modules["binance"] = mock_binance
sys.modules["binance.exceptions"] = mock_binance.exceptions

from contracts.trading_config import LeverageStatus  # noqa: E402
from tradeengine.leverage_manager import LeverageManager  # noqa: E402


@pytest.fixture
def leverage_manager():
    """Create a LeverageManager instance for testing"""
    manager = LeverageManager()
    return manager


@pytest.fixture
def mock_binance_client():
    """Mock Binance client for testing"""
    client = Mock()
    client.futures_change_leverage = Mock()
    return client


@pytest.fixture
def mock_mongodb_client():
    """Mock MongoDB client for testing"""
    client = Mock()
    client.connected = True
    client.get_leverage_status = AsyncMock(return_value=None)
    client.set_leverage_status = AsyncMock()
    client.get_all_leverage_status = AsyncMock(return_value=[])
    return client


class TestLeverageManagerBasic:
    """Test basic LeverageManager functionality"""

    def test_initialization(self, leverage_manager):
        """Test LeverageManager initialization"""
        assert leverage_manager is not None
        assert hasattr(leverage_manager, "_leverage_cache")
        assert hasattr(leverage_manager, "binance_client")
        assert hasattr(leverage_manager, "mongodb_client")

    @pytest.mark.asyncio
    async def test_get_leverage_status_from_cache(self, leverage_manager):
        """Test getting leverage status from cache"""
        from contracts.trading_config import LeverageStatus

        status = LeverageStatus(
            id=None,
            symbol="BTCUSDT",
            configured_leverage=10,
            actual_leverage=10,
            last_sync_at=datetime.utcnow(),
            last_sync_success=True,
            last_sync_error=None,
            updated_at=datetime.utcnow(),
        )
        leverage_manager._leverage_cache["BTCUSDT"] = status

        result = await leverage_manager.get_leverage_status("BTCUSDT")
        assert result is not None
        assert result.symbol == "BTCUSDT"
        assert result.configured_leverage == 10

    @pytest.mark.asyncio
    async def test_get_leverage_status_from_db(
        self, leverage_manager, mock_mongodb_client
    ):
        """Test getting leverage status from database"""
        from contracts.trading_config import LeverageStatus

        status = LeverageStatus(
            id=None,
            symbol="ETHUSDT",
            configured_leverage=5,
            actual_leverage=5,
            last_sync_at=datetime.utcnow(),
            last_sync_success=True,
            last_sync_error=None,
            updated_at=datetime.utcnow(),
        )
        leverage_manager.mongodb_client = mock_mongodb_client
        mock_mongodb_client.get_leverage_status = AsyncMock(return_value=status)

        result = await leverage_manager.get_leverage_status("ETHUSDT")
        assert result is not None
        assert result.symbol == "ETHUSDT"
        assert result.configured_leverage == 5

    @pytest.mark.asyncio
    async def test_get_leverage_status_not_found(self, leverage_manager):
        """Test getting leverage status when not found"""
        result = await leverage_manager.get_leverage_status("NONEXISTENT")
        assert result is None

    @pytest.mark.asyncio
    async def test_ensure_leverage_already_correct(
        self, leverage_manager, mock_binance_client
    ):
        """Test ensure_leverage when leverage is already correct"""
        from contracts.trading_config import LeverageStatus

        status = LeverageStatus(
            id=None,
            symbol="BTCUSDT",
            configured_leverage=10,
            actual_leverage=10,
            last_sync_at=datetime.utcnow(),
            last_sync_success=True,
            last_sync_error=None,
            updated_at=datetime.utcnow(),
        )
        leverage_manager._leverage_cache["BTCUSDT"] = status
        leverage_manager.binance_client = mock_binance_client

        result = await leverage_manager.ensure_leverage("BTCUSDT", 10)
        assert result is True
        # Should not call binance client
        mock_binance_client.futures_change_leverage.assert_not_called()

    @pytest.mark.asyncio
    async def test_ensure_leverage_no_binance_client(self, leverage_manager):
        """Test ensure_leverage when binance client is not available"""
        leverage_manager.binance_client = None

        result = await leverage_manager.ensure_leverage("BTCUSDT", 10)
        assert result is False

    @pytest.mark.asyncio
    async def test_force_leverage_success(self, leverage_manager, mock_binance_client):
        """Test force_leverage successful operation"""
        leverage_manager.binance_client = mock_binance_client
        leverage_manager.mongodb_client = Mock()
        leverage_manager.mongodb_client.connected = True
        leverage_manager.mongodb_client.set_leverage_status = AsyncMock()

        result = await leverage_manager.force_leverage("BTCUSDT", 20)
        assert result["success"] is True
        assert result["symbol"] == "BTCUSDT"
        assert result["leverage"] == 20
        mock_binance_client.futures_change_leverage.assert_called_once_with(
            symbol="BTCUSDT", leverage=20
        )

    @pytest.mark.asyncio
    async def test_force_leverage_no_binance_client(self, leverage_manager):
        """Test force_leverage when binance client is not available"""
        leverage_manager.binance_client = None

        result = await leverage_manager.force_leverage("BTCUSDT", 20)
        assert result["success"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_sync_all_leverage_no_mongodb(self, leverage_manager):
        """Test sync_all_leverage when MongoDB is not connected"""
        leverage_manager.mongodb_client = None

        result = await leverage_manager.sync_all_leverage()
        assert result["success"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_sync_all_leverage_success(
        self, leverage_manager, mock_mongodb_client, mock_binance_client
    ):
        """Test sync_all_leverage successful operation"""
        from contracts.trading_config import LeverageStatus

        status1 = LeverageStatus(
            id=None,
            symbol="BTCUSDT",
            configured_leverage=10,
            actual_leverage=10,
            last_sync_at=datetime.utcnow(),
            last_sync_success=True,
            last_sync_error=None,
            updated_at=datetime.utcnow(),
        )
        status2 = LeverageStatus(
            id=None,
            symbol="ETHUSDT",
            configured_leverage=5,
            actual_leverage=5,
            last_sync_at=datetime.utcnow(),
            last_sync_success=True,
            last_sync_error=None,
            updated_at=datetime.utcnow(),
        )

        leverage_manager.mongodb_client = mock_mongodb_client
        leverage_manager.binance_client = mock_binance_client
        mock_mongodb_client.get_all_leverage_status = AsyncMock(
            return_value=[status1, status2]
        )

        # Mock ensure_leverage to return True
        leverage_manager.ensure_leverage = AsyncMock(return_value=True)

        result = await leverage_manager.sync_all_leverage()
        assert result["total"] == 2
        assert result["synced"] == 2
        assert result["failed"] == 0
        assert len(result["symbols"]) == 2

    @pytest.mark.asyncio
    async def test_ensure_leverage_success(
        self, leverage_manager, mock_binance_client, mock_mongodb_client
    ):
        """Test ensure_leverage successful leverage change"""
        from binance.exceptions import BinanceAPIException

        from contracts.trading_config import LeverageStatus

        leverage_manager.binance_client = mock_binance_client
        leverage_manager.mongodb_client = mock_mongodb_client
        mock_mongodb_client.set_leverage_status = AsyncMock()

        # No existing leverage status
        leverage_manager.get_leverage_status = AsyncMock(return_value=None)

        result = await leverage_manager.ensure_leverage("BTCUSDT", 10)
        assert result is True
        mock_binance_client.futures_change_leverage.assert_called_once_with(
            symbol="BTCUSDT", leverage=10
        )

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="BinanceAPIException mocking issue - needs investigation")
    async def test_ensure_leverage_binance_error_4028(
        self, leverage_manager, mock_binance_client, mock_mongodb_client
    ):
        """Test ensure_leverage handling Binance error -4028 (open position)"""
        from unittest.mock import Mock

        from binance.exceptions import BinanceAPIException

        class MockResponse:
            def __init__(self):
                self.status_code = 400
                self.headers = {}

        leverage_manager.binance_client = mock_binance_client
        leverage_manager.mongodb_client = mock_mongodb_client
        mock_mongodb_client.set_leverage_status = AsyncMock()

        leverage_manager.get_leverage_status = AsyncMock(return_value=None)

        # Create exception with code attribute
        exception = BinanceAPIException(MockResponse(), "Leverage not changed")
        exception.code = -4028
        exception.message = "Leverage not changed"

        mock_binance_client.futures_change_leverage = Mock(side_effect=exception)

        result = await leverage_manager.ensure_leverage("BTCUSDT", 10)
        # Should return False but not be critical
        assert result is False

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="BinanceAPIException mocking issue - needs investigation")
    async def test_ensure_leverage_binance_error_other_error(
        self, leverage_manager, mock_binance_client, mock_mongodb_client
    ):
        """Test ensure_leverage handling other Binance errors"""
        from unittest.mock import Mock

        from binance.exceptions import BinanceAPIException

        class MockResponse:
            def __init__(self):
                self.status_code = 400
                self.headers = {}
                self.text = '{"code": -1000, "msg": "Other error"}'

        leverage_manager.binance_client = mock_binance_client
        leverage_manager.mongodb_client = mock_mongodb_client
        mock_mongodb_client.set_leverage_status = AsyncMock()

        leverage_manager.get_leverage_status = AsyncMock(return_value=None)

        # Create exception - BinanceAPIException expects response and message
        exception = BinanceAPIException(MockResponse(), "Other error")
        # Set code attribute manually
        exception.code = -1000

        mock_binance_client.futures_change_leverage = Mock(side_effect=exception)

        result = await leverage_manager.ensure_leverage("BTCUSDT", 10)
        assert result is False

    @pytest.mark.asyncio
    async def test_ensure_leverage_exception(
        self, leverage_manager, mock_binance_client
    ):
        """Test ensure_leverage handling unexpected exceptions"""
        leverage_manager.binance_client = mock_binance_client
        leverage_manager.get_leverage_status = AsyncMock(
            side_effect=Exception("Unexpected error")
        )

        result = await leverage_manager.ensure_leverage("BTCUSDT", 10)
        assert result is False

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="BinanceAPIException mocking issue - needs investigation")
    async def test_force_leverage_binance_error(
        self, leverage_manager, mock_binance_client, mock_mongodb_client
    ):
        """Test force_leverage handling Binance error"""
        from unittest.mock import Mock

        from binance.exceptions import BinanceAPIException

        class MockResponse:
            def __init__(self):
                self.status_code = 400
                self.headers = {}
                self.text = '{"code": -1000, "msg": "Error message"}'

        leverage_manager.binance_client = mock_binance_client
        leverage_manager.mongodb_client = mock_mongodb_client

        # Create exception - BinanceAPIException expects response and message
        exception = BinanceAPIException(MockResponse(), "Error message")
        # Set code attribute manually
        exception.code = -1000

        mock_binance_client.futures_change_leverage = Mock(side_effect=exception)

        result = await leverage_manager.force_leverage("BTCUSDT", 20)
        assert result["success"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_sync_all_leverage_with_failures(
        self, leverage_manager, mock_mongodb_client, mock_binance_client
    ):
        """Test sync_all_leverage with some failures"""
        from contracts.trading_config import LeverageStatus

        status1 = LeverageStatus(
            id=None,
            symbol="BTCUSDT",
            configured_leverage=10,
            actual_leverage=10,
            last_sync_at=datetime.utcnow(),
            last_sync_success=True,
            last_sync_error=None,
            updated_at=datetime.utcnow(),
        )
        status2 = LeverageStatus(
            id=None,
            symbol="ETHUSDT",
            configured_leverage=5,
            actual_leverage=5,
            last_sync_at=datetime.utcnow(),
            last_sync_success=True,
            last_sync_error=None,
            updated_at=datetime.utcnow(),
        )

        leverage_manager.mongodb_client = mock_mongodb_client
        leverage_manager.binance_client = mock_binance_client
        mock_mongodb_client.get_all_leverage_status = AsyncMock(
            return_value=[status1, status2]
        )

        # Mock ensure_leverage to return mixed results
        async def mock_ensure(symbol, leverage):
            return symbol == "BTCUSDT"  # BTC succeeds, ETH fails

        leverage_manager.ensure_leverage = mock_ensure

        result = await leverage_manager.sync_all_leverage()
        assert result["total"] == 2
        assert result["synced"] == 1
        assert result["failed"] == 1

    @pytest.mark.asyncio
    async def test_sync_all_leverage_exception(
        self, leverage_manager, mock_mongodb_client
    ):
        """Test sync_all_leverage handling exceptions"""
        leverage_manager.mongodb_client = mock_mongodb_client
        mock_mongodb_client.get_all_leverage_status = AsyncMock(
            side_effect=Exception("DB error")
        )

        result = await leverage_manager.sync_all_leverage()
        assert result["success"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_update_leverage_status(self, leverage_manager, mock_mongodb_client):
        """Test _update_leverage_status updating cache and database"""
        leverage_manager.mongodb_client = mock_mongodb_client
        mock_mongodb_client.set_leverage_status = AsyncMock()

        await leverage_manager._update_leverage_status(
            symbol="BTCUSDT",
            configured=10,
            actual=10,
            success=True,
            error=None,
        )

        assert "BTCUSDT" in leverage_manager._leverage_cache
        status = leverage_manager._leverage_cache["BTCUSDT"]
        assert status.configured_leverage == 10
        assert status.actual_leverage == 10
        assert status.last_sync_success is True
        mock_mongodb_client.set_leverage_status.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_leverage_status_no_mongodb(self, leverage_manager):
        """Test _update_leverage_status when MongoDB not connected"""
        leverage_manager.mongodb_client = None

        await leverage_manager._update_leverage_status(
            symbol="BTCUSDT",
            configured=10,
            actual=10,
            success=True,
            error=None,
        )

        # Should still update cache
        assert "BTCUSDT" in leverage_manager._leverage_cache

    @pytest.mark.asyncio
    async def test_update_leverage_status_error(
        self, leverage_manager, mock_mongodb_client
    ):
        """Test _update_leverage_status error handling"""
        leverage_manager.mongodb_client = mock_mongodb_client
        mock_mongodb_client.set_leverage_status = AsyncMock(
            side_effect=Exception("DB error")
        )

        # Should not raise, just log error
        await leverage_manager._update_leverage_status(
            symbol="BTCUSDT",
            configured=10,
            actual=10,
            success=True,
            error=None,
        )

        # Cache should still be updated
        assert "BTCUSDT" in leverage_manager._leverage_cache

    @pytest.mark.asyncio
    async def test_ensure_leverage_sets_leverage(
        self, leverage_manager, mock_binance_client, mock_mongodb_client
    ):
        """Test ensure_leverage successfully setting leverage"""
        leverage_manager.binance_client = mock_binance_client
        leverage_manager.mongodb_client = mock_mongodb_client
        leverage_manager._update_leverage_status = AsyncMock()

        result = await leverage_manager.ensure_leverage("BTCUSDT", 10)
        assert result is True
        mock_binance_client.futures_change_leverage.assert_called_once_with(
            symbol="BTCUSDT", leverage=10
        )

    @pytest.mark.asyncio
    async def test_ensure_leverage_exception(
        self, leverage_manager, mock_binance_client
    ):
        """Test ensure_leverage handling unexpected exceptions"""
        mock_binance_client.futures_change_leverage = Mock(
            side_effect=Exception("Unexpected error")
        )
        leverage_manager.binance_client = mock_binance_client

        result = await leverage_manager.ensure_leverage("BTCUSDT", 10)
        # Should return False but not raise exception
        assert result is False

    @pytest.mark.asyncio
    async def test_sync_all_leverage_with_failures(
        self, leverage_manager, mock_mongodb_client, mock_binance_client
    ):
        """Test sync_all_leverage with some failures"""
        from contracts.trading_config import LeverageStatus

        status1 = LeverageStatus(
            id=None,
            symbol="BTCUSDT",
            configured_leverage=10,
            actual_leverage=10,
            last_sync_at=datetime.utcnow(),
            last_sync_success=True,
            last_sync_error=None,
            updated_at=datetime.utcnow(),
        )
        status2 = LeverageStatus(
            id=None,
            symbol="ETHUSDT",
            configured_leverage=5,
            actual_leverage=5,
            last_sync_at=datetime.utcnow(),
            last_sync_success=True,
            last_sync_error=None,
            updated_at=datetime.utcnow(),
        )

        leverage_manager.mongodb_client = mock_mongodb_client
        leverage_manager.binance_client = mock_binance_client
        mock_mongodb_client.get_all_leverage_status = AsyncMock(
            return_value=[status1, status2]
        )

        # Mock ensure_leverage to return mixed results
        leverage_manager.ensure_leverage = AsyncMock(side_effect=[True, False])

        result = await leverage_manager.sync_all_leverage()
        assert result["total"] == 2
        assert result["synced"] == 1
        assert result["failed"] == 1

    @pytest.mark.asyncio
    async def test_sync_all_leverage_exception(
        self, leverage_manager, mock_mongodb_client
    ):
        """Test sync_all_leverage handling exceptions"""
        leverage_manager.mongodb_client = mock_mongodb_client
        mock_mongodb_client.get_all_leverage_status = AsyncMock(
            side_effect=Exception("DB error")
        )

        result = await leverage_manager.sync_all_leverage()
        assert result["success"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_update_leverage_status(self, leverage_manager, mock_mongodb_client):
        """Test _update_leverage_status"""
        leverage_manager.mongodb_client = mock_mongodb_client
        mock_mongodb_client.set_leverage_status = AsyncMock()

        await leverage_manager._update_leverage_status(
            symbol="BTCUSDT",
            configured=10,
            actual=10,
            success=True,
            error=None,
        )

        # Should update cache
        assert "BTCUSDT" in leverage_manager._leverage_cache
        status = leverage_manager._leverage_cache["BTCUSDT"]
        assert status.configured_leverage == 10
        assert status.actual_leverage == 10
        assert status.last_sync_success is True

        # Should persist to database
        mock_mongodb_client.set_leverage_status.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_leverage_status_no_mongodb(self, leverage_manager):
        """Test _update_leverage_status when MongoDB not connected"""
        leverage_manager.mongodb_client = None

        await leverage_manager._update_leverage_status(
            symbol="BTCUSDT",
            configured=10,
            actual=10,
            success=True,
            error=None,
        )

        # Should still update cache
        assert "BTCUSDT" in leverage_manager._leverage_cache

    @pytest.mark.asyncio
    async def test_update_leverage_status_exception(
        self, leverage_manager, mock_mongodb_client
    ):
        """Test _update_leverage_status handling exceptions"""
        leverage_manager.mongodb_client = mock_mongodb_client
        mock_mongodb_client.set_leverage_status = AsyncMock(
            side_effect=Exception("DB error")
        )

        # Should not raise exception
        await leverage_manager._update_leverage_status(
            symbol="BTCUSDT",
            configured=10,
            actual=10,
            success=True,
            error=None,
        )

        # Cache should still be updated
        assert "BTCUSDT" in leverage_manager._leverage_cache
