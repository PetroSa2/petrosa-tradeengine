"""
Tests for configuration rollback in Trade Engine.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from tradeengine.config_manager import TradingConfigManager


@pytest.fixture
def mock_mongodb_client():
    client = MagicMock()
    client.connected = True
    client.rollback_config = AsyncMock(return_value=True)
    return client


@pytest.mark.asyncio
async def test_trading_config_rollback(mock_mongodb_client):
    # Setup
    manager = TradingConfigManager(mongodb_client=mock_mongodb_client)
    # Mock get_config
    manager.get_config = AsyncMock(return_value={"leverage": 10})
    
    # Execute
    success, config, errors = await manager.rollback_config("admin", symbol="BTCUSDT")
    
    # Verify
    assert success is True
    assert config.symbol == "BTCUSDT"
    mock_mongodb_client.rollback_config.assert_called_once()
