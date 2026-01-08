"""
Comprehensive tests for tradeengine/db/mongodb_client.py to increase coverage
"""

from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest

from contracts.trading_config import TradingConfig, TradingConfigAudit
from tradeengine.db.mongodb_client import DataManagerConfigClient


@pytest.fixture
def mongodb_client():
    """Create a DataManagerConfigClient instance for testing"""
    client = DataManagerConfigClient()
    return client


class TestDataManagerConfigClientBasic:
    """Test basic DataManagerConfigClient functionality"""

    def test_initialization(self, mongodb_client):
        """Test DataManagerConfigClient initialization"""
        assert mongodb_client is not None
        assert hasattr(mongodb_client, "data_manager_client")

    @pytest.mark.asyncio
    async def test_connect(self, mongodb_client):
        """Test connecting to data manager"""
        with patch.object(
            mongodb_client.data_manager_client, "connect", new_callable=AsyncMock
        ) as mock_connect:
            await mongodb_client.connect()
            mock_connect.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect(self, mongodb_client):
        """Test disconnecting from data manager"""
        with patch.object(
            mongodb_client.data_manager_client, "disconnect", new_callable=AsyncMock
        ) as mock_disconnect:
            await mongodb_client.disconnect()
            mock_disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_connected_property(self, mongodb_client):
        """Test connected property"""
        assert mongodb_client.connected is True

    @pytest.mark.asyncio
    async def test_health_check(self, mongodb_client):
        """Test health check"""
        with patch.object(
            mongodb_client.data_manager_client._client, "health", new_callable=AsyncMock
        ) as mock_health:
            mock_health.return_value = {"status": "healthy"}

            health = await mongodb_client.health_check()
            assert isinstance(health, dict)
            assert health["status"] == "healthy"
            assert health["service"] == "data-manager"

    @pytest.mark.asyncio
    async def test_health_check_unhealthy(self, mongodb_client):
        """Test health check when unhealthy"""
        with patch.object(
            mongodb_client.data_manager_client._client, "health", new_callable=AsyncMock
        ) as mock_health:
            mock_health.return_value = {"status": "unhealthy"}

            health = await mongodb_client.health_check()
            assert health["status"] == "unhealthy"

    @pytest.mark.asyncio
    async def test_health_check_with_error(self, mongodb_client):
        """Test health check with error"""
        with patch.object(
            mongodb_client.data_manager_client._client, "health", new_callable=AsyncMock
        ) as mock_health:
            mock_health.side_effect = Exception("Connection error")

            health = await mongodb_client.health_check()
            assert health["status"] == "unhealthy"
            assert "error" in health


class TestDataManagerConfigClientGlobalConfig:
    """Test global configuration methods"""

    @pytest.mark.asyncio
    async def test_get_global_config(self, mongodb_client):
        """Test getting global config"""
        with patch.object(
            mongodb_client.data_manager_client._client, "query", new_callable=AsyncMock
        ) as mock_query:
            mock_query.return_value = {"data": []}

            config = await mongodb_client.get_global_config()
            assert config is None

    @pytest.mark.asyncio
    async def test_get_global_config_with_data(self, mongodb_client):
        """Test getting global config with data"""
        with patch.object(
            mongodb_client.data_manager_client._client, "query", new_callable=AsyncMock
        ) as mock_query:
            mock_query.return_value = {
                "data": [
                    {
                        "_id": "global",
                        "parameters": {"leverage": 10},
                        "created_by": "test_user",
                        "created_at": datetime.utcnow(),
                        "updated_at": datetime.utcnow(),
                    }
                ]
            }

            config = await mongodb_client.get_global_config()
            assert config is not None
            assert config.parameters == {"leverage": 10}

    @pytest.mark.asyncio
    async def test_get_global_config_with_error(self, mongodb_client):
        """Test getting global config with error"""
        with patch.object(
            mongodb_client.data_manager_client._client, "query", new_callable=AsyncMock
        ) as mock_query:
            mock_query.side_effect = Exception("Connection error")

            config = await mongodb_client.get_global_config()
            assert config is None

    @pytest.mark.asyncio
    async def test_upsert_global_config(self, mongodb_client):
        """Test upserting global config"""
        config = TradingConfig(parameters={"leverage": 10}, created_by="test_user")

        with patch.object(
            mongodb_client.data_manager_client._client,
            "upsert_one",
            new_callable=AsyncMock,
        ) as mock_upsert:
            mock_upsert.return_value = {"upserted_id": "global"}

            result = await mongodb_client.upsert_global_config(config)
            assert result is True
            mock_upsert.assert_called_once()

    @pytest.mark.asyncio
    async def test_upsert_global_config_with_modified_count(self, mongodb_client):
        """Test upserting global config with modified_count"""
        config = TradingConfig(parameters={"leverage": 10}, created_by="test_user")

        with patch.object(
            mongodb_client.data_manager_client._client,
            "upsert_one",
            new_callable=AsyncMock,
        ) as mock_upsert:
            mock_upsert.return_value = {"modified_count": 1}

            result = await mongodb_client.upsert_global_config(config)
            assert result is True

    @pytest.mark.asyncio
    async def test_upsert_global_config_failure(self, mongodb_client):
        """Test upserting global config with failure"""
        config = TradingConfig(parameters={"leverage": 10}, created_by="test_user")

        with patch.object(
            mongodb_client.data_manager_client._client,
            "upsert_one",
            new_callable=AsyncMock,
        ) as mock_upsert:
            mock_upsert.return_value = {}

            result = await mongodb_client.upsert_global_config(config)
            assert result is False

    @pytest.mark.asyncio
    async def test_upsert_global_config_with_error(self, mongodb_client):
        """Test upserting global config with error"""
        config = TradingConfig(parameters={"leverage": 10}, created_by="test_user")

        with patch.object(
            mongodb_client.data_manager_client._client,
            "upsert_one",
            new_callable=AsyncMock,
        ) as mock_upsert:
            mock_upsert.side_effect = Exception("Connection error")

            result = await mongodb_client.upsert_global_config(config)
            assert result is False

    @pytest.mark.asyncio
    async def test_set_global_config(self, mongodb_client):
        """Test setting global config (alias for upsert)"""
        config = TradingConfig(parameters={"leverage": 10}, created_by="test_user")

        with patch.object(
            mongodb_client, "upsert_global_config", new_callable=AsyncMock
        ) as mock_upsert:
            mock_upsert.return_value = True

            result = await mongodb_client.set_global_config(config)
            assert result is True
            mock_upsert.assert_called_once_with(config)

    @pytest.mark.asyncio
    async def test_delete_global_config(self, mongodb_client):
        """Test deleting global config"""
        with patch.object(
            mongodb_client.data_manager_client._client,
            "delete_one",
            new_callable=AsyncMock,
        ) as mock_delete:
            mock_delete.return_value = {"deleted_count": 1}

            result = await mongodb_client.delete_global_config()
            assert result is True
            mock_delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_global_config_not_found(self, mongodb_client):
        """Test deleting global config when not found"""
        with patch.object(
            mongodb_client.data_manager_client._client,
            "delete_one",
            new_callable=AsyncMock,
        ) as mock_delete:
            mock_delete.return_value = {"deleted_count": 0}

            result = await mongodb_client.delete_global_config()
            assert result is False

    @pytest.mark.asyncio
    async def test_delete_global_config_with_error(self, mongodb_client):
        """Test deleting global config with error"""
        with patch.object(
            mongodb_client.data_manager_client._client,
            "delete_one",
            new_callable=AsyncMock,
        ) as mock_delete:
            mock_delete.side_effect = Exception("Connection error")

            result = await mongodb_client.delete_global_config()
            assert result is False


class TestDataManagerConfigClientSymbolConfig:
    """Test symbol configuration methods"""

    @pytest.mark.asyncio
    async def test_get_symbol_config(self, mongodb_client):
        """Test getting symbol config"""
        with patch.object(
            mongodb_client.data_manager_client._client, "query", new_callable=AsyncMock
        ) as mock_query:
            mock_query.return_value = {"data": []}

            config = await mongodb_client.get_symbol_config("BTCUSDT")
            assert config is None

    @pytest.mark.asyncio
    async def test_get_symbol_config_with_data(self, mongodb_client):
        """Test getting symbol config with data"""
        with patch.object(
            mongodb_client.data_manager_client._client, "query", new_callable=AsyncMock
        ) as mock_query:
            mock_query.return_value = {
                "data": [
                    {
                        "_id": "test_id",
                        "symbol": "BTCUSDT",
                        "parameters": {"leverage": 10},
                        "created_by": "test_user",
                        "created_at": datetime.utcnow(),
                        "updated_at": datetime.utcnow(),
                    }
                ]
            }

            config = await mongodb_client.get_symbol_config("BTCUSDT")
            assert config is not None
            assert config.symbol == "BTCUSDT"

    @pytest.mark.asyncio
    async def test_get_symbol_config_with_error(self, mongodb_client):
        """Test getting symbol config with error"""
        with patch.object(
            mongodb_client.data_manager_client._client, "query", new_callable=AsyncMock
        ) as mock_query:
            mock_query.side_effect = Exception("Connection error")

            config = await mongodb_client.get_symbol_config("BTCUSDT")
            assert config is None

    @pytest.mark.asyncio
    async def test_upsert_symbol_config(self, mongodb_client):
        """Test upserting symbol config"""
        config = TradingConfig(
            symbol="BTCUSDT", parameters={"leverage": 10}, created_by="test_user"
        )

        with patch.object(
            mongodb_client.data_manager_client._client,
            "upsert_one",
            new_callable=AsyncMock,
        ) as mock_upsert:
            mock_upsert.return_value = {"upserted_id": "test_id"}

            result = await mongodb_client.upsert_symbol_config("BTCUSDT", config)
            assert result is True

    @pytest.mark.asyncio
    async def test_upsert_symbol_config_with_modified_count(self, mongodb_client):
        """Test upserting symbol config with modified_count"""
        config = TradingConfig(
            symbol="BTCUSDT", parameters={"leverage": 10}, created_by="test_user"
        )

        with patch.object(
            mongodb_client.data_manager_client._client,
            "upsert_one",
            new_callable=AsyncMock,
        ) as mock_upsert:
            mock_upsert.return_value = {"modified_count": 1}

            result = await mongodb_client.upsert_symbol_config("BTCUSDT", config)
            assert result is True

    @pytest.mark.asyncio
    async def test_upsert_symbol_config_failure(self, mongodb_client):
        """Test upserting symbol config with failure"""
        config = TradingConfig(
            symbol="BTCUSDT", parameters={"leverage": 10}, created_by="test_user"
        )

        with patch.object(
            mongodb_client.data_manager_client._client,
            "upsert_one",
            new_callable=AsyncMock,
        ) as mock_upsert:
            mock_upsert.return_value = {}

            result = await mongodb_client.upsert_symbol_config("BTCUSDT", config)
            assert result is False

    @pytest.mark.asyncio
    async def test_upsert_symbol_config_with_error(self, mongodb_client):
        """Test upserting symbol config with error"""
        config = TradingConfig(
            symbol="BTCUSDT", parameters={"leverage": 10}, created_by="test_user"
        )

        with patch.object(
            mongodb_client.data_manager_client._client,
            "upsert_one",
            new_callable=AsyncMock,
        ) as mock_upsert:
            mock_upsert.side_effect = Exception("Connection error")

            result = await mongodb_client.upsert_symbol_config("BTCUSDT", config)
            assert result is False

    @pytest.mark.asyncio
    async def test_set_symbol_config(self, mongodb_client):
        """Test setting symbol config (alias for upsert)"""
        config = TradingConfig(
            symbol="BTCUSDT", parameters={"leverage": 10}, created_by="test_user"
        )

        with patch.object(
            mongodb_client, "upsert_symbol_config", new_callable=AsyncMock
        ) as mock_upsert:
            mock_upsert.return_value = True

            result = await mongodb_client.set_symbol_config(config)
            assert result is True
            # Should extract symbol from config
            mock_upsert.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_symbol_config_with_unknown_symbol(self, mongodb_client):
        """Test setting symbol config with unknown symbol"""
        config = TradingConfig(parameters={"leverage": 10}, created_by="test_user")

        with patch.object(
            mongodb_client, "upsert_symbol_config", new_callable=AsyncMock
        ) as mock_upsert:
            mock_upsert.return_value = True

            result = await mongodb_client.set_symbol_config(config)
            assert result is True
            # getattr returns None if symbol is None (not "UNKNOWN" since attribute exists)
            mock_upsert.assert_called_once_with(None, config)

    @pytest.mark.asyncio
    async def test_delete_symbol_config(self, mongodb_client):
        """Test deleting symbol config"""
        with patch.object(
            mongodb_client.data_manager_client._client,
            "delete_one",
            new_callable=AsyncMock,
        ) as mock_delete:
            mock_delete.return_value = {"deleted_count": 1}

            result = await mongodb_client.delete_symbol_config("BTCUSDT")
            assert result is True

    @pytest.mark.asyncio
    async def test_delete_symbol_config_not_found(self, mongodb_client):
        """Test deleting symbol config when not found"""
        with patch.object(
            mongodb_client.data_manager_client._client,
            "delete_one",
            new_callable=AsyncMock,
        ) as mock_delete:
            mock_delete.return_value = {"deleted_count": 0}

            result = await mongodb_client.delete_symbol_config("BTCUSDT")
            assert result is False

    @pytest.mark.asyncio
    async def test_delete_symbol_config_with_error(self, mongodb_client):
        """Test deleting symbol config with error"""
        with patch.object(
            mongodb_client.data_manager_client._client,
            "delete_one",
            new_callable=AsyncMock,
        ) as mock_delete:
            mock_delete.side_effect = Exception("Connection error")

            result = await mongodb_client.delete_symbol_config("BTCUSDT")
            assert result is False


class TestDataManagerConfigClientSymbolSideConfig:
    """Test symbol-side configuration methods"""

    @pytest.mark.asyncio
    async def test_get_symbol_side_config(self, mongodb_client):
        """Test getting symbol-side config"""
        with patch.object(
            mongodb_client.data_manager_client._client, "query", new_callable=AsyncMock
        ) as mock_query:
            mock_query.return_value = {"data": []}

            config = await mongodb_client.get_symbol_side_config("BTCUSDT", "LONG")
            assert config is None

    @pytest.mark.asyncio
    async def test_get_symbol_side_config_with_data(self, mongodb_client):
        """Test getting symbol-side config with data"""
        with patch.object(
            mongodb_client.data_manager_client._client, "query", new_callable=AsyncMock
        ) as mock_query:
            mock_query.return_value = {
                "data": [
                    {
                        "_id": "test_id",
                        "symbol": "BTCUSDT",
                        "side": "LONG",
                        "parameters": {"leverage": 10},
                        "created_by": "test_user",
                        "created_at": datetime.utcnow(),
                        "updated_at": datetime.utcnow(),
                    }
                ]
            }

            config = await mongodb_client.get_symbol_side_config("BTCUSDT", "LONG")
            assert config is not None
            assert config.symbol == "BTCUSDT"
            assert config.side == "LONG"

    @pytest.mark.asyncio
    async def test_get_symbol_side_config_with_error(self, mongodb_client):
        """Test getting symbol-side config with error"""
        with patch.object(
            mongodb_client.data_manager_client._client, "query", new_callable=AsyncMock
        ) as mock_query:
            mock_query.side_effect = Exception("Connection error")

            config = await mongodb_client.get_symbol_side_config("BTCUSDT", "LONG")
            assert config is None

    @pytest.mark.asyncio
    async def test_set_symbol_side_config(self, mongodb_client):
        """Test setting symbol-side config"""
        config = TradingConfig(
            symbol="BTCUSDT",
            side="LONG",
            parameters={"leverage": 10},
            created_by="test_user",
        )

        with patch.object(
            mongodb_client.data_manager_client._client,
            "upsert_one",
            new_callable=AsyncMock,
        ) as mock_upsert:
            mock_upsert.return_value = {"upserted_id": "test_id"}

            result = await mongodb_client.set_symbol_side_config(config)
            assert result is True

    @pytest.mark.asyncio
    async def test_set_symbol_side_config_with_modified_count(self, mongodb_client):
        """Test setting symbol-side config with modified_count"""
        config = TradingConfig(
            symbol="BTCUSDT",
            side="LONG",
            parameters={"leverage": 10},
            created_by="test_user",
        )

        with patch.object(
            mongodb_client.data_manager_client._client,
            "upsert_one",
            new_callable=AsyncMock,
        ) as mock_upsert:
            mock_upsert.return_value = {"modified_count": 1}

            result = await mongodb_client.set_symbol_side_config(config)
            assert result is True

    @pytest.mark.asyncio
    async def test_set_symbol_side_config_failure(self, mongodb_client):
        """Test setting symbol-side config with failure"""
        config = TradingConfig(
            symbol="BTCUSDT",
            side="LONG",
            parameters={"leverage": 10},
            created_by="test_user",
        )

        with patch.object(
            mongodb_client.data_manager_client._client,
            "upsert_one",
            new_callable=AsyncMock,
        ) as mock_upsert:
            mock_upsert.return_value = {}

            result = await mongodb_client.set_symbol_side_config(config)
            assert result is False

    @pytest.mark.asyncio
    async def test_set_symbol_side_config_with_error(self, mongodb_client):
        """Test setting symbol-side config with error"""
        config = TradingConfig(
            symbol="BTCUSDT",
            side="LONG",
            parameters={"leverage": 10},
            created_by="test_user",
        )

        with patch.object(
            mongodb_client.data_manager_client._client,
            "upsert_one",
            new_callable=AsyncMock,
        ) as mock_upsert:
            mock_upsert.side_effect = Exception("Connection error")

            result = await mongodb_client.set_symbol_side_config(config)
            assert result is False

    @pytest.mark.asyncio
    async def test_delete_symbol_side_config(self, mongodb_client):
        """Test deleting symbol-side config"""
        with patch.object(
            mongodb_client.data_manager_client._client,
            "delete_one",
            new_callable=AsyncMock,
        ) as mock_delete:
            mock_delete.return_value = {"deleted_count": 1}

            result = await mongodb_client.delete_symbol_side_config("BTCUSDT", "LONG")
            assert result is True

    @pytest.mark.asyncio
    async def test_delete_symbol_side_config_not_found(self, mongodb_client):
        """Test deleting symbol-side config when not found"""
        with patch.object(
            mongodb_client.data_manager_client._client,
            "delete_one",
            new_callable=AsyncMock,
        ) as mock_delete:
            mock_delete.return_value = {"deleted_count": 0}

            result = await mongodb_client.delete_symbol_side_config("BTCUSDT", "LONG")
            assert result is False

    @pytest.mark.asyncio
    async def test_delete_symbol_side_config_with_error(self, mongodb_client):
        """Test deleting symbol-side config with error"""
        with patch.object(
            mongodb_client.data_manager_client._client,
            "delete_one",
            new_callable=AsyncMock,
        ) as mock_delete:
            mock_delete.side_effect = Exception("Connection error")

            result = await mongodb_client.delete_symbol_side_config("BTCUSDT", "LONG")
            assert result is False


class TestDataManagerConfigClientAuditTrail:
    """Test audit trail methods"""

    @pytest.mark.asyncio
    async def test_create_audit_record(self, mongodb_client):
        """Test creating audit record"""
        audit = TradingConfigAudit(
            config_type="symbol",
            symbol="BTCUSDT",
            action="update",
            changed_by="test_user",
        )

        with patch.object(
            mongodb_client.data_manager_client._client,
            "insert_one",
            new_callable=AsyncMock,
        ) as mock_insert:
            mock_insert.return_value = {"inserted_id": "test_id"}

            result = await mongodb_client.create_audit_record(audit)
            assert result is True

    @pytest.mark.asyncio
    async def test_create_audit_record_failure(self, mongodb_client):
        """Test creating audit record with failure"""
        audit = TradingConfigAudit(
            config_type="symbol",
            symbol="BTCUSDT",
            action="update",
            changed_by="test_user",
        )

        with patch.object(
            mongodb_client.data_manager_client._client,
            "insert_one",
            new_callable=AsyncMock,
        ) as mock_insert:
            mock_insert.return_value = {}

            result = await mongodb_client.create_audit_record(audit)
            assert result is False

    @pytest.mark.asyncio
    async def test_create_audit_record_with_error(self, mongodb_client):
        """Test creating audit record with error"""
        audit = TradingConfigAudit(
            config_type="symbol",
            symbol="BTCUSDT",
            action="update",
            changed_by="test_user",
        )

        with patch.object(
            mongodb_client.data_manager_client._client,
            "insert_one",
            new_callable=AsyncMock,
        ) as mock_insert:
            mock_insert.side_effect = Exception("Connection error")

            result = await mongodb_client.create_audit_record(audit)
            assert result is False

    @pytest.mark.asyncio
    async def test_add_audit_record(self, mongodb_client):
        """Test adding audit record (alias for create)"""
        audit = TradingConfigAudit(
            config_type="symbol",
            symbol="BTCUSDT",
            action="update",
            changed_by="test_user",
        )

        with patch.object(
            mongodb_client, "create_audit_record", new_callable=AsyncMock
        ) as mock_create:
            mock_create.return_value = True

            result = await mongodb_client.add_audit_record(audit)
            assert result is True
            mock_create.assert_called_once_with(audit)

    @pytest.mark.asyncio
    async def test_get_audit_trail(self, mongodb_client):
        """Test getting audit trail"""
        with patch.object(
            mongodb_client.data_manager_client._client, "query", new_callable=AsyncMock
        ) as mock_query:
            mock_query.return_value = {"data": []}

            trail = await mongodb_client.get_audit_trail()
            assert isinstance(trail, list)

    @pytest.mark.asyncio
    async def test_get_audit_trail_with_limit(self, mongodb_client):
        """Test getting audit trail with limit"""
        with patch.object(
            mongodb_client.data_manager_client._client, "query", new_callable=AsyncMock
        ) as mock_query:
            mock_query.return_value = {"data": []}

            trail = await mongodb_client.get_audit_trail(limit=50)
            assert isinstance(trail, list)

    @pytest.mark.asyncio
    async def test_get_audit_trail_with_data(self, mongodb_client):
        """Test getting audit trail with data"""
        with patch.object(
            mongodb_client.data_manager_client._client, "query", new_callable=AsyncMock
        ) as mock_query:
            mock_query.return_value = {
                "data": [
                    {
                        "config_type": "symbol",
                        "symbol": "BTCUSDT",
                        "action": "update",
                        "changed_by": "test_user",
                        "timestamp": datetime.utcnow(),
                    }
                ]
            }

            trail = await mongodb_client.get_audit_trail()
            assert len(trail) == 1

    @pytest.mark.asyncio
    async def test_get_audit_trail_with_error(self, mongodb_client):
        """Test getting audit trail with error"""
        with patch.object(
            mongodb_client.data_manager_client._client, "query", new_callable=AsyncMock
        ) as mock_query:
            mock_query.side_effect = Exception("Connection error")

            trail = await mongodb_client.get_audit_trail()
            assert trail == []

    @pytest.mark.asyncio
    async def test_get_audit_trail_with_no_response(self, mongodb_client):
        """Test getting audit trail with no response"""
        with patch.object(
            mongodb_client.data_manager_client._client, "query", new_callable=AsyncMock
        ) as mock_query:
            mock_query.return_value = None

            trail = await mongodb_client.get_audit_trail()
            assert trail == []
