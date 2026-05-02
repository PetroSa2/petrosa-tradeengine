"""
Global test configuration and fixtures for petrosa-tradeengine.
"""

import os
import sys
from unittest.mock import MagicMock

# Pre-mock binance in sys.modules BEFORE any imports to survive xdist workers
_mock_binance = MagicMock()
_mock_binance.Client = MagicMock()
sys.modules["binance"] = _mock_binance
sys.modules["binance.enums"] = MagicMock()
sys.modules["binance.exceptions"] = MagicMock()

# Disable OpenTelemetry auto-initialization during tests (BEFORE any imports)
os.environ["OTEL_NO_AUTO_INIT"] = "1"
os.environ["OTEL_SDK_DISABLED"] = "true"
os.environ["OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED"] = "false"

# Set up test environment BEFORE any imports
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

# Add tests directory to path for otel_cleanup
sys.path.append(os.path.dirname(__file__))

from collections.abc import Generator  # noqa: E402
from unittest.mock import AsyncMock, MagicMock, Mock, patch  # noqa: E402

import pytest  # noqa: E402
from otel_cleanup import cleanup_logging, restore_all_otel_init_patches  # noqa: E402


# =============================================================================
# GLOBAL BINANCE CLIENT MOCK
# =============================================================================
# This prevents ANY real network calls to Binance during tests
@pytest.fixture(scope="session", autouse=True)
def mock_binance_global():
    """Globally mock Binance clients to prevent network calls."""
    mock_client = Mock()

    # Mock return values for common methods
    mock_client.futures_ping.return_value = {}
    mock_client.futures_account.return_value = {
        "totalWalletBalance": "10000.0",
        "availableBalance": "5000.0",
        "assets": [],
    }
    mock_client.futures_exchange_info.return_value = {"symbols": []}
    mock_client.futures_symbol_ticker.return_value = {
        "symbol": "BTCUSDT",
        "price": "50000.0",
    }
    mock_client.futures_get_open_orders.return_value = []
    mock_client.futures_position_information.return_value = []
    mock_client.futures_get_position_mode.return_value = {"dualSidePosition": False}

    # Patch in confirmed locations
    with (
        patch("binance.Client", return_value=mock_client),
        patch("tradeengine.exchange.binance.Client", return_value=mock_client),
    ):
        yield {"client": mock_client}


@pytest.fixture(scope="session", autouse=True)
def mock_rate_limit_monitor():
    """Globally mock RateLimitMonitor to prevent NATS connection hangs."""
    mock_monitor = MagicMock()
    mock_monitor.start = AsyncMock()
    mock_monitor.stop = AsyncMock()
    mock_monitor.update_from_headers = AsyncMock()

    with patch(
        "tradeengine.exchange.binance.RateLimitMonitor", return_value=mock_monitor
    ):
        yield mock_monitor


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment() -> Generator[None, None, None]:
    """Set up test environment."""
    yield


@pytest.fixture(autouse=True)
def cleanup_logging_state():
    """Clean up logging state before and after each test.

    NOTE: We deliberately do NOT call patch.stopall() here because it
    destroys session-scoped mocks (e.g. mock_binance_global).  OTEL
    patch cleanup is handled by restore_all_otel_init_patches() in
    teardown, which selectively restores only otel_init patches.
    """
    # BEFORE test — only reset logging handlers
    cleanup_logging()

    yield

    # AFTER test — reset logging and selectively restore OTEL patches
    cleanup_logging()
    restore_all_otel_init_patches()


# =============================================================================
# SESSION SCOPE MOCKS (Optimization)
# =============================================================================


@pytest.fixture(scope="session")
def mock_mongodb_client_session():
    """Session-scoped Mock MongoDB client."""
    mock_client = Mock()
    mock_db = Mock()
    mock_collection = Mock()
    mock_client.return_value = mock_db
    mock_db.__getitem__.return_value = mock_collection
    mock_collection.find_one.return_value = None
    mock_collection.insert_one.return_value = Mock(inserted_id="test_id")
    mock_collection.find.return_value = []
    return mock_client


@pytest.fixture(scope="session")
def mock_nats_client_session():
    """Session-scoped Mock NATS client."""
    mock_client = AsyncMock()
    mock_client.publish = AsyncMock()
    mock_client.subscribe = AsyncMock()
    mock_client.close = AsyncMock()
    return mock_client


# =============================================================================
# FUNCTION SCOPE FIXTURES (For backward compatibility)
# =============================================================================


def get_real_configure_logging():
    """
    Helper function to get the real configure_logging function, not a mock.

    NOTE: We do NOT call patch.stopall() here because it destroys session-scoped
    mocks (e.g. mock_binance_global). Only reload the otel_init module.
    """
    import importlib
    import sys

    if "otel_init" in sys.modules:
        module = sys.modules["otel_init"]
        module_file = getattr(module, "__file__", None)

        if module_file and module_file.endswith(".py"):
            try:
                importlib.reload(module)

                fresh_otel_init = sys.modules["otel_init"]
                fresh_func = getattr(fresh_otel_init, "configure_logging", None)

                if fresh_func:
                    if "tradeengine.api" in sys.modules:
                        sys.modules["tradeengine.api"].otel_init = fresh_otel_init

                    def wrapped_configure_logging(*args, func=fresh_func, **kwargs):
                        return func(*args, **kwargs)

                    wrapped_configure_logging._module = fresh_otel_init
                    wrapped_configure_logging._original_func = fresh_func
                    return wrapped_configure_logging
            except Exception:
                pass

    import otel_init

    func = otel_init.configure_logging

    def wrapped_configure_logging(*args, **kwargs):
        return func(*args, **kwargs)

    wrapped_configure_logging._module = otel_init
    wrapped_configure_logging._original_func = func
    return wrapped_configure_logging


@pytest.fixture
def mock_mongodb_client(mock_mongodb_client_session):
    """Mock MongoDB client for testing."""
    with patch("pymongo.MongoClient", return_value=mock_mongodb_client_session):
        yield mock_mongodb_client_session


@pytest.fixture
def mock_nats_client(mock_nats_client_session):
    """Mock NATS client for testing."""
    with patch("nats.connect", return_value=mock_nats_client_session):
        yield mock_nats_client_session


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
