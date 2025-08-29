"""
Global test configuration and fixtures for petrosa-tradeengine.
"""

import os
from typing import Generator
from unittest.mock import AsyncMock, Mock, patch

import pytest

# Set up test environment BEFORE any imports that might trigger validation
os.environ.update(
    {
        "MONGODB_URI": "mongodb://localhost:27017",
        "MONGODB_DATABASE": "test_tradeengine",
        "ENVIRONMENT": "test",
        "LOG_LEVEL": "DEBUG",
        "NATS_URL": "nats://localhost:4222",
        "BINANCE_API_KEY": "test_api_key",
        "BINANCE_API_SECRET": "test_api_secret",
        "BINANCE_TESTNET": "true",
        "REDIS_URL": "redis://localhost:6379",
        "JWT_SECRET_KEY": "test_jwt_secret_key_for_testing_only",
        "PROMETHEUS_ENABLED": "false",
        "NATS_ENABLED": "false",
        "SIMULATION_ENABLED": "true",
    }
)


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment() -> Generator[None, None, None]:
    """Set up test environment with mocked dependencies."""
    # Environment is already set up above
    yield


@pytest.fixture
def mock_mongodb_client():
    """Mock MongoDB client for testing."""
    with patch("pymongo.MongoClient") as mock_client:
        mock_db = Mock()
        mock_collection = Mock()
        mock_client.return_value = mock_db
        mock_db.__getitem__.return_value = mock_collection
        mock_collection.find_one.return_value = None
        mock_collection.insert_one.return_value = Mock(inserted_id="test_id")
        mock_collection.find.return_value = []
        yield mock_client


@pytest.fixture
def mock_nats_client():
    """Mock NATS client for testing."""
    with patch("nats.connect") as mock_connect:
        mock_client = AsyncMock()
        mock_connect.return_value = mock_client
        mock_client.publish = AsyncMock()
        mock_client.subscribe = AsyncMock()
        mock_client.close = AsyncMock()
        yield mock_client


@pytest.fixture
def mock_redis_client():
    """Mock Redis client for testing."""
    with patch("redis.Redis") as mock_redis:
        mock_client = Mock()
        mock_redis.return_value = mock_client
        mock_client.get.return_value = None
        mock_client.set.return_value = True
        mock_client.delete.return_value = 1
        yield mock_client


@pytest.fixture
def sample_signal_data():
    """Sample signal data for testing."""
    return {
        "signal_id": "test_signal_123",
        "symbol": "BTCUSDT",
        "action": "BUY",
        "confidence": 0.85,
        "strategy": "momentum_breakout",
        "timestamp": "2024-01-01T12:00:00Z",
        "metadata": {
            "price": 50000.0,
            "volume": 1.5,
            "stop_loss": 48000.0,
            "take_profit": 55000.0,
        },
    }


@pytest.fixture
def sample_order_data():
    """Sample order data for testing."""
    return {
        "order_id": "test_order_456",
        "symbol": "BTCUSDT",
        "side": "BUY",
        "type": "LIMIT",
        "quantity": 0.001,
        "price": 50000.0,
        "time_in_force": "GTC",
        "status": "NEW",
    }


@pytest.fixture
def mock_binance_client():
    """Mock Binance client for testing."""
    with patch("tradeengine.exchange.binance.BinanceFuturesClient") as mock_client:
        mock_instance = Mock()
        mock_client.return_value = mock_instance
        mock_instance.create_order.return_value = {
            "orderId": 123456,
            "status": "NEW",
            "symbol": "BTCUSDT",
        }
        mock_instance.get_account_info.return_value = {
            "totalWalletBalance": "10000.0",
            "availableBalance": "5000.0",
        }
        yield mock_instance


@pytest.fixture
def mock_environment_variables():
    """Mock environment variables for testing."""
    test_vars = {
        "MONGODB_URI": "mongodb://localhost:27017/test_db",
        "NATS_URL": "nats://localhost:4222",
        "ENVIRONMENT": "test",
        "LOG_LEVEL": "DEBUG",
    }

    with patch.dict(os.environ, test_vars):
        yield test_vars
