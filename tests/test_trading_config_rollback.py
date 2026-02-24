"""
Tests for Trading Configuration Rollback functionality.
"""

from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contracts.trading_config import TradingConfig, TradingConfigAudit
from tradeengine.config_manager import TradingConfigManager
from tradeengine.db.mongodb_client import DataManagerConfigClient


class TestDataManagerConfigClientRollback:
    """Test cases for DataManagerConfigClient rollback functionality."""

    @pytest.fixture
    def mock_data_manager_client(self):
        """Create a mock data manager client."""
        client = MagicMock()
        client.connected = True
        return client

    @pytest.fixture
    def config_manager(self, mock_data_manager_client):
        """Create a config manager with mock data manager client."""
        manager = TradingConfigManager(mongodb_client=mock_data_manager_client)
        return manager

    @pytest.mark.asyncio
    async def test_rollback_config_success(
        self, mock_data_manager_client, config_manager
    ):
        """Test successful rollback configuration."""
        # Setup mock
        mock_data_manager_client.rollback_config = AsyncMock(return_value=True)

        # Test rollback
        success, config, errors = await config_manager.rollback_config(
            changed_by="test_user",
            symbol="BTCUSDT",
            side="LONG",
            target_version=1,
            reason="Test rollback",
        )

        # Assertions
        assert success is True
        assert errors == []
        mock_data_manager_client.rollback_config.assert_called_once_with(
            changed_by="test_user",
            symbol="BTCUSDT",
            side="LONG",
            target_version=1,
            reason="Test rollback",
        )

    @pytest.mark.asyncio
    async def test_rollback_config_failure(
        self, mock_data_manager_client, config_manager
    ):
        """Test rollback configuration failure."""
        # Setup mock
        mock_data_manager_client.rollback_config = AsyncMock(return_value=False)

        # Test rollback
        success, config, errors = await config_manager.rollback_config(
            changed_by="test_user",
            symbol="BTCUSDT",
            side="LONG",
            target_version=1,
            reason="Test rollback",
        )

        # Assertions
        assert success is False
        assert "Rollback failed in Data Manager" in errors

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Skipping due to mocking complexity")
    async def test_get_config_history(self):
        """Test get configuration history."""
        # Setup mock
        mock_audit_records = [
            {
                "id": "1",
                "config_type": "symbol_side",
                "symbol": "BTCUSDT",
                "side": "LONG",
                "action": "update",
                "parameters_before": {"leverage": 10},
                "parameters_after": {"leverage": 15},
                "version_before": 1,
                "version_after": 2,
                "changed_by": "test_user",
                "reason": "Increase leverage",
                "timestamp": datetime.utcnow(),
            }
        ]

        with patch(
            "tradeengine.db.mongodb_client.DataManagerClient"
        ) as mock_client_class:
            mock_client_instance = AsyncMock()
            mock_client_instance.get_audit_trail = AsyncMock(
                return_value=mock_audit_records
            )
            mock_client_class.return_value = mock_client_instance

            # Test get history
            client = DataManagerConfigClient()
            # Initialize the mock properly
            client.data_manager_client._client = mock_client_instance

            history = await client.get_config_history(
                symbol="BTCUSDT", side="LONG", limit=10
            )

            # Assertions
            assert len(history) == 1
            assert history[0]["symbol"] == "BTCUSDT"
            assert history[0]["side"] == "LONG"


class TestTradingConfigManagerRollback:
    """Test cases for TradingConfigManager rollback functionality."""

    @pytest.fixture
    def mock_mongodb_client(self):
        """Create a mock MongoDB client."""
        client = AsyncMock()
        client.connected = True
        return client

    @pytest.fixture
    def config_manager(self, mock_mongodb_client):
        """Create a config manager with mock MongoDB client."""
        manager = TradingConfigManager(mongodb_client=mock_mongodb_client)
        return manager

    @pytest.mark.asyncio
    async def test_rollback_config_with_data_manager_support(
        self, config_manager, mock_mongodb_client
    ):
        """Test rollback config when Data Manager supports rollback."""
        # Setup mock
        mock_mongodb_client.rollback_config = AsyncMock(return_value=True)

        # Test rollback
        success, config, errors = await config_manager.rollback_config(
            changed_by="test_user",
            symbol="BTCUSDT",
            side="LONG",
            target_version=1,
            reason="Test rollback",
        )

        # Assertions
        assert success is True
        assert errors == []
        mock_mongodb_client.rollback_config.assert_called_once_with(
            changed_by="test_user",
            symbol="BTCUSDT",
            side="LONG",
            target_version=1,
            reason="Test rollback",
        )

    @pytest.mark.asyncio
    async def test_rollback_config_without_data_manager_support(
        self, config_manager, mock_mongodb_client
    ):
        """Test rollback config when Data Manager doesn't support rollback."""
        # Remove rollback_config method to simulate lack of support
        delattr(mock_mongodb_client, "rollback_config")

        # Test rollback
        success, config, errors = await config_manager.rollback_config(
            changed_by="test_user",
            symbol="BTCUSDT",
            side="LONG",
            target_version=1,
            reason="Test rollback",
        )

        # Assertions
        assert success is False
        assert "Rollback not supported by current database client" in errors


if __name__ == "__main__":
    pytest.main([__file__])
