"""
Test configuration override for petrosa-tradeengine.
This module provides test-specific configurations that bypass Kubernetes dependencies.
"""

import os
from unittest.mock import patch


class TestConfigManager:
    """Manages test-specific configuration overrides."""

    def __init__(self):
        self.original_env = {}
        self.test_env = {
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

    def setup_test_environment(self):
        """Set up test environment variables."""
        for key, value in self.test_env.items():
            self.original_env[key] = os.environ.get(key)
            os.environ[key] = value

    def teardown_test_environment(self):
        """Restore original environment variables."""
        for key, original_value in self.original_env.items():
            if original_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original_value
        self.original_env.clear()


def mock_mongodb_validation():
    """Mock MongoDB validation functions for testing."""

    def mock_validate_mongodb_config():
        """Mock validation that always passes."""
        pass

    def mock_get_mongodb_connection_string():
        """Mock connection string for testing."""
        return "mongodb://localhost:27017/test_tradeengine"

    return patch.multiple(
        "shared.constants",
        validate_mongodb_config=mock_validate_mongodb_config,
        get_mongodb_connection_string=mock_get_mongodb_connection_string,
    )


def mock_nats_validation():
    """Mock NATS validation functions for testing."""

    def mock_get_nats_connection_string():
        """Mock NATS connection string for testing."""
        return "nats://localhost:4222"

    return patch(
        "shared.constants.get_nats_connection_string", mock_get_nats_connection_string
    )


# Global test configuration manager
test_config = TestConfigManager()
