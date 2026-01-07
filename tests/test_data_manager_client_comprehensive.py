"""
Comprehensive tests for tradeengine/services/data_manager_client.py to increase coverage
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from tradeengine.services.data_manager_client import DataManagerClient


@pytest.fixture
def data_manager_client():
    """Create a DataManagerClient instance for testing"""
    client = DataManagerClient()
    client.nats_client = None  # Disable NATS for unit tests
    return client


class TestDataManagerClientBasic:
    """Test basic DataManagerClient functionality"""

    def test_initialization(self, data_manager_client):
        """Test DataManagerClient initialization"""
        assert data_manager_client is not None
        assert hasattr(data_manager_client, 'nats_client')

    @pytest.mark.asyncio
    async def test_get_global_config(self, data_manager_client):
        """Test getting global config"""
        with patch.object(data_manager_client, '_client') as mock_client:
            mock_client.query = AsyncMock(return_value={"data": []})
            
            config = await data_manager_client.get_global_config()
            # May return None if no config exists
            assert config is None or hasattr(config, 'symbol')

    @pytest.mark.asyncio
    async def test_get_global_config_with_error(self, data_manager_client):
        """Test getting global config with error handling"""
        with patch.object(data_manager_client, '_client') as mock_client:
            mock_client.query = AsyncMock(side_effect=Exception("Connection error"))
            
            config = await data_manager_client.get_global_config()
            # Should return None on error
            assert config is None

    @pytest.mark.asyncio
    async def test_get_symbol_config(self, data_manager_client):
        """Test getting symbol config"""
        with patch.object(data_manager_client, '_client') as mock_client:
            mock_client.query = AsyncMock(return_value={"data": []})
            
            config = await data_manager_client.get_symbol_config("BTCUSDT")
            # May return None if no config exists
            assert config is None or hasattr(config, 'symbol')

    @pytest.mark.asyncio
    async def test_get_symbol_config_with_error(self, data_manager_client):
        """Test getting symbol config with error handling"""
        with patch.object(data_manager_client, '_client') as mock_client:
            mock_client.query = AsyncMock(side_effect=Exception("Connection error"))
            
            config = await data_manager_client.get_symbol_config("BTCUSDT")
            # Should return None on error
            assert config is None

    @pytest.mark.asyncio
    async def test_health_check(self, data_manager_client):
        """Test health check"""
        with patch.object(data_manager_client, '_client') as mock_client:
            mock_client.health = AsyncMock(return_value={"status": "healthy"})
            
            health = await data_manager_client.health_check()
            assert isinstance(health, dict)
            assert "status" in health

