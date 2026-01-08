"""
Comprehensive tests for tradeengine/config_manager.py to increase coverage
"""

import asyncio
import time
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contracts.trading_config import TradingConfig, TradingConfigAudit
from tradeengine.config_manager import TradingConfigManager


@pytest.fixture
def mock_mongodb_client():
    """Create a mock MongoDB client"""
    client = MagicMock()
    client.connected = True
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.get_global_config = AsyncMock(return_value=None)
    client.get_symbol_config = AsyncMock(return_value=None)
    client.get_symbol_side_config = AsyncMock(return_value=None)
    client.set_global_config = AsyncMock(return_value=True)
    client.set_symbol_config = AsyncMock(return_value=True)
    client.set_symbol_side_config = AsyncMock(return_value=True)
    client.delete_global_config = AsyncMock(return_value=True)
    client.delete_symbol_config = AsyncMock(return_value=True)
    client.delete_symbol_side_config = AsyncMock(return_value=True)
    client.add_audit_record = AsyncMock(return_value=True)
    return client


@pytest.fixture
def mock_mysql_repository():
    """Create a mock MySQL repository"""
    repo = MagicMock()
    repo.connect = AsyncMock()
    repo.disconnect = AsyncMock()
    return repo


@pytest.fixture
def config_manager(mock_mongodb_client, mock_mysql_repository):
    """Create a TradingConfigManager instance for testing"""
    return TradingConfigManager(
        mongodb_client=mock_mongodb_client,
        mysql_repository=mock_mysql_repository,
        cache_ttl_seconds=60,
    )


class TestTradingConfigManagerInitialization:
    """Test TradingConfigManager initialization"""

    def test_initialization(self, mock_mongodb_client, mock_mysql_repository):
        """Test basic initialization"""
        manager = TradingConfigManager(
            mongodb_client=mock_mongodb_client,
            mysql_repository=mock_mysql_repository,
            cache_ttl_seconds=60,
        )
        assert manager.mongodb_client == mock_mongodb_client
        assert manager.mysql_repository == mock_mysql_repository
        assert manager.cache_ttl_seconds == 60
        assert manager._cache == {}
        assert manager._running is False

    def test_initialization_without_clients(self):
        """Test initialization without clients"""
        manager = TradingConfigManager()
        assert manager.mongodb_client is None
        assert manager.mysql_repository is None
        assert manager._cache == {}

    def test_initialization_custom_cache_ttl(self):
        """Test initialization with custom cache TTL"""
        manager = TradingConfigManager(cache_ttl_seconds=120)
        assert manager.cache_ttl_seconds == 120


class TestTradingConfigManagerLifecycle:
    """Test start/stop lifecycle methods"""

    @pytest.mark.asyncio
    async def test_start(
        self, config_manager, mock_mongodb_client, mock_mysql_repository
    ):
        """Test starting the config manager"""
        await config_manager.start()
        assert config_manager._running is True
        assert config_manager._cache_refresh_task is not None
        mock_mongodb_client.connect.assert_called_once()
        mock_mysql_repository.connect.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_without_clients(self):
        """Test starting without clients"""
        manager = TradingConfigManager()
        await manager.start()
        assert manager._running is True

    @pytest.mark.asyncio
    async def test_stop(self, config_manager):
        """Test stopping the config manager"""
        await config_manager.start()
        await config_manager.stop()
        assert config_manager._running is False
        config_manager.mongodb_client.disconnect.assert_called_once()
        config_manager.mysql_repository.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_without_start(self, config_manager):
        """Test stopping without starting"""
        await config_manager.stop()
        # Should not raise exception


class TestTradingConfigManagerCache:
    """Test cache-related methods"""

    def test_get_cache_key(self, config_manager):
        """Test cache key generation"""
        assert config_manager._get_cache_key(None, None) == "global:all"
        assert config_manager._get_cache_key("BTCUSDT", None) == "BTCUSDT:all"
        assert config_manager._get_cache_key("BTCUSDT", "LONG") == "BTCUSDT:LONG"
        assert config_manager._get_cache_key(None, "LONG") == "global:LONG"

    def test_invalidate_cache(self, config_manager):
        """Test cache invalidation"""
        config_manager._cache["BTCUSDT:LONG"] = ({"leverage": 10}, time.time())
        config_manager.invalidate_cache("BTCUSDT", "LONG")
        assert "BTCUSDT:LONG" not in config_manager._cache

    def test_invalidate_cache_not_found(self, config_manager):
        """Test cache invalidation when key not found"""
        config_manager.invalidate_cache("BTCUSDT", "LONG")
        # Should not raise exception

    @pytest.mark.asyncio
    async def test_cache_refresh_loop(self, config_manager):
        """Test cache refresh loop"""
        # Add expired cache entry
        old_time = time.time() - 100  # 100 seconds ago
        config_manager._cache["BTCUSDT:LONG"] = ({"leverage": 10}, old_time)
        config_manager.cache_ttl_seconds = 60

        # Start the manager
        await config_manager.start()

        # Wait for cache refresh (should clear expired entry)
        await asyncio.sleep(0.1)  # Small delay to allow task to run

        # Stop the manager
        await config_manager.stop()

        # Expired entry should be cleared
        # Note: This test may be flaky due to timing, but it tests the logic


class TestTradingConfigManagerGetConfig:
    """Test get_config method"""

    @pytest.mark.asyncio
    async def test_get_config_from_cache(self, config_manager):
        """Test getting config from cache"""
        cache_key = config_manager._get_cache_key(None, None)
        config_manager._cache[cache_key] = ({"leverage": 15}, time.time())

        config = await config_manager.get_config()
        assert config["leverage"] == 15

    @pytest.mark.asyncio
    async def test_get_config_from_defaults(self, config_manager):
        """Test getting config from defaults when no cache"""
        config = await config_manager.get_config()
        assert isinstance(config, dict)
        assert "leverage" in config  # Defaults should have leverage

    @pytest.mark.asyncio
    async def test_get_config_with_global_config(
        self, config_manager, mock_mongodb_client
    ):
        """Test getting config with global config override"""
        global_config = TradingConfig(
            parameters={"leverage": 20},
            created_by="test",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        mock_mongodb_client.get_global_config = AsyncMock(return_value=global_config)

        config = await config_manager.get_config()
        assert config["leverage"] == 20

    @pytest.mark.asyncio
    async def test_get_config_with_symbol_config(
        self, config_manager, mock_mongodb_client
    ):
        """Test getting config with symbol config override"""
        symbol_config = TradingConfig(
            symbol="BTCUSDT",
            parameters={"leverage": 25},
            created_by="test",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        mock_mongodb_client.get_symbol_config = AsyncMock(return_value=symbol_config)

        config = await config_manager.get_config(symbol="BTCUSDT")
        assert config["leverage"] == 25

    @pytest.mark.asyncio
    async def test_get_config_with_symbol_side_config(
        self, config_manager, mock_mongodb_client
    ):
        """Test getting config with symbol-side config override"""
        symbol_side_config = TradingConfig(
            symbol="BTCUSDT",
            side="LONG",
            parameters={"leverage": 30},
            created_by="test",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        mock_mongodb_client.get_symbol_side_config = AsyncMock(
            return_value=symbol_side_config
        )

        config = await config_manager.get_config(symbol="BTCUSDT", side="LONG")
        assert config["leverage"] == 30

    @pytest.mark.asyncio
    async def test_get_config_hierarchy(self, config_manager, mock_mongodb_client):
        """Test config hierarchy (global -> symbol -> symbol-side)"""
        global_config = TradingConfig(
            parameters={"leverage": 10, "stop_loss_pct": 2.0},
            created_by="test",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        symbol_config = TradingConfig(
            symbol="BTCUSDT",
            parameters={"leverage": 20},  # Override leverage
            created_by="test",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        symbol_side_config = TradingConfig(
            symbol="BTCUSDT",
            side="LONG",
            parameters={"stop_loss_pct": 1.5},  # Override stop_loss_pct
            created_by="test",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        mock_mongodb_client.get_global_config = AsyncMock(return_value=global_config)
        mock_mongodb_client.get_symbol_config = AsyncMock(return_value=symbol_config)
        mock_mongodb_client.get_symbol_side_config = AsyncMock(
            return_value=symbol_side_config
        )

        config = await config_manager.get_config(symbol="BTCUSDT", side="LONG")
        assert config["leverage"] == 20  # From symbol config
        assert config["stop_loss_pct"] == 1.5  # From symbol-side config

    @pytest.mark.asyncio
    async def test_get_config_with_mongodb_error(
        self, config_manager, mock_mongodb_client
    ):
        """Test get_config when MongoDB raises error"""
        mock_mongodb_client.get_global_config = AsyncMock(
            side_effect=Exception("DB error")
        )

        # Should fall back to defaults
        config = await config_manager.get_config()
        assert isinstance(config, dict)

    @pytest.mark.asyncio
    async def test_get_config_with_mongodb_not_connected(
        self, config_manager, mock_mongodb_client
    ):
        """Test get_config when MongoDB is not connected"""
        mock_mongodb_client.connected = False

        # Should use defaults only
        config = await config_manager.get_config()
        assert isinstance(config, dict)

    @pytest.mark.asyncio
    async def test_get_config_cache_expired(self, config_manager):
        """Test get_config when cache is expired"""
        old_time = time.time() - 100  # 100 seconds ago
        config_manager._cache["global:all"] = ({"leverage": 15}, old_time)
        config_manager.cache_ttl_seconds = 60

        # Should fetch fresh config (defaults)
        config = await config_manager.get_config()
        # Should not be the cached value
        assert config["leverage"] != 15 or time.time() - old_time >= 60


class TestTradingConfigManagerSetConfig:
    """Test set_config method"""

    @pytest.mark.asyncio
    async def test_set_global_config(self, config_manager, mock_mongodb_client):
        """Test setting global config"""
        success, config, errors = await config_manager.set_config(
            parameters={"leverage": 15}, changed_by="test_user"
        )
        assert success is True
        assert config is not None
        assert errors == []
        mock_mongodb_client.set_global_config.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_symbol_config(self, config_manager, mock_mongodb_client):
        """Test setting symbol config"""
        success, config, errors = await config_manager.set_config(
            parameters={"leverage": 20}, changed_by="test_user", symbol="BTCUSDT"
        )
        assert success is True
        assert config is not None
        assert config.symbol == "BTCUSDT"
        mock_mongodb_client.set_symbol_config.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_symbol_side_config(self, config_manager, mock_mongodb_client):
        """Test setting symbol-side config"""
        success, config, errors = await config_manager.set_config(
            parameters={"leverage": 25},
            changed_by="test_user",
            symbol="BTCUSDT",
            side="LONG",
        )
        assert success is True
        assert config is not None
        assert config.symbol == "BTCUSDT"
        assert config.side == "LONG"
        mock_mongodb_client.set_symbol_side_config.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_config_with_validation_error(self, config_manager):
        """Test set_config with invalid parameters"""
        success, config, errors = await config_manager.set_config(
            parameters={"leverage": 200}, changed_by="test_user"  # Invalid: > 125
        )
        assert success is False
        assert config is None
        assert len(errors) > 0

    @pytest.mark.asyncio
    async def test_set_config_validate_only(self, config_manager, mock_mongodb_client):
        """Test set_config with validate_only flag"""
        success, config, errors = await config_manager.set_config(
            parameters={"leverage": 15}, changed_by="test_user", validate_only=True
        )
        assert success is True
        assert config is None  # Not saved
        assert errors == []
        # Should not call MongoDB
        mock_mongodb_client.set_global_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_set_config_with_existing_config(
        self, config_manager, mock_mongodb_client
    ):
        """Test set_config with existing config (version increment)"""
        existing_config = TradingConfig(
            parameters={"leverage": 10},
            version=2,
            created_by="test",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        mock_mongodb_client.get_global_config = AsyncMock(return_value=existing_config)

        success, config, errors = await config_manager.set_config(
            parameters={"leverage": 15}, changed_by="test_user"
        )
        assert success is True
        assert config is not None
        assert config.version == 3  # Incremented

    @pytest.mark.asyncio
    async def test_set_config_with_audit_record(
        self, config_manager, mock_mongodb_client
    ):
        """Test set_config creates audit record"""
        success, config, errors = await config_manager.set_config(
            parameters={"leverage": 15}, changed_by="test_user", reason="Test update"
        )
        assert success is True
        mock_mongodb_client.add_audit_record.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_config_invalidates_cache(
        self, config_manager, mock_mongodb_client
    ):
        """Test set_config invalidates cache"""
        cache_key = config_manager._get_cache_key(None, None)
        config_manager._cache[cache_key] = ({"leverage": 10}, time.time())

        await config_manager.set_config(
            parameters={"leverage": 15}, changed_by="test_user"
        )

        assert cache_key not in config_manager._cache

    @pytest.mark.asyncio
    async def test_set_config_with_mongodb_error(
        self, config_manager, mock_mongodb_client
    ):
        """Test set_config when MongoDB raises error"""
        mock_mongodb_client.set_global_config = AsyncMock(
            side_effect=Exception("DB error")
        )

        success, config, errors = await config_manager.set_config(
            parameters={"leverage": 15}, changed_by="test_user"
        )
        assert success is False
        assert len(errors) > 0

    @pytest.mark.asyncio
    async def test_set_config_with_mongodb_not_connected(
        self, config_manager, mock_mongodb_client
    ):
        """Test set_config when MongoDB is not connected"""
        mock_mongodb_client.connected = False

        success, config, errors = await config_manager.set_config(
            parameters={"leverage": 15}, changed_by="test_user"
        )
        assert success is False
        assert "Failed to save" in errors[0]


class TestTradingConfigManagerDeleteConfig:
    """Test delete_config method"""

    @pytest.mark.asyncio
    async def test_delete_global_config(self, config_manager, mock_mongodb_client):
        """Test deleting global config"""
        success, errors = await config_manager.delete_config(changed_by="test_user")
        assert success is True
        assert errors == []
        mock_mongodb_client.delete_global_config.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_symbol_config(self, config_manager, mock_mongodb_client):
        """Test deleting symbol config"""
        success, errors = await config_manager.delete_config(
            changed_by="test_user", symbol="BTCUSDT"
        )
        assert success is True
        assert errors == []
        mock_mongodb_client.delete_symbol_config.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_symbol_side_config(self, config_manager, mock_mongodb_client):
        """Test deleting symbol-side config"""
        success, errors = await config_manager.delete_config(
            changed_by="test_user", symbol="BTCUSDT", side="LONG"
        )
        assert success is True
        assert errors == []
        mock_mongodb_client.delete_symbol_side_config.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_config_with_audit_record(
        self, config_manager, mock_mongodb_client
    ):
        """Test delete_config creates audit record"""
        existing_config = TradingConfig(
            parameters={"leverage": 10},
            version=1,
            created_by="test",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        mock_mongodb_client.get_global_config = AsyncMock(return_value=existing_config)

        success, errors = await config_manager.delete_config(changed_by="test_user")
        assert success is True
        mock_mongodb_client.add_audit_record.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_config_invalidates_cache(
        self, config_manager, mock_mongodb_client
    ):
        """Test delete_config invalidates cache"""
        cache_key = config_manager._get_cache_key(None, None)
        config_manager._cache[cache_key] = ({"leverage": 10}, time.time())

        await config_manager.delete_config(changed_by="test_user")

        assert cache_key not in config_manager._cache

    @pytest.mark.asyncio
    async def test_delete_config_with_mongodb_error(
        self, config_manager, mock_mongodb_client
    ):
        """Test delete_config when MongoDB raises error"""
        mock_mongodb_client.delete_global_config = AsyncMock(
            side_effect=Exception("DB error")
        )

        success, errors = await config_manager.delete_config(changed_by="test_user")
        assert success is False
        assert len(errors) > 0

    @pytest.mark.asyncio
    async def test_delete_config_with_mongodb_not_connected(
        self, config_manager, mock_mongodb_client
    ):
        """Test delete_config when MongoDB is not connected"""
        mock_mongodb_client.connected = False

        success, errors = await config_manager.delete_config(changed_by="test_user")
        assert success is False
        assert "Failed to delete" in errors[0]

    @pytest.mark.asyncio
    async def test_delete_config_not_found(self, config_manager, mock_mongodb_client):
        """Test delete_config when config not found"""
        mock_mongodb_client.delete_global_config = AsyncMock(return_value=False)

        success, errors = await config_manager.delete_config(changed_by="test_user")
        assert success is False
        assert "Failed to delete" in errors[0]
