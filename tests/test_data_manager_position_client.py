"""
Tests for Data Manager position client operations.

Tests the integration between tradeengine and petrosa-data-manager API,
specifically for position tracking and P&L updates.
"""

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
        assert result.ok is True
        position_client.data_manager_client._client.upsert_one.assert_called_once()
        call_args = position_client.data_manager_client._client.upsert_one.call_args
        assert call_args.kwargs["database"] == "mysql"
        assert call_args.kwargs["collection"] == "positions"
        assert call_args.kwargs["filter"] == {"position_id": "pos_123"}
        assert call_args.kwargs["record"] == position_data

    @pytest.mark.asyncio
    async def test_upsert_position_short(self, position_client):
        """Test position upsert for SHORT position"""
        # Arrange
        position_client.data_manager_client._client.upsert_one = AsyncMock(
            return_value={"upserted_id": "test_id"}
        )

        position_data = {
            "position_id": "pos_456",
            "symbol": "ETHUSDT",
            "position_side": "SHORT",
            "quantity": 0.01,
            "entry_price": 3000.0,
            "status": "open",
        }

        # Act
        result = await position_client.upsert_position(position_data)

        # Assert
        assert result.ok is True
        call_args = position_client.data_manager_client._client.upsert_one.call_args
        assert call_args.kwargs["filter"] == {"position_id": "pos_456"}

    @pytest.mark.asyncio
    async def test_upsert_position_requires_position_id(self, position_client):
        """Test position upsert defaults to LONG if position_side not specified"""
        # Arrange
        position_data = {
            "symbol": "BTCUSDT",
            "quantity": 0.001,
            "status": "open",
        }

        # Act
        result = await position_client.upsert_position(position_data)

        # Assert
        assert result.ok is False
        position_client.data_manager_client._client.upsert_one.assert_not_called()

    @pytest.mark.asyncio
    async def test_upsert_position_failure(self, position_client):
        """Test position upsert failure handling"""
        # Arrange
        position_client.data_manager_client._client.upsert_one = AsyncMock(
            side_effect=Exception("Connection failed")
        )

        position_data = {
            "position_id": "pos_failure",
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "quantity": 0.001,
            "status": "open",
        }

        # Act
        result = await position_client.upsert_position(position_data)

        # Assert
        assert result.ok is False


class TestPositionContractRequests:
    """Pin the request contracts used by the position persistence paths."""

    @pytest.mark.asyncio
    async def test_position_updates_send_plain_column_maps(self, position_client):
        position_client.data_manager_client._client.update_one = AsyncMock(
            return_value={"updated_count": 1}
        )

        update = {"status": "closed", "exit_price": 101.25}
        result = await position_client.update_position("pos-1", update)

        assert result.ok is True
        call = position_client.data_manager_client._client.update_one.call_args
        assert call.kwargs["update"] == update
        assert "$set" not in call.kwargs["update"]

    @pytest.mark.asyncio
    async def test_risk_order_update_uses_updated_count(self, position_client):
        position_client.data_manager_client._client.update_one = AsyncMock(
            return_value={"updated_count": 1}
        )

        result = await position_client.update_position_risk_orders(
            "pos-1", {"stop_loss_order_id": "sl-1"}
        )

        assert result.ok is True

    @pytest.mark.asyncio
    async def test_close_position_sends_plain_column_map(self, position_client):
        position_client.data_manager_client._client.update_one = AsyncMock(
            return_value={"updated_count": 1}
        )

        result = await position_client.close_position(
            "BTCUSDT", "LONG", {"status": "closed", "exit_price": 100.0}
        )

        assert result.ok is True
        call = position_client.data_manager_client._client.update_one.call_args
        assert call.kwargs["update"] == {"status": "closed", "exit_price": 100.0}

    @pytest.mark.asyncio
    async def test_open_positions_query_contains_filter(self, position_client):
        position_client.data_manager_client._client.query = AsyncMock(
            return_value={"data": []}
        )

        await position_client.get_open_positions("strategy-1")

        call = position_client.data_manager_client._client.query.call_args
        assert call.kwargs["filter"] == {
            "status": "open",
            "strategy_id": "strategy-1",
        }
        assert call.kwargs["sort"] == {"entry_time": -1}
        assert "params" not in call.kwargs

    @pytest.mark.asyncio
    async def test_upsert_position_no_upsert_parameter(self, position_client):
        """Verify upsert_one is called without deprecated 'upsert' parameter"""
        # Arrange
        position_client.data_manager_client._client.upsert_one = AsyncMock(
            return_value={"upserted_id": "test_id"}
        )

        position_data = {
            "position_id": "pos_contract",
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
