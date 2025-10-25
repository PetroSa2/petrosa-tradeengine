"""
Tests for Data Manager position client operations.

Tests the integration between tradeengine and petrosa-data-manager API,
specifically for position tracking and P&L updates.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shared.mysql_client import DataManagerPositionClient


@pytest.fixture
def mock_data_manager_client():
    """Create a mock Data Manager client"""
    client = MagicMock()
    client._client = AsyncMock()
    return client


@pytest.fixture
def position_client(mock_data_manager_client):
    """Create a DataManagerPositionClient with mocked dependencies"""
    with patch(
        "shared.mysql_client.DataManagerClient",
        return_value=mock_data_manager_client,
    ):
        client = DataManagerPositionClient()
        return client


class TestUpdateDailyPnL:
    """Test daily P&L update functionality"""

    @pytest.mark.asyncio
    async def test_update_daily_pnl_success(self, position_client):
        """Test successful daily P&L update via Data Manager"""
        # Arrange
        position_client.data_manager_client._client.upsert_one = AsyncMock(
            return_value={"upserted_id": "test_id"}
        )

        # Act
        result = await position_client.update_daily_pnl("2025-10-25", 1500.50)

        # Assert
        assert result is True
        position_client.data_manager_client._client.upsert_one.assert_called_once()
        call_args = position_client.data_manager_client._client.upsert_one.call_args
        assert call_args.kwargs["database"] == "mysql"
        assert call_args.kwargs["collection"] == "daily_pnl"
        assert call_args.kwargs["filter"] == {"date": "2025-10-25"}
        assert call_args.kwargs["record"]["date"] == "2025-10-25"
        assert call_args.kwargs["record"]["daily_pnl"] == 1500.50
        assert "updated_at" in call_args.kwargs["record"]

    @pytest.mark.asyncio
    async def test_update_daily_pnl_uses_timezone_aware_datetime(self, position_client):
        """Test that update_daily_pnl uses timezone-aware datetime"""
        # Arrange
        position_client.data_manager_client._client.upsert_one = AsyncMock(
            return_value={"upserted_id": "test_id"}
        )

        # Act
        await position_client.update_daily_pnl("2025-10-25", 2000.00)

        # Assert
        call_args = position_client.data_manager_client._client.upsert_one.call_args
        updated_at = call_args.kwargs["record"]["updated_at"]
        # Verify it's a datetime object with timezone info
        assert isinstance(updated_at, datetime)
        assert updated_at.tzinfo is not None

    @pytest.mark.asyncio
    async def test_update_daily_pnl_failure(self, position_client):
        """Test daily P&L update failure handling"""
        # Arrange
        position_client.data_manager_client._client.upsert_one = AsyncMock(
            side_effect=Exception("Connection failed")
        )

        # Act
        result = await position_client.update_daily_pnl("2025-10-25", 1500.50)

        # Assert
        assert result is False

    @pytest.mark.asyncio
    async def test_update_daily_pnl_negative_value(self, position_client):
        """Test daily P&L update with negative value (loss)"""
        # Arrange
        position_client.data_manager_client._client.upsert_one = AsyncMock(
            return_value={"upserted_id": "test_id"}
        )

        # Act
        result = await position_client.update_daily_pnl("2025-10-25", -500.75)

        # Assert
        assert result is True
        call_args = position_client.data_manager_client._client.upsert_one.call_args
        assert call_args.kwargs["record"]["daily_pnl"] == -500.75


class TestUpsertPosition:
    """Test position upsert functionality"""

    @pytest.mark.asyncio
    async def test_upsert_position_success(self, position_client):
        """Test successful position upsert via Data Manager"""
        # Arrange
        position_client.data_manager_client._client.upsert_one = AsyncMock(
            return_value={"upserted_id": "test_id"}
        )

        position_data = {
            "position_id": "pos_123",
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "quantity": 0.001,
            "entry_price": 50000.0,
            "status": "open",
        }

        # Act
        result = await position_client.upsert_position(position_data)

        # Assert
        assert result is True
        position_client.data_manager_client._client.upsert_one.assert_called_once()
        call_args = position_client.data_manager_client._client.upsert_one.call_args
        assert call_args.kwargs["database"] == "mysql"
        assert call_args.kwargs["collection"] == "positions"
        assert call_args.kwargs["filter"]["symbol"] == "BTCUSDT"
        assert call_args.kwargs["filter"]["position_side"] == "LONG"
        assert call_args.kwargs["filter"]["status"] == "open"
        assert call_args.kwargs["record"] == position_data

    @pytest.mark.asyncio
    async def test_upsert_position_short(self, position_client):
        """Test position upsert for SHORT position"""
        # Arrange
        position_client.data_manager_client._client.upsert_one = AsyncMock(
            return_value={"upserted_id": "test_id"}
        )

        position_data = {
            "symbol": "ETHUSDT",
            "position_side": "SHORT",
            "quantity": 0.01,
            "entry_price": 3000.0,
            "status": "open",
        }

        # Act
        result = await position_client.upsert_position(position_data)

        # Assert
        assert result is True
        call_args = position_client.data_manager_client._client.upsert_one.call_args
        assert call_args.kwargs["filter"]["symbol"] == "ETHUSDT"
        assert call_args.kwargs["filter"]["position_side"] == "SHORT"

    @pytest.mark.asyncio
    async def test_upsert_position_default_long(self, position_client):
        """Test position upsert defaults to LONG if position_side not specified"""
        # Arrange
        position_client.data_manager_client._client.upsert_one = AsyncMock(
            return_value={"upserted_id": "test_id"}
        )

        position_data = {
            "symbol": "BTCUSDT",
            "quantity": 0.001,
            "status": "open",
        }

        # Act
        result = await position_client.upsert_position(position_data)

        # Assert
        assert result is True
        call_args = position_client.data_manager_client._client.upsert_one.call_args
        assert call_args.kwargs["filter"]["position_side"] == "LONG"  # Default

    @pytest.mark.asyncio
    async def test_upsert_position_failure(self, position_client):
        """Test position upsert failure handling"""
        # Arrange
        position_client.data_manager_client._client.upsert_one = AsyncMock(
            side_effect=Exception("Connection failed")
        )

        position_data = {
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "quantity": 0.001,
            "status": "open",
        }

        # Act
        result = await position_client.upsert_position(position_data)

        # Assert
        assert result is False


class TestDataManagerAPICompatibility:
    """Test that Data Manager API is called with correct signature"""

    @pytest.mark.asyncio
    async def test_upsert_one_no_update_parameter(self, position_client):
        """Verify upsert_one is called without 'update' parameter (uses 'record' instead)"""
        # Arrange
        position_client.data_manager_client._client.upsert_one = AsyncMock(
            return_value={"upserted_id": "test_id"}
        )

        # Act
        await position_client.update_daily_pnl("2025-10-25", 1500.50)

        # Assert
        call_args = position_client.data_manager_client._client.upsert_one.call_args
        # Verify 'record' parameter is used (not 'update')
        assert "record" in call_args.kwargs
        assert "update" not in call_args.kwargs
        # Verify no 'upsert' parameter (this was the bug)
        assert "upsert" not in call_args.kwargs

    @pytest.mark.asyncio
    async def test_upsert_position_no_upsert_parameter(self, position_client):
        """Verify upsert_one is called without deprecated 'upsert' parameter"""
        # Arrange
        position_client.data_manager_client._client.upsert_one = AsyncMock(
            return_value={"upserted_id": "test_id"}
        )

        position_data = {
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "quantity": 0.001,
            "status": "open",
        }

        # Act
        await position_client.upsert_position(position_data)

        # Assert
        call_args = position_client.data_manager_client._client.upsert_one.call_args
        # Verify no deprecated 'upsert' parameter in call
        assert "upsert" not in call_args.kwargs
        # Verify correct parameters are used
        assert "database" in call_args.kwargs
        assert "collection" in call_args.kwargs
        assert "filter" in call_args.kwargs
        assert "record" in call_args.kwargs
