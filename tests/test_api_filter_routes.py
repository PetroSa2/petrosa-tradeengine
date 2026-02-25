"""
Tests for strategy-specific filter API endpoints.

Tests all endpoints in api_filter_routes.py:
- GET /api/v1/config/filters/strategy/{strategy_id}
- PUT /api/v1/config/filters/strategy/{strategy_id}
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from contracts.trading_config import TradingConfig
from tradeengine.api import app
from tradeengine.api_filter_routes import get_config_manager, set_config_manager
from tradeengine.config_manager import TradingConfigManager


@pytest.fixture
def client() -> TestClient:
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def mock_config_manager():
    """Create mock config manager."""
    manager = MagicMock(spec=TradingConfigManager)
    manager.mongodb_client = MagicMock()
    manager.mongodb_client.connected = True
    manager._cache = {}
    return manager


@pytest.fixture(autouse=True)
def setup_config_manager(mock_config_manager):
    """Setup config manager for tests."""
    set_config_manager(mock_config_manager)
    yield
    set_config_manager(None)


class TestStrategyFilterRoutes:
    """Test strategy-specific filter routes."""

    def test_get_strategy_filters_success(self, client, mock_config_manager):
        """Test successful retrieval of strategy filters."""
        # Setup mock
        mock_config_manager.get_config = AsyncMock(
            return_value={
                "tp_distance_min_pct": 1.0,
                "tp_distance_max_pct": 10.0,
                "sl_distance_min_pct": 0.5,
                "sl_distance_max_pct": 5.0,
                "price_min_absolute": 100.0,
                "price_max_absolute": 100000.0,
                "enabled_sides": ["LONG", "SHORT"],
                "other_param": "should_be_filtered_out",
            }
        )

        # Make request
        response = client.get("/api/v1/config/filters/strategy/momentum_strategy")

        # Assert response
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["strategy_id"] == "momentum_strategy"
        assert "filters" in data["data"]
        filters = data["data"]["filters"]
        assert "tp_distance_min_pct" in filters
        assert "tp_distance_max_pct" in filters
        assert "sl_distance_min_pct" in filters
        assert "sl_distance_max_pct" in filters
        assert "price_min_absolute" in filters
        assert "price_max_absolute" in filters
        assert "enabled_sides" in filters
        # Verify non-filter params are filtered out
        assert "other_param" not in filters

        # Verify config manager was called correctly
        mock_config_manager.get_config.assert_called_once_with(
            symbol=None, side=None, strategy_id="momentum_strategy"
        )

    def test_get_strategy_filters_config_manager_error(
        self, client, mock_config_manager
    ):
        """Test error handling when config manager fails."""
        # Setup mock to raise exception
        mock_config_manager.get_config = AsyncMock(
            side_effect=Exception("Database error")
        )

        # Make request
        response = client.get("/api/v1/config/filters/strategy/momentum_strategy")

        # Assert response
        assert (
            response.status_code == 200
        )  # API catches exceptions and returns error response
        data = response.json()
        assert data["success"] is False
        assert "error" in data
        assert "INTERNAL_ERROR" in str(data["error"])

    def test_update_strategy_filters_success(self, client, mock_config_manager):
        """Test successful update of strategy filters."""
        # Setup mock
        mock_config_manager.mongodb_client.upsert_strategy_config = AsyncMock(
            return_value=True
        )

        # Make request
        response = client.put(
            "/api/v1/config/filters/strategy/momentum_strategy",
            json={
                "filters": {
                    "tp_distance_min_pct": 2.0,
                    "tp_distance_max_pct": 15.0,
                    "enabled_sides": ["LONG"],
                },
                "changed_by": "admin",
                "reason": "Momentum strategy prefers longer TP distances",
            },
        )

        # Assert response
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "config" in data["data"]
        assert "metadata" in data
        assert "updated successfully" in data["metadata"]["message"]

        # Verify config manager methods were called
        mock_config_manager.mongodb_client.upsert_strategy_config.assert_called_once()
        mock_config_manager.invalidate_cache.assert_called_once_with(
            strategy_id="momentum_strategy"
        )

    def test_update_strategy_filters_no_db_client(self, client, mock_config_manager):
        """Test error handling when no database client is configured."""
        # Setup mock without mongodb_client
        mock_config_manager.mongodb_client = None

        # Make request
        response = client.put(
            "/api/v1/config/filters/strategy/momentum_strategy",
            json={
                "filters": {"tp_distance_min_pct": 2.0},
                "changed_by": "admin",
                "reason": "Test",
            },
        )

        # Assert response
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert data["error"]["code"] == "NO_DB"

    def test_update_strategy_filters_upsert_failure(self, client, mock_config_manager):
        """Test error handling when upsert fails."""
        # Setup mock
        mock_config_manager.mongodb_client.upsert_strategy_config = AsyncMock(
            return_value=False
        )

        # Make request
        response = client.put(
            "/api/v1/config/filters/strategy/momentum_strategy",
            json={
                "filters": {"tp_distance_min_pct": 2.0},
                "changed_by": "admin",
                "reason": "Test",
            },
        )

        # Assert response
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert data["error"]["code"] == "UPDATE_FAILED"

    def test_update_strategy_filters_exception_handling(
        self, client, mock_config_manager
    ):
        """Test error handling when exception occurs during update."""
        # Setup mock to raise exception
        mock_config_manager.mongodb_client.upsert_strategy_config = AsyncMock(
            side_effect=Exception("Database error")
        )

        # Make request
        response = client.put(
            "/api/v1/config/filters/strategy/momentum_strategy",
            json={
                "filters": {"tp_distance_min_pct": 2.0},
                "changed_by": "admin",
                "reason": "Test",
            },
        )

        # Assert response
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "error" in data
        assert "INTERNAL_ERROR" in str(data["error"])
