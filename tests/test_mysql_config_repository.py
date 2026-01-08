"""
Comprehensive tests for tradeengine/db/mysql_config_repository.py to increase coverage
"""

from datetime import datetime

import pytest

from contracts.trading_config import LeverageStatus, TradingConfig, TradingConfigAudit
from tradeengine.db.mysql_config_repository import MySQLConfigRepository


@pytest.fixture
def mysql_repo():
    """Create MySQLConfigRepository instance for testing"""
    return MySQLConfigRepository("mysql://user:pass@localhost:3306/testdb")


@pytest.fixture
def sample_config():
    """Create sample TradingConfig for testing"""
    return TradingConfig(
        symbol="BTCUSDT",
        parameters={"leverage": 10, "position_size_pct": 0.1},
        created_by="test",
    )


@pytest.fixture
def sample_leverage_status():
    """Create sample LeverageStatus for testing"""
    return LeverageStatus(
        id=None,
        symbol="BTCUSDT",
        configured_leverage=10,
        actual_leverage=10,
        last_sync_at=datetime.utcnow(),
        last_sync_success=True,
        last_sync_error=None,
        updated_at=datetime.utcnow(),
    )


@pytest.fixture
def sample_audit():
    """Create sample TradingConfigAudit for testing"""
    return TradingConfigAudit(
        id=None,
        config_type="global",
        symbol=None,
        side=None,
        action="update",
        old_values={},
        new_values={"leverage": 10},
        changed_by="test",
        timestamp=datetime.utcnow(),
    )


class TestMySQLConfigRepositoryBasic:
    """Test basic MySQLConfigRepository functionality"""

    def test_initialization(self, mysql_repo):
        """Test MySQLConfigRepository initialization"""
        assert mysql_repo is not None
        assert mysql_repo.mysql_uri == "mysql://user:pass@localhost:3306/testdb"
        assert mysql_repo.connected is False

    @pytest.mark.asyncio
    async def test_connect(self, mysql_repo):
        """Test connect method"""
        await mysql_repo.connect()
        # Currently returns False (stub implementation)
        assert mysql_repo.connected is False

    @pytest.mark.asyncio
    async def test_disconnect(self, mysql_repo):
        """Test disconnect method"""
        # Should not raise exception
        await mysql_repo.disconnect()


class TestConfigOperations:
    """Test config operations (all return None/False as stubs)"""

    @pytest.mark.asyncio
    async def test_get_global_config(self, mysql_repo):
        """Test get_global_config returns None (stub)"""
        result = await mysql_repo.get_global_config()
        assert result is None

    @pytest.mark.asyncio
    async def test_set_global_config(self, mysql_repo, sample_config):
        """Test set_global_config returns False (stub)"""
        result = await mysql_repo.set_global_config(sample_config)
        assert result is False

    @pytest.mark.asyncio
    async def test_get_symbol_config(self, mysql_repo):
        """Test get_symbol_config returns None (stub)"""
        result = await mysql_repo.get_symbol_config("BTCUSDT")
        assert result is None

    @pytest.mark.asyncio
    async def test_set_symbol_config(self, mysql_repo, sample_config):
        """Test set_symbol_config returns False (stub)"""
        result = await mysql_repo.set_symbol_config(sample_config)
        assert result is False

    @pytest.mark.asyncio
    async def test_get_symbol_side_config(self, mysql_repo):
        """Test get_symbol_side_config returns None (stub)"""
        result = await mysql_repo.get_symbol_side_config("BTCUSDT", "LONG")
        assert result is None

    @pytest.mark.asyncio
    async def test_set_symbol_side_config(self, mysql_repo, sample_config):
        """Test set_symbol_side_config returns False (stub)"""
        result = await mysql_repo.set_symbol_side_config(sample_config)
        assert result is False


class TestAuditOperations:
    """Test audit operations"""

    @pytest.mark.asyncio
    async def test_add_audit_record(self, mysql_repo, sample_audit):
        """Test add_audit_record returns False (stub)"""
        result = await mysql_repo.add_audit_record(sample_audit)
        assert result is False


class TestLeverageOperations:
    """Test leverage operations"""

    @pytest.mark.asyncio
    async def test_get_leverage_status(self, mysql_repo):
        """Test get_leverage_status returns None (stub)"""
        result = await mysql_repo.get_leverage_status("BTCUSDT")
        assert result is None

    @pytest.mark.asyncio
    async def test_set_leverage_status(self, mysql_repo, sample_leverage_status):
        """Test set_leverage_status returns False (stub)"""
        result = await mysql_repo.set_leverage_status(sample_leverage_status)
        assert result is False
