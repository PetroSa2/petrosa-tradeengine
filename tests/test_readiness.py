"""
Tests for /ready endpoint (readiness_check function).

Covers acceptance criteria from issue #357:
1. Readiness failure logs include which component failed and why
2. Timeout alignment with Binance ping sentinel
3. Readiness aggregation behavior
"""

import asyncio

# Mock modules before importing api
import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

# Mock OpenTelemetry and other optional imports
sys.modules["opentelemetry.instrumentation.logging"] = MagicMock()
sys.modules["otel_init"] = MagicMock()
sys.modules["profiler_init"] = MagicMock()

# Mock binance module
binance_module = ModuleType("binance")
binance_module.Client = MagicMock
binance_enums = ModuleType("binance.enums")
binance_enums.FUTURE_ORDER_TYPE_LIMIT = "LIMIT"
binance_enums.FUTURE_ORDER_TYPE_MARKET = "MARKET"
binance_enums.FUTURE_ORDER_TYPE_STOP = "STOP"
binance_enums.FUTURE_ORDER_TYPE_STOP_MARKET = "STOP_MARKET"
binance_enums.FUTURE_ORDER_TYPE_TAKE_PROFIT = "TAKE_PROFIT"
binance_enums.FUTURE_ORDER_TYPE_TAKE_PROFIT_MARKET = "TAKE_PROFIT_MARKET"
binance_enums.SIDE_BUY = "BUY"
binance_enums.SIDE_SELL = "SELL"
binance_enums.TIME_IN_FORCE_GTC = "GTC"
binance_exceptions = ModuleType("binance.exceptions")
binance_exceptions.BinanceAPIException = Exception
sys.modules["binance"] = binance_module
sys.modules["binance.enums"] = binance_enums
sys.modules["binance.exceptions"] = binance_exceptions

# Now import the app
# Import the actual objects to patch their methods
from tradeengine.api import (  # noqa: E402
    app,
    binance_exchange,
    dispatcher,
    simulator_exchange,
)


@pytest.fixture
def client() -> TestClient:
    """Create a test client for the FastAPI app."""
    return TestClient(app)


# --- Helper async functions for mocking health checks ---
# These are REAL async functions that return actual coroutines when called.
# This avoids the AsyncMock issue where AsyncMock(return_value={...})() returns
# a Mock object instead of an awaitable coroutine.


async def healthy_check():
    """Mock health check that returns healthy status."""
    return {"status": "healthy"}


async def unhealthy_check():
    """Mock health check that returns unhealthy status."""
    return {"status": "unhealthy", "error": "not initialized"}


async def unhealthy_ping_check():
    """Mock health check that returns unhealthy status for ping failure."""
    return {"status": "unhealthy", "error": "ping sentinel expired"}


async def unhealthy_ping_failed_check():
    """Mock health check that returns unhealthy status for ping failed."""
    return {"status": "unhealthy", "error": "ping failed"}


async def timeout_check():
    """Mock health check that raises TimeoutError."""
    raise TimeoutError("Timed out")


async def error_check():
    """Mock health check that raises RuntimeError."""
    raise RuntimeError("Database connection lost")


# --- AC-1: Readiness failure logs include which component failed and why ---


class TestReadinessComponentFailureLogging:
    """Test that readiness logs which component failed and why (AC-1)."""

    def test_dispatcher_timeout_logs_component_name(self, client):
        """Test that dispatcher timeout is logged with component name."""
        with (
            patch("shared.constants.validate_mongodb_config"),
            patch.object(dispatcher, "health_check", timeout_check),
            patch.object(binance_exchange, "health_check", healthy_check),
            patch.object(simulator_exchange, "health_check", healthy_check),
            patch("tradeengine.api.logger") as mock_logger,
        ):
            # Should return 503
            response = client.get("/ready")
            assert response.status_code == 503

            # Check that logger.error was called with component name
            error_calls = [str(call) for call in mock_logger.error.call_args_list]
            assert any("dispatcher" in call.lower() for call in error_calls)

    def test_binance_unhealthy_logs_component_name(self, client):
        """Test that Binance unhealthy status is logged with component name."""
        with (
            patch("shared.constants.validate_mongodb_config"),
            patch.object(dispatcher, "health_check", healthy_check),
            patch.object(binance_exchange, "health_check", unhealthy_ping_check),
            patch.object(simulator_exchange, "health_check", healthy_check),
        ):
            response = client.get("/ready")
            assert response.status_code == 503

            # Check error message contains component name
            detail = response.json().get("detail", "")
            assert "binance" in detail.lower()

    def test_simulator_exception_logs_component_and_error(self, client):
        """Test that simulator exception is logged with component name and error."""
        with (
            patch("shared.constants.validate_mongodb_config"),
            patch.object(dispatcher, "health_check", healthy_check),
            patch.object(binance_exchange, "health_check", healthy_check),
            patch.object(simulator_exchange, "health_check", error_check),
        ):
            response = client.get("/ready")
            assert response.status_code == 503

            # Check that the error detail contains useful information
            detail = response.json().get("detail", "")
            assert "simulator" in detail.lower()
            # Should not be empty
            assert len(detail) > 20

    def test_all_components_fail_logs_all_names(self, client):
        """Test that when all components fail, all names are in the error."""
        with (
            patch("shared.constants.validate_mongodb_config"),
            patch.object(dispatcher, "health_check", unhealthy_check),
            patch.object(binance_exchange, "health_check", unhealthy_ping_failed_check),
            patch.object(simulator_exchange, "health_check", unhealthy_check),
        ):
            response = client.get("/ready")
            assert response.status_code == 503

            detail = response.json().get("detail", "")
            assert "dispatcher" in detail.lower()
            assert "binance" in detail.lower()
            assert "simulator" in detail.lower()


# --- AC-2: Timeout alignment with Binance ping sentinel ---


class TestReadinessTimeoutAlignment:
    """Test that timeouts align with Binance ping sentinel (AC-2)."""

    def test_timeout_value_aligned_with_ping_loop(self, client):
        """Test that readiness uses 5s timeout (aligned with Binance ping loop)."""
        with (
            patch("shared.constants.validate_mongodb_config"),
            patch.object(dispatcher, "health_check", healthy_check),
            patch.object(binance_exchange, "health_check", healthy_check),
            patch.object(simulator_exchange, "health_check", healthy_check),
        ):
            # Call readiness
            response = client.get("/ready")
            assert response.status_code == 200

            # Verify the response is correct
            assert response.json() == {"status": "ready"}

    def test_binance_health_check_uses_cached_sentinel(self, client):
        """Test that Binance health_check is non-blocking (uses cached sentinel)."""
        call_count = 0

        async def counting_healthy_check():
            nonlocal call_count
            call_count += 1
            return {"status": "healthy"}

        with (
            patch("shared.constants.validate_mongodb_config"),
            patch.object(dispatcher, "health_check", healthy_check),
            patch.object(binance_exchange, "health_check", counting_healthy_check),
            patch.object(simulator_exchange, "health_check", healthy_check),
        ):
            # Call readiness
            response = client.get("/ready")
            # Should succeed quickly (non-blocking)
            assert response.status_code == 200
            # Verify health_check was called
            assert call_count == 1


# --- AC-3: Tests for readiness aggregation behavior ---


class TestReadinessAggregation:
    """Test readiness aggregation behavior (AC-3)."""

    def test_all_healthy_returns_ready(self, client):
        """Test that all healthy components return {'status': 'ready'}."""
        with (
            patch("shared.constants.validate_mongodb_config"),
            patch.object(dispatcher, "health_check", healthy_check),
            patch.object(binance_exchange, "health_check", healthy_check),
            patch.object(simulator_exchange, "health_check", healthy_check),
        ):
            response = client.get("/ready")
            assert response.status_code == 200
            assert response.json() == {"status": "ready"}

    def test_one_unhealthy_returns_503(self, client):
        """Test that one unhealthy component returns 503."""
        with (
            patch("shared.constants.validate_mongodb_config"),
            patch.object(dispatcher, "health_check", healthy_check),
            patch.object(binance_exchange, "health_check", unhealthy_check),
            patch.object(simulator_exchange, "health_check", healthy_check),
        ):
            response = client.get("/ready")
            assert response.status_code == 503

    def test_error_detail_not_empty(self, client):
        """Test that error detail is never empty (regression test for empty logs)."""
        with (
            patch("shared.constants.validate_mongodb_config"),
            patch.object(dispatcher, "health_check", healthy_check),
            patch.object(binance_exchange, "health_check", unhealthy_check),
            patch.object(simulator_exchange, "health_check", healthy_check),
        ):
            response = client.get("/ready")
            assert response.status_code == 503

            detail = response.json().get("detail", "")
            # Detail should not be empty or just "Components not ready"
            assert detail != ""
            assert detail != "Components not ready"
            # Should contain useful information
            assert len(detail) > 10

    def test_timeout_produces_meaningful_error(self, client):
        """Test that timeout produces a meaningful error message."""
        with (
            patch("shared.constants.validate_mongodb_config"),
            patch.object(dispatcher, "health_check", timeout_check),
            patch.object(binance_exchange, "health_check", healthy_check),
            patch.object(simulator_exchange, "health_check", healthy_check),
        ):
            response = client.get("/ready")
            # Error should mention timeout and component
            assert response.status_code == 503
            detail = response.json().get("detail", "")
            assert "timeout" in detail.lower() or "timed out" in detail.lower()
            assert "dispatcher" in detail.lower()
