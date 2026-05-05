"""
Tests for readiness endpoint (issue #357).

Covers:
- AC-1: Readiness failure logs include which component failed and why
- AC-2: Timeout aligned with Binance ping sentinel (_PING_TTL=30s)
- AC-3: Readiness aggregation behavior with multiple components
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from tradeengine.api import app


@pytest.fixture
def client():
    """Create test client for the FastAPI app."""
    return TestClient(app)


@pytest.fixture
def mock_health_checks():
    """Mock all three health check functions."""
    with (
        patch("tradeengine.api.dispatcher") as mock_dispatcher,
        patch("tradeengine.api.binance_exchange") as mock_binance,
        patch("tradeengine.api.simulator_exchange") as mock_simulator,
    ):
        # Setup default healthy responses
        mock_dispatcher.health_check = AsyncMock(return_value={"status": "healthy"})
        mock_binance.health_check = AsyncMock(return_value={"status": "healthy"})
        mock_simulator.health_check = AsyncMock(return_value={"status": "healthy"})

        yield {
            "dispatcher": mock_dispatcher,
            "binance": mock_binance,
            "simulator": mock_simulator,
        }


class TestReadinessComponentFailureLogging:
    """AC-1: Readiness failure logs include which component failed and why."""

    def test_dispatcher_failure_includes_component_name(
        self, client, mock_health_checks
    ):
        """Test that dispatcher failure includes component name in error."""
        mock_health_checks["dispatcher"].health_check = AsyncMock(
            return_value={"status": "unhealthy", "error": "Database connection failed"}
        )

        response = client.get("/ready")
        assert response.status_code == 503
        assert "dispatcher" in response.json()["detail"].lower()
        assert "database connection failed" in response.json()["detail"].lower()

    def test_binance_failure_includes_error_detail(self, client, mock_health_checks):
        """Test that binance failure includes error details."""
        mock_health_checks["binance"].health_check = AsyncMock(
            return_value={"status": "error", "message": "API rate limit exceeded"}
        )

        response = client.get("/ready")
        assert response.status_code == 503
        assert "binance" in response.json()["detail"].lower()
        assert "rate limit" in response.json()["detail"].lower()

    def test_simulator_failure_logs_component(self, client, mock_health_checks):
        """Test that simulator failure is logged with component name."""
        mock_health_checks["simulator"].health_check = AsyncMock(
            side_effect=Exception("Simulator not initialized")
        )

        response = client.get("/ready")
        assert response.status_code == 503
        assert "simulator" in response.json()["detail"].lower()
        assert "not initialized" in response.json()["detail"].lower()

    def test_timeout_includes_component_name(self, client, mock_health_checks):
        """Test that timeout error includes which component timed out."""

        async def slow_response():
            await asyncio.sleep(10)  # Longer than timeout
            return {"status": "healthy"}

        mock_health_checks["dispatcher"].health_check = slow_response

        response = client.get("/ready")
        assert response.status_code == 503
        assert "dispatcher" in response.json()["detail"].lower()
        assert "timed out" in response.json()["detail"].lower()


class TestReadinessTimeoutAlignment:
    """AC-2: Evaluate and align 3s timeout with Binance ping sentinel (_PING_TTL=30s)."""

    def test_timeout_value_is_aligned_with_ping_sentinel(self, client):
        """Test that timeout is set to 5.0s (aligned with _PING_TTL=30s)."""
        import inspect

        from tradeengine.api import readiness_check

        source = inspect.getsource(readiness_check)
        assert "_TIMEOUT = 5.0" in source

    def test_timeout_allows_for_network_latency(self, client, mock_health_checks):
        """Test that 5.0s timeout is reasonable for Binance API calls."""
        # The timeout should be > 3s (previous value) but << 30s (_PING_TTL)
        # This is validated by checking the source code
        import inspect

        from tradeengine.api import readiness_check

        source = inspect.getsource(readiness_check)
        assert "5.0" in source
        # Ensure we're not using the old 3.0 value
        lines = source.split("\n")
        for line in lines:
            if "_TIMEOUT" in line and "5.0" not in line:
                pytest.fail(f"Found unexpected timeout value: {line}")

    @pytest.mark.asyncio
    async def test_all_components_timeout_within_bounds(self, mock_health_checks):
        """Test that all components are checked with the same timeout."""
        import asyncio

        from tradeengine.api import readiness_check

        # Mock slow responses
        async def slow_response():
            await asyncio.sleep(10)
            return {"status": "healthy"}

        mock_health_checks["dispatcher"].health_check = slow_response
        mock_health_checks["binance"].health_check = slow_response
        mock_health_checks["simulator"].health_check = slow_response

        # All should timeout at 5.0s
        start = asyncio.get_event_loop().time()
        try:
            await readiness_check()
        except HTTPException as e:
            elapsed = asyncio.get_event_loop().time() - start
            # Should timeout around 5s, not 15s (3 separate 5s timeouts)
            assert elapsed < 10.0, f"Timeout took {elapsed}s, expected ~5s"


class TestReadinessAggregation:
    """AC-3: Readiness aggregation returns detailed component status."""

    def test_all_healthy_returns_ready(self, client, mock_health_checks):
        """Test that all healthy components return ready status."""
        response = client.get("/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ready"
        assert "components" in data
        assert data["components"]["dispatcher"]["status"] == "healthy"
        assert data["components"]["binance"]["status"] == "healthy"
        assert data["components"]["simulator"]["status"] == "healthy"

    def test_partial_failure_returns_503(self, client, mock_health_checks):
        """Test that partial failure returns 503 with details."""
        mock_health_checks["binance"].health_check = AsyncMock(
            return_value={"status": "unhealthy"}
        )

        response = client.get("/ready")
        assert response.status_code == 503
        assert "binance" in response.json()["detail"].lower()

    def test_multiple_failures_all_reported(self, client, mock_health_checks):
        """Test that multiple component failures are all reported."""
        mock_health_checks["dispatcher"].health_check = AsyncMock(
            return_value={"status": "unhealthy", "error": "DB error"}
        )
        mock_health_checks["simulator"].health_check = AsyncMock(
            side_effect=Exception("Simulator crashed")
        )

        response = client.get("/ready")
        assert response.status_code == 503
        detail = response.json()["detail"]
        assert "dispatcher" in detail.lower()
        assert "simulator" in detail.lower()
        assert "db error" in detail.lower()
        assert "crashed" in detail.lower()

    def test_response_includes_all_components_even_if_healthy(
        self, client, mock_health_checks
    ):
        """Test that response includes all component statuses when ready."""
        response = client.get("/ready")
        assert response.status_code == 200
        data = response.json()
        components = data["components"]

        # All three components should be present
        assert "dispatcher" in components
        assert "binance" in components
        assert "simulator" in components

        # Each should have a status
        for name, status in components.items():
            assert "status" in status
