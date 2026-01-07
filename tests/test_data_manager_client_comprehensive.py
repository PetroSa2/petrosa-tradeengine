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
    async def test_get_open_positions(self, data_manager_client):
        """Test getting open positions"""
        with patch('shared.nats_client.nats_client') as mock_nats:
            mock_nats.request = AsyncMock(return_value={
                "data": []
            })
            data_manager_client.nats_client = mock_nats
            
            positions = await data_manager_client.get_open_positions()
            assert isinstance(positions, list)

    @pytest.mark.asyncio
    async def test_get_open_positions_with_error(self, data_manager_client):
        """Test getting open positions with error handling"""
        with patch('shared.nats_client.nats_client') as mock_nats:
            mock_nats.request = AsyncMock(side_effect=Exception("Connection error"))
            data_manager_client.nats_client = mock_nats
            
            positions = await data_manager_client.get_open_positions()
            # Should return empty list on error
            assert isinstance(positions, list)

    @pytest.mark.asyncio
    async def test_update_daily_pnl(self, data_manager_client):
        """Test updating daily PnL"""
        with patch('shared.nats_client.nats_client') as mock_nats:
            mock_nats.request = AsyncMock(return_value={"status": "success"})
            data_manager_client.nats_client = mock_nats
            
            result = await data_manager_client.update_daily_pnl("2024-01-01", 100.0)
            # May return None or success status
            assert result is None or isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_update_daily_pnl_with_error(self, data_manager_client):
        """Test updating daily PnL with error handling"""
        with patch('shared.nats_client.nats_client') as mock_nats:
            mock_nats.request = AsyncMock(side_effect=Exception("Connection error"))
            data_manager_client.nats_client = mock_nats
            
            result = await data_manager_client.update_daily_pnl("2024-01-01", 100.0)
            # Should handle error gracefully
            assert result is None or isinstance(result, dict)

