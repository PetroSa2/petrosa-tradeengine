"""
Test configuration and fixtures for the Petrosa Trading Engine tests.
"""

import os
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def mock_mongodb_config():
    """Automatically mock MongoDB configuration for all tests to avoid requiring actual MongoDB connection."""
    with patch("shared.constants.validate_mongodb_config"):
        yield


@pytest.fixture(autouse=True)
def mock_environment_variables():
    """Set up test environment variables."""
    test_env = {
        "MONGODB_URI": "mongodb://localhost:27017/test",
        "MONGODB_DATABASE": "test",
        "BINANCE_API_KEY": "test-api-key",
        "BINANCE_API_SECRET": "test-api-secret",
        "BINANCE_TESTNET": "true",
        "ENVIRONMENT": "test",
        "LOG_LEVEL": "INFO",
        "NATS_ENABLED": "false",
        "JWT_SECRET_KEY": "test-jwt-secret",
    }

    with patch.dict(os.environ, test_env):
        yield


@pytest.fixture(autouse=True)
def mock_external_services():
    """Mock external services that are not needed for unit tests."""
    with (
        patch("tradeengine.exchange.binance.BinanceFuturesExchange.initialize"),
        patch("tradeengine.exchange.simulator.SimulatorExchange.initialize"),
        patch("tradeengine.dispatcher.Dispatcher.initialize"),
        patch("tradeengine.exchange.binance.BinanceFuturesExchange.close"),
        patch("tradeengine.exchange.simulator.SimulatorExchange.close"),
        patch("tradeengine.dispatcher.Dispatcher.close"),
    ):
        yield
