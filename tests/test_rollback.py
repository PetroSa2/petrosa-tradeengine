"""
Tests for configuration rollback in Trade Engine.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from tradeengine.config_manager import TradingConfigManager


@pytest.fixture
def mock_mongodb_client():
    client = MagicMock()
    client.connected = True
    return client


@pytest.mark.asyncio
async def test_trading_config_rollback(mock_mongodb_client):
    # Setup
    manager = TradingConfigManager(mongodb_client=mock_mongodb_client)

    # Mock history
    mock_mongodb_client.get_config_history = AsyncMock(
        return_value=[
            {
                "action": "update",
                "parameters_before": {"leverage": 10},
                "parameters_after": {"leverage": 20},
            }
        ]
    )

    # Mock set_symbol_config
    mock_mongodb_client.set_symbol_config = AsyncMock(return_value=True)
    mock_mongodb_client.get_symbol_config = AsyncMock(return_value=None)
    mock_mongodb_client.add_audit_record = AsyncMock()

    # Execute
    success, config, errors = await manager.rollback_config("admin", symbol="BTCUSDT")

    # Verify
    assert success is True
    assert config is not None
    assert config.symbol == "BTCUSDT"
    mock_mongodb_client.get_config_history.assert_called_once()
    mock_mongodb_client.set_symbol_config.assert_called_once()
