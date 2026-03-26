import sys
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from shared.constants import UTC

# Mock OpenTelemetry and other non-essential modules
sys.modules["opentelemetry.instrumentation.logging"] = MagicMock()
sys.modules["otel_init"] = MagicMock()
sys.modules["profiler_init"] = MagicMock()

# Mock binance to avoid initialization errors
sys.modules["binance"] = MagicMock()
sys.modules["binance.enums"] = MagicMock()
sys.modules["binance.exceptions"] = MagicMock()

from tradeengine.api import app  # noqa: E402


@pytest.fixture
def client():
    return TestClient(app)


class TestStateEndpoint:
    """Tests for the /state endpoint."""

    def test_get_state_success(self, client):
        """Test successful CIO state retrieval."""
        mock_state = {
            "portfolio": {
                "gross_exposure": 0.5,
                "same_asset_pct": 0.1,
                "open_positions_count": 2,
            },
            "risk_limits": {
                "max_drawdown_pct": 0.15,
                "max_orders_global": 50,
                "max_orders_per_symbol": 5,
                "max_position_size_usd": 5000.0,
            },
            "env_stats": {
                "global_drawdown_pct": 0.02,
                "open_orders_global": 10,
                "open_orders_symbol": 1,
                "available_capital_usd": 25000.0,
            },
        }

        with patch("tradeengine.api.dispatcher.get_cio_state", return_value=mock_state):
            response = client.get("/state?symbol=BTCUSDT")
            assert response.status_code == 200
            data = response.json()
            assert data["portfolio"]["gross_exposure"] == 0.5
            assert data["risk_limits"]["max_orders_global"] == 50
            assert data["env_stats"]["open_orders_symbol"] == 1

    def test_get_state_missing_symbol(self, client):
        """Test state retrieval without symbol parameter."""
        response = client.get("/state")
        assert (
            response.status_code == 422
        )  # Validation error for missing required Query param

    def test_get_state_internal_error(self, client):
        """Test state retrieval handling dispatcher error."""
        with patch(
            "tradeengine.api.dispatcher.get_cio_state",
            side_effect=Exception("Dispatcher failure"),
        ):
            response = client.get("/state?symbol=BTCUSDT")
            assert response.status_code == 500
            assert "Failed to retrieve engine state" in response.json()["detail"]
