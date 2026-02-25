"""
Unit and integration tests for trading configuration rollback in Trade Engine.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from contracts.trading_config import TradingConfig, TradingConfigAudit
from tradeengine.api_config_routes import router, set_config_manager
from tradeengine.config_manager import TradingConfigManager
from tradeengine.db.mongodb_client import DataManagerConfigClient


@pytest.fixture
def mock_mongodb_client():
    client = AsyncMock(spec=DataManagerConfigClient)
    client.connected = True
    return client


@pytest.fixture
def config_manager(mock_mongodb_client):
    return TradingConfigManager(mongodb_client=mock_mongodb_client)


@pytest.fixture
def client(config_manager):
    app = FastAPI()
    app.include_router(router)
    set_config_manager(config_manager)
    return TestClient(app)


@pytest.fixture
def sample_audit_history():
    return [
        {
            "_id": "audit_3",
            "config_type": "symbol",
            "symbol": "BTCUSDT",
            "action": "update",
            "parameters_before": {"leverage": 10},
            "parameters_after": {"leverage": 20},
            "version_before": 2,
            "version_after": 3,
            "changed_by": "user1",
            "timestamp": datetime.utcnow(),
        },
        {
            "_id": "audit_2",
            "config_type": "symbol",
            "symbol": "BTCUSDT",
            "action": "update",
            "parameters_before": {"leverage": 5},
            "parameters_after": {"leverage": 10},
            "version_before": 1,
            "version_after": 2,
            "changed_by": "user1",
            "timestamp": datetime.utcnow(),
        },
    ]


@pytest.mark.asyncio
class TestTradingConfigRollback:
    async def test_get_previous_config(
        self, config_manager, mock_mongodb_client, sample_audit_history
    ):
        """Test getting the immediately preceding configuration."""
        mock_mongodb_client.get_config_history.return_value = sample_audit_history

        prev_config = await config_manager.get_previous_config(symbol="BTCUSDT")
        assert prev_config is not None
        assert prev_config["leverage"] == 10
        mock_mongodb_client.get_config_history.assert_called_with(
            symbol="BTCUSDT", side=None, limit=2
        )

    async def test_get_config_by_version(self, config_manager, mock_mongodb_client):
        """Test retrieving config by version number."""
        mock_record = {
            "version_after": 2,
            "parameters_after": {"leverage": 15},
            "config_type": "symbol",
            "symbol": "ETHUSDT",
        }
        mock_mongodb_client.get_audit_record_by_version.return_value = mock_record

        config = await config_manager.get_config_by_version(2, symbol="ETHUSDT")
        assert config["leverage"] == 15
        mock_mongodb_client.get_audit_record_by_version.assert_called_with(
            version=2, config_type="symbol", symbol="ETHUSDT", side=None
        )

    async def test_get_config_by_id_security(self, config_manager, mock_mongodb_client):
        """Test security validation when retrieving by audit ID."""
        # Record exists but for different symbol
        mock_record = {
            "_id": "audit_123",
            "symbol": "ETHUSDT",
            "parameters_after": {"leverage": 15},
        }
        mock_mongodb_client.get_audit_record_by_id.return_value = mock_record

        # Attempt to get for BTCUSDT using ETHUSDT's audit ID
        config = await config_manager.get_config_by_id("audit_123", symbol="BTCUSDT")
        assert config is None

    async def test_rollback_success(
        self, config_manager, mock_mongodb_client, sample_audit_history
    ):
        """Test successful rollback execution."""
        mock_mongodb_client.get_config_history.return_value = sample_audit_history

        # Mock set_config to succeed
        with patch.object(
            config_manager, "set_config", new_callable=AsyncMock
        ) as mock_set:
            mock_set.return_value = (True, MagicMock(spec=TradingConfig), [])

            success, config, errors = await config_manager.rollback_config(
                changed_by="admin", symbol="BTCUSDT"
            )

            assert success is True
            mock_set.assert_called_once()
            assert mock_set.call_args[1]["parameters"]["leverage"] == 10

    async def test_rollback_invalid_version(self, config_manager):
        """Test rejection of invalid version numbers."""
        success, config, errors = await config_manager.rollback_config(
            changed_by="admin", target_version=0
        )
        assert success is False
        assert "Invalid version number" in errors[0]


def test_rollback_api_integration(client, config_manager):
    """Test the rollback API endpoint."""
    with patch.object(
        config_manager, "rollback_config", new_callable=AsyncMock
    ) as mock_rollback:
        mock_rollback.return_value = (True, MagicMock(spec=TradingConfig), [])

        payload = {
            "changed_by": "admin",
            "target_version": 2,
            "rollback_id": "audit_123",
            "reason": "API test",
        }
        response = client.post("/api/v1/config/rollback?symbol=BTCUSDT", json=payload)

        assert response.status_code == 200
        mock_rollback.assert_called_once_with(
            changed_by="admin",
            symbol="BTCUSDT",
            side=None,
            target_version=2,
            rollback_id="audit_123",
            reason="API test",
        )


def test_restore_api_alias(client, config_manager):
    """Test the restore API alias."""
    with patch.object(
        config_manager, "rollback_config", new_callable=AsyncMock
    ) as mock_rollback:
        mock_rollback.return_value = (True, MagicMock(spec=TradingConfig), [])

        response = client.post("/api/v1/config/restore", json={"changed_by": "admin"})
        assert response.status_code == 200
        mock_rollback.assert_called_once()
