"""
Tests for Data Manager configuration client operations.

Tests the DataManagerClient methods that were updated to use upsert_one()
instead of update() with upsert parameter (fixes #165).
"""

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from contracts.trading_config import LeverageStatus, TradingConfig
from tradeengine.services.data_manager_client import DataManagerClient


@pytest.fixture
def mock_base_client():
    """Create a mock base Data Manager client"""
    client = AsyncMock()
    client.upsert_one = AsyncMock(return_value={"upserted_count": 1})
    client.health = AsyncMock(return_value={"status": "healthy"})
    return client


@pytest.fixture
def data_manager_client(mock_base_client):
    """Create a DataManagerClient with mocked dependencies"""
    with patch(
        "tradeengine.services.data_manager_client.BaseDataManagerClient",
        return_value=mock_base_client,
    ):
        client = DataManagerClient(base_url="http://test:8080")
        return client


class TestSetGlobalConfig:
    """Test set_global_config uses upsert_one correctly"""

    @pytest.mark.asyncio
    async def test_set_global_config_success(self, data_manager_client):
        """Test successful global config upsert via Data Manager"""
        # Arrange
        config = TradingConfig(
            parameters={
                "enabled": True,
                "max_position_size": 100.0,
                "max_leverage": 10,
                "stop_loss_percentage": 2.0,
            },
            created_by="test_agent",
        )

        # Act
        result = await data_manager_client.set_global_config(config)

        # Assert
        assert result is True
        data_manager_client._client.upsert_one.assert_called_once()
        call_args = data_manager_client._client.upsert_one.call_args

        # Verify correct parameters (not 'update' with 'upsert=True')
        assert call_args.kwargs["database"] == "mongodb"
        assert call_args.kwargs["collection"] == "trading_configs_global"
        assert call_args.kwargs["filter"] == {}
        assert "record" in call_args.kwargs
        assert "upsert" not in call_args.kwargs  # Bug fix verification
        assert "data" not in call_args.kwargs  # Should use 'record', not 'data'

        # Verify config data
        record = call_args.kwargs["record"]
        assert record["parameters"]["enabled"] is True
        assert record["parameters"]["max_position_size"] == 100.0
        assert "updated_at" in record

    @pytest.mark.asyncio
    async def test_set_global_config_updates_timestamp(self, data_manager_client):
        """Test that set_global_config adds updated_at timestamp"""
        # Arrange
        config = TradingConfig(parameters={"enabled": True}, created_by="test_agent")

        # Act
        await data_manager_client.set_global_config(config)

        # Assert
        call_args = data_manager_client._client.upsert_one.call_args
        record = call_args.kwargs["record"]
        assert "updated_at" in record
        assert isinstance(record["updated_at"], datetime)

    @pytest.mark.asyncio
    async def test_set_global_config_excludes_id(self, data_manager_client):
        """Test that set_global_config excludes 'id' field from record"""
        # Arrange
        config = TradingConfig(parameters={"enabled": True}, created_by="test_agent")

        # Act
        await data_manager_client.set_global_config(config)

        # Assert
        call_args = data_manager_client._client.upsert_one.call_args
        record = call_args.kwargs["record"]
        assert "id" not in record

    @pytest.mark.asyncio
    async def test_set_global_config_failure(self, data_manager_client):
        """Test set_global_config failure handling"""
        # Arrange
        config = TradingConfig(parameters={"enabled": True}, created_by="test_agent")
        data_manager_client._client.upsert_one = AsyncMock(
            side_effect=Exception("Connection failed")
        )

        # Act
        result = await data_manager_client.set_global_config(config)

        # Assert
        assert result is False


class TestSetSymbolConfig:
    """Test set_symbol_config uses upsert_one correctly"""

    @pytest.mark.asyncio
    async def test_set_symbol_config_success(self, data_manager_client):
        """Test successful symbol config upsert via Data Manager"""
        # Arrange
        config = TradingConfig(
            symbol="BTCUSDT",
            parameters={
                "enabled": True,
                "max_position_size": 1.0,
                "max_leverage": 5,
            },
            created_by="test_agent",
        )

        # Act
        result = await data_manager_client.set_symbol_config(config)

        # Assert
        assert result is True
        data_manager_client._client.upsert_one.assert_called_once()
        call_args = data_manager_client._client.upsert_one.call_args

        # Verify correct parameters
        assert call_args.kwargs["database"] == "mongodb"
        assert call_args.kwargs["collection"] == "trading_configs_symbol"
        assert call_args.kwargs["filter"] == {"symbol": "BTCUSDT"}
        assert "record" in call_args.kwargs
        assert "upsert" not in call_args.kwargs  # Bug fix verification

        # Verify config data
        record = call_args.kwargs["record"]
        assert record["symbol"] == "BTCUSDT"
        assert record["parameters"]["enabled"] is True

    @pytest.mark.asyncio
    async def test_set_symbol_config_requires_symbol(self, data_manager_client):
        """Test that set_symbol_config validates symbol is present"""
        # Arrange
        config = TradingConfig(
            parameters={"enabled": True}, created_by="test_agent"
        )  # No symbol

        # Act
        result = await data_manager_client.set_symbol_config(config)

        # Assert - should return False when symbol is None
        assert result is False
        data_manager_client._client.upsert_one.assert_not_called()


class TestSetSymbolSideConfig:
    """Test set_symbol_side_config uses upsert_one correctly"""

    @pytest.mark.asyncio
    async def test_set_symbol_side_config_success(self, data_manager_client):
        """Test successful symbol-side config upsert via Data Manager"""
        # Arrange
        config = TradingConfig(
            symbol="ETHUSDT",
            side="LONG",
            parameters={"enabled": True, "max_position_size": 2.0},
            created_by="test_agent",
        )

        # Act
        result = await data_manager_client.set_symbol_side_config(config)

        # Assert
        assert result is True
        data_manager_client._client.upsert_one.assert_called_once()
        call_args = data_manager_client._client.upsert_one.call_args

        # Verify correct parameters
        assert call_args.kwargs["database"] == "mongodb"
        assert call_args.kwargs["collection"] == "trading_configs_symbol_side"
        assert call_args.kwargs["filter"] == {"symbol": "ETHUSDT", "side": "LONG"}
        assert "record" in call_args.kwargs
        assert "upsert" not in call_args.kwargs  # Bug fix verification

        # Verify config data
        record = call_args.kwargs["record"]
        assert record["symbol"] == "ETHUSDT"
        assert record["side"] == "LONG"

    @pytest.mark.asyncio
    async def test_set_symbol_side_config_requires_symbol_and_side(
        self, data_manager_client
    ):
        """Test that set_symbol_side_config validates symbol and side"""
        # Arrange - missing side
        config = TradingConfig(
            symbol="BTCUSDT", parameters={"enabled": True}, created_by="test_agent"
        )

        # Act
        result = await data_manager_client.set_symbol_side_config(config)

        # Assert
        assert result is False
        data_manager_client._client.upsert_one.assert_not_called()


class TestSetLeverageStatus:
    """Test set_leverage_status uses upsert_one correctly"""

    @pytest.mark.asyncio
    async def test_set_leverage_status_success(self, data_manager_client):
        """Test successful leverage status upsert via Data Manager"""
        # Arrange
        status = LeverageStatus(
            symbol="BTCUSDT",
            current_leverage=5,
            max_leverage=10,
            configured_leverage=5,
            enabled=True,
        )

        # Act
        result = await data_manager_client.set_leverage_status(status)

        # Assert
        assert result is True
        data_manager_client._client.upsert_one.assert_called_once()
        call_args = data_manager_client._client.upsert_one.call_args

        # Verify correct parameters
        assert call_args.kwargs["database"] == "mongodb"
        assert call_args.kwargs["collection"] == "leverage_status"
        assert call_args.kwargs["filter"] == {"symbol": "BTCUSDT"}
        assert "record" in call_args.kwargs
        assert "upsert" not in call_args.kwargs  # Bug fix verification
        assert "data" not in call_args.kwargs  # Should use 'record', not 'data'

        # Verify status data
        record = call_args.kwargs["record"]
        assert record["symbol"] == "BTCUSDT"
        assert record["configured_leverage"] == 5
        assert "updated_at" in record

    @pytest.mark.asyncio
    async def test_set_leverage_status_with_high_leverage(self, data_manager_client):
        """Test leverage status with high leverage value"""
        # Arrange
        status = LeverageStatus(
            symbol="ETHUSDT",
            configured_leverage=20,
        )

        # Act
        result = await data_manager_client.set_leverage_status(status)

        # Assert
        assert result is True
        call_args = data_manager_client._client.upsert_one.call_args
        record = call_args.kwargs["record"]
        assert record["configured_leverage"] == 20

    @pytest.mark.asyncio
    async def test_set_leverage_status_failure(self, data_manager_client):
        """Test set_leverage_status failure handling"""
        # Arrange
        status = LeverageStatus(
            symbol="BTCUSDT",
            configured_leverage=5,
        )
        data_manager_client._client.upsert_one = AsyncMock(
            side_effect=Exception("Connection failed")
        )

        # Act
        result = await data_manager_client.set_leverage_status(status)

        # Assert
        assert result is False


class TestAPIContractCompliance:
    """Test that all methods comply with Data Manager API contract (no 'upsert' parameter)"""

    @pytest.mark.asyncio
    async def test_no_method_calls_update_with_upsert_parameter(
        self, data_manager_client
    ):
        """Verify none of the config methods call update() with upsert parameter"""
        # Arrange
        config = TradingConfig(
            symbol="BTCUSDT",
            side="LONG",
            parameters={"enabled": True, "max_position_size": 1.0},
            created_by="test_agent",
        )
        status = LeverageStatus(
            symbol="BTCUSDT",
            configured_leverage=5,
        )

        # Act - call all methods that were fixed
        await data_manager_client.set_global_config(config)
        await data_manager_client.set_symbol_config(config)
        await data_manager_client.set_symbol_side_config(config)
        await data_manager_client.set_leverage_status(status)

        # Assert - verify upsert_one was called 4 times (once for each method)
        assert data_manager_client._client.upsert_one.call_count == 4

    @pytest.mark.asyncio
    async def test_all_upsert_calls_use_record_parameter(self, data_manager_client):
        """Verify all upsert_one calls use 'record' parameter, not 'data'"""
        # Arrange
        config = TradingConfig(
            symbol="BTCUSDT", parameters={"enabled": True}, created_by="test_agent"
        )
        status = LeverageStatus(
            symbol="BTCUSDT",
            configured_leverage=5,
        )

        # Act
        await data_manager_client.set_global_config(config)
        await data_manager_client.set_symbol_config(config)
        await data_manager_client.set_symbol_side_config(
            TradingConfig(
                symbol="BTCUSDT",
                side="LONG",
                parameters={"enabled": True},
                created_by="test_agent",
            )
        )
        await data_manager_client.set_leverage_status(status)

        # Assert - all calls use 'record', not 'data'
        for call in data_manager_client._client.upsert_one.call_args_list:
            assert "record" in call.kwargs
            assert "data" not in call.kwargs
            assert "upsert" not in call.kwargs
