"""
Tests for configuration validation API endpoint.

Tests the /api/v1/config/validate endpoint including:
- Parameter validation
- Error format standardization
- Impact assessment
- Risk level calculation
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from tradeengine.api import app
from tradeengine.api_config_routes import (
    CrossServiceConflict,
    ValidationError,
    ValidationResponse,
)
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


class TestConfigValidationEndpoint:
    """Test /api/v1/config/validate endpoint."""

    @patch("tradeengine.api_config_routes.get_config_manager")
    @patch("tradeengine.api_config_routes.detect_cross_service_conflicts")
    def test_validate_config_success_global(
        self, mock_detect_conflicts, mock_get_manager, client, mock_config_manager
    ):
        """Test successful validation for global config."""
        mock_get_manager.return_value = mock_config_manager
        mock_config_manager.set_config = AsyncMock(
            return_value=(True, None, [])  # success, config, errors
        )
        mock_detect_conflicts.return_value = []  # No conflicts

        response = client.post(
            "/api/v1/config/validate",
            json={
                "parameters": {
                    "leverage": 10,
                    "stop_loss_pct": 2.0,
                }
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["validation_passed"] is True
        assert len(data["data"]["errors"]) == 0
        assert data["data"]["estimated_impact"]["risk_level"] == "medium"
        assert data["data"]["estimated_impact"]["affected_scope"] == "global"
        assert data["metadata"]["validation_mode"] == "dry_run"

    @patch("tradeengine.api_config_routes.get_config_manager")
    @patch("tradeengine.api_config_routes.detect_cross_service_conflicts")
    def test_validate_config_success_symbol(
        self, mock_detect_conflicts, mock_get_manager, client, mock_config_manager
    ):
        """Test successful validation for symbol-specific config."""
        mock_get_manager.return_value = mock_config_manager
        mock_config_manager.set_config = AsyncMock(return_value=(True, None, []))
        mock_detect_conflicts.return_value = []

        response = client.post(
            "/api/v1/config/validate",
            json={
                "parameters": {"leverage": 15},
                "symbol": "BTCUSDT",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["validation_passed"] is True
        assert data["data"]["estimated_impact"]["affected_scope"] == "symbol:BTCUSDT"
        assert data["metadata"]["scope"] == "BTCUSDT:all"

    @patch("tradeengine.api_config_routes.get_config_manager")
    @patch("tradeengine.api_config_routes.detect_cross_service_conflicts")
    def test_validate_config_success_symbol_side(
        self, mock_detect_conflicts, mock_get_manager, client, mock_config_manager
    ):
        """Test successful validation for symbol-side-specific config."""
        mock_get_manager.return_value = mock_config_manager
        mock_config_manager.set_config = AsyncMock(return_value=(True, None, []))
        mock_detect_conflicts.return_value = []

        response = client.post(
            "/api/v1/config/validate",
            json={
                "parameters": {"leverage": 20},
                "symbol": "BTCUSDT",
                "side": "LONG",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["validation_passed"] is True
        assert data["metadata"]["scope"] == "BTCUSDT:LONG"

    @patch("tradeengine.api_config_routes.get_config_manager")
    @patch("tradeengine.api_config_routes.detect_cross_service_conflicts")
    def test_validate_config_invalid_type_error(
        self, mock_detect_conflicts, mock_get_manager, client, mock_config_manager
    ):
        """Test validation with invalid type error."""
        mock_get_manager.return_value = mock_config_manager
        mock_config_manager.set_config = AsyncMock(
            return_value=(
                False,
                None,
                ["leverage must be integer, got <class 'str'>"],
            )
        )
        mock_detect_conflicts.return_value = []

        response = client.post(
            "/api/v1/config/validate",
            json={"parameters": {"leverage": "invalid"}},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["validation_passed"] is False
        assert len(data["data"]["errors"]) == 1
        assert data["data"]["errors"][0]["field"] == "leverage"
        assert data["data"]["errors"][0]["code"] == "INVALID_TYPE"
        assert "Change leverage to an integer value" in data["data"]["suggested_fixes"]

    @patch("tradeengine.api_config_routes.get_config_manager")
    @patch("tradeengine.api_config_routes.detect_cross_service_conflicts")
    def test_validate_config_out_of_range_error(
        self, mock_detect_conflicts, mock_get_manager, client, mock_config_manager
    ):
        """Test validation with out of range error."""
        mock_get_manager.return_value = mock_config_manager
        mock_config_manager.set_config = AsyncMock(
            return_value=(
                False,
                None,
                ["leverage must be >= 1, got 0"],
            )
        )
        mock_detect_conflicts.return_value = []

        response = client.post(
            "/api/v1/config/validate",
            json={"parameters": {"leverage": 0}},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["validation_passed"] is False
        assert len(data["data"]["errors"]) == 1
        assert data["data"]["errors"][0]["field"] == "leverage"
        assert data["data"]["errors"][0]["code"] == "OUT_OF_RANGE"
        assert data["data"]["errors"][0]["suggested_value"] is not None

    @patch("tradeengine.api_config_routes.get_config_manager")
    @patch("tradeengine.api_config_routes.detect_cross_service_conflicts")
    def test_validate_config_invalid_value_error(
        self, mock_detect_conflicts, mock_get_manager, client, mock_config_manager
    ):
        """Test validation with invalid value (not in allowed list)."""
        mock_get_manager.return_value = mock_config_manager
        mock_config_manager.set_config = AsyncMock(
            return_value=(
                False,
                None,
                ["side must be one of ['LONG', 'SHORT'], got INVALID"],
            )
        )
        mock_detect_conflicts.return_value = []

        response = client.post(
            "/api/v1/config/validate",
            json={"parameters": {"side": "INVALID"}},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["validation_passed"] is False
        assert len(data["data"]["errors"]) == 1
        assert data["data"]["errors"][0]["code"] == "INVALID_VALUE"
        assert any("Use one of:" in fix for fix in data["data"]["suggested_fixes"])

    @patch("tradeengine.api_config_routes.get_config_manager")
    @patch("tradeengine.api_config_routes.detect_cross_service_conflicts")
    def test_validate_config_unknown_parameter_error(
        self, mock_detect_conflicts, mock_get_manager, client, mock_config_manager
    ):
        """Test validation with unknown parameter error."""
        mock_get_manager.return_value = mock_config_manager
        mock_config_manager.set_config = AsyncMock(
            return_value=(False, None, ["Unknown parameter: invalid_param"])
        )
        mock_detect_conflicts.return_value = []

        response = client.post(
            "/api/v1/config/validate",
            json={"parameters": {"invalid_param": 123}},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["validation_passed"] is False
        assert len(data["data"]["errors"]) == 1
        assert data["data"]["errors"][0]["field"] == "invalid_param"
        assert data["data"]["errors"][0]["code"] == "UNKNOWN_PARAMETER"
        assert any(
            "Remove invalid_param" in fix for fix in data["data"]["suggested_fixes"]
        )

    @patch("tradeengine.api_config_routes.get_config_manager")
    @patch("tradeengine.api_config_routes.detect_cross_service_conflicts")
    def test_validate_config_generic_error(
        self, mock_detect_conflicts, mock_get_manager, client, mock_config_manager
    ):
        """Test validation with generic error message."""
        mock_get_manager.return_value = mock_config_manager
        mock_config_manager.set_config = AsyncMock(
            return_value=(False, None, ["Some generic error message"])
        )
        mock_detect_conflicts.return_value = []

        response = client.post(
            "/api/v1/config/validate",
            json={"parameters": {"leverage": 10}},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["validation_passed"] is False
        assert len(data["data"]["errors"]) == 1
        assert data["data"]["errors"][0]["field"] == "unknown"
        assert data["data"]["errors"][0]["code"] == "VALIDATION_ERROR"

    @patch("tradeengine.api_config_routes.get_config_manager")
    @patch("tradeengine.api_config_routes.detect_cross_service_conflicts")
    def test_validate_config_high_risk_leverage(
        self, mock_detect_conflicts, mock_get_manager, client, mock_config_manager
    ):
        """Test validation with high-risk leverage parameter."""
        mock_get_manager.return_value = mock_config_manager
        mock_config_manager.set_config = AsyncMock(return_value=(True, None, []))
        mock_detect_conflicts.return_value = []

        response = client.post(
            "/api/v1/config/validate",
            json={"parameters": {"leverage": 100}},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["estimated_impact"]["risk_level"] == "high"
        assert "warning" in data["data"]["estimated_impact"]
        assert "High leverage" in data["data"]["estimated_impact"]["warning"]

    @patch("tradeengine.api_config_routes.get_config_manager")
    @patch("tradeengine.api_config_routes.detect_cross_service_conflicts")
    def test_validate_config_medium_risk_params(
        self, mock_detect_conflicts, mock_get_manager, client, mock_config_manager
    ):
        """Test validation with medium-risk parameters."""
        mock_get_manager.return_value = mock_config_manager
        mock_config_manager.set_config = AsyncMock(return_value=(True, None, []))
        mock_detect_conflicts.return_value = []

        response = client.post(
            "/api/v1/config/validate",
            json={
                "parameters": {
                    "stop_loss_pct": 1.5,
                }
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["estimated_impact"]["risk_level"] == "medium"

    @patch("tradeengine.api_config_routes.get_config_manager")
    @patch("tradeengine.api_config_routes.detect_cross_service_conflicts")
    def test_validate_config_low_risk_params(
        self, mock_detect_conflicts, mock_get_manager, client, mock_config_manager
    ):
        """Test validation with low-risk parameters."""
        mock_get_manager.return_value = mock_config_manager
        mock_config_manager.set_config = AsyncMock(return_value=(True, None, []))
        mock_detect_conflicts.return_value = []

        response = client.post(
            "/api/v1/config/validate",
            json={
                "parameters": {
                    "some_other_param": "value",
                }
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["estimated_impact"]["risk_level"] == "low"

    @patch("tradeengine.api_config_routes.get_config_manager")
    @patch("tradeengine.api_config_routes.detect_cross_service_conflicts")
    def test_validate_config_multiple_errors(
        self, mock_detect_conflicts, mock_get_manager, client, mock_config_manager
    ):
        """Test validation with multiple errors."""
        mock_get_manager.return_value = mock_config_manager
        mock_config_manager.set_config = AsyncMock(
            return_value=(
                False,
                None,
                [
                    "leverage must be integer, got <class 'str'>",
                    "stop_loss_pct must be >= 0, got -1",
                ],
            )
        )
        mock_detect_conflicts.return_value = []

        response = client.post(
            "/api/v1/config/validate",
            json={
                "parameters": {
                    "leverage": "invalid",
                    "stop_loss_pct": -1,
                }
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["validation_passed"] is False
        assert len(data["data"]["errors"]) == 2
        # At least one suggested fix should be present (for INVALID_TYPE)
        assert len(data["data"]["suggested_fixes"]) >= 1
        # Check that both errors are present
        error_fields = [err["field"] for err in data["data"]["errors"]]
        assert "leverage" in error_fields
        assert "stop_loss_pct" in error_fields

    @patch("tradeengine.api_config_routes.get_config_manager")
    @patch("tradeengine.api_config_routes.detect_cross_service_conflicts")
    def test_validate_config_exception_handling(
        self, mock_detect_conflicts, mock_get_manager, client, mock_config_manager
    ):
        """Test exception handling in validation endpoint."""
        mock_get_manager.return_value = mock_config_manager
        mock_config_manager.set_config = AsyncMock(
            side_effect=Exception("Database error")
        )
        mock_detect_conflicts.return_value = []

        response = client.post(
            "/api/v1/config/validate",
            json={"parameters": {"leverage": 10}},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert data["error"]["code"] == "INTERNAL_ERROR"

    @patch("tradeengine.api_config_routes.get_config_manager")
    @patch("tradeengine.api_config_routes.detect_cross_service_conflicts")
    def test_validate_config_suggested_value_from_schema(
        self, mock_detect_conflicts, mock_get_manager, client, mock_config_manager
    ):
        """Test that suggested values are extracted from parameter schema."""
        mock_get_manager.return_value = mock_config_manager
        mock_config_manager.set_config = AsyncMock(
            return_value=(False, None, ["leverage must be >= 1, got 0"])
        )
        mock_detect_conflicts.return_value = []

        response = client.post(
            "/api/v1/config/validate",
            json={"parameters": {"leverage": 0}},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["validation_passed"] is False
        # Suggested value should be calculated from schema
        error = data["data"]["errors"][0]
        assert error["code"] == "OUT_OF_RANGE"
        # Suggested value should be a number (from schema min/max/default)
        assert error["suggested_value"] is not None
        assert isinstance(error["suggested_value"], (int, float))


class TestValidationModels:
    """Test validation model classes."""

    def test_validation_error_model(self):
        """Test ValidationError model."""
        error = ValidationError(
            field="leverage",
            message="leverage must be >= 1",
            code="OUT_OF_RANGE",
            suggested_value=10,
        )
        assert error.field == "leverage"
        assert error.code == "OUT_OF_RANGE"
        assert error.suggested_value == 10

    def test_cross_service_conflict_model(self):
        """Test CrossServiceConflict model."""
        conflict = CrossServiceConflict(
            service="data-manager",
            conflict_type="PARAMETER_CONFLICT",
            description="Conflicting configuration",
            resolution="Update both services",
        )
        assert conflict.service == "data-manager"
        assert conflict.conflict_type == "PARAMETER_CONFLICT"

    def test_validation_response_model(self):
        """Test ValidationResponse model."""
        response = ValidationResponse(
            validation_passed=True,
            errors=[],
            warnings=[],
            suggested_fixes=[],
            estimated_impact={"risk_level": "low"},
            conflicts=[],
        )
        assert response.validation_passed is True
        assert len(response.errors) == 0
        assert response.estimated_impact["risk_level"] == "low"

    def test_validation_response_with_errors(self):
        """Test ValidationResponse with errors."""
        errors = [
            ValidationError(
                field="leverage",
                message="Invalid value",
                code="INVALID_TYPE",
                suggested_value=None,
            )
        ]
        response = ValidationResponse(
            validation_passed=False,
            errors=errors,
            warnings=["Warning message"],
            suggested_fixes=["Fix 1", "Fix 2"],
            estimated_impact={"risk_level": "high"},
            conflicts=[],
        )
        assert response.validation_passed is False
        assert len(response.errors) == 1
        assert len(response.warnings) == 1
        assert len(response.suggested_fixes) == 2


class TestCrossServiceConflictDetection:
    """Test cross-service conflict detection function."""

    @pytest.mark.asyncio
    async def test_detect_conflicts_no_conflicts(self):
        """Test conflict detection when no conflicts exist."""
        from unittest.mock import AsyncMock, patch

        from tradeengine.api_config_routes import detect_cross_service_conflicts

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Mock successful responses with no conflicts
            mock_client.get = AsyncMock(
                return_value=AsyncMock(
                    status_code=200,
                    json=lambda: {
                        "success": True,
                        "data": {"max_positions": 10},
                    },
                )
            )
            mock_client.post = AsyncMock(
                return_value=AsyncMock(
                    status_code=200,
                    json=lambda: {
                        "success": True,
                        "data": {"validation_passed": True},
                    },
                )
            )

            conflicts = await detect_cross_service_conflicts(
                {"max_position_size": 10, "leverage": 5}
            )

            assert len(conflicts) == 0

    @pytest.mark.asyncio
    async def test_detect_conflicts_data_manager_position_mismatch(self):
        """Test conflict detection when data-manager has position limit mismatch."""
        from unittest.mock import AsyncMock, patch

        from tradeengine.api_config_routes import detect_cross_service_conflicts

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Mock data-manager response with different position limit
            mock_client.get = AsyncMock(
                return_value=AsyncMock(
                    status_code=200,
                    json=lambda: {
                        "success": True,
                        "data": {"max_positions": 5},  # Different from proposed 10
                    },
                )
            )
            mock_client.post = AsyncMock(
                return_value=AsyncMock(
                    status_code=200,
                    json=lambda: {
                        "success": True,
                        "data": {"validation_passed": True},
                    },
                )
            )

            conflicts = await detect_cross_service_conflicts(
                {"max_position_size": 10}  # 10 vs 5 = 50% difference > 20% threshold
            )

            assert len(conflicts) == 1
            assert conflicts[0].service == "data-manager"
            assert conflicts[0].conflict_type == "PARAMETER_CONFLICT"

    @pytest.mark.asyncio
    async def test_detect_conflicts_ta_bot_validation_error(self):
        """Test conflict detection when ta-bot reports validation errors."""
        from unittest.mock import AsyncMock, patch

        from tradeengine.api_config_routes import detect_cross_service_conflicts

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Mock data-manager response (no conflict)
            mock_client.get = AsyncMock(
                return_value=AsyncMock(
                    status_code=200,
                    json=lambda: {
                        "success": True,
                        "data": {"max_positions": 10},
                    },
                )
            )

            # Mock ta-bot response with validation errors
            mock_client.post = AsyncMock(
                side_effect=[
                    # First call for ta-bot
                    AsyncMock(
                        status_code=200,
                        json=lambda: {
                            "success": True,
                            "data": {
                                "validation_passed": False,
                                "errors": [
                                    {"message": "Leverage too high"},
                                    {"message": "Invalid stop loss"},
                                ],
                            },
                        },
                    ),
                    # Second call for realtime-strategies (no conflict)
                    AsyncMock(
                        status_code=200,
                        json=lambda: {
                            "success": True,
                            "data": {"validation_passed": True},
                        },
                    ),
                ]
            )

            conflicts = await detect_cross_service_conflicts(
                {"leverage": 100, "stop_loss_pct": 0.5}
            )

            assert len(conflicts) == 1
            assert conflicts[0].service == "ta-bot"
            assert conflicts[0].conflict_type == "VALIDATION_CONFLICT"
            assert "Leverage too high" in conflicts[0].description

    @pytest.mark.asyncio
    async def test_detect_conflicts_timeout_handling(self):
        """Test conflict detection handles timeouts gracefully."""
        from unittest.mock import AsyncMock, patch

        import httpx

        from tradeengine.api_config_routes import detect_cross_service_conflicts

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Mock timeout exception
            mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))
            mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))

            conflicts = await detect_cross_service_conflicts(
                {"max_position_size": 10, "leverage": 5}
            )

            # Should return empty list on timeout (graceful degradation)
            assert len(conflicts) == 0

    @pytest.mark.asyncio
    async def test_detect_conflicts_exception_handling(self):
        """Test conflict detection handles exceptions gracefully."""
        from unittest.mock import AsyncMock, patch

        from tradeengine.api_config_routes import detect_cross_service_conflicts

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Mock exception
            mock_client.get = AsyncMock(side_effect=Exception("Connection error"))
            mock_client.post = AsyncMock(side_effect=Exception("Connection error"))

            conflicts = await detect_cross_service_conflicts(
                {"max_position_size": 10, "leverage": 5}
            )

            # Should return empty list on exception (graceful degradation)
            assert len(conflicts) == 0

    @pytest.mark.asyncio
    async def test_detect_conflicts_realtime_strategies_validation_error(self):
        """Test conflict detection when realtime-strategies reports validation errors."""
        from unittest.mock import AsyncMock, patch

        from tradeengine.api_config_routes import detect_cross_service_conflicts

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Mock data-manager response (no conflict)
            mock_client.get = AsyncMock(
                return_value=AsyncMock(
                    status_code=200,
                    json=lambda: {
                        "success": True,
                        "data": {"max_positions": 10},
                    },
                )
            )

            # Mock realtime-strategies response with validation errors
            mock_client.post = AsyncMock(
                side_effect=[
                    # First call for ta-bot (no conflict)
                    AsyncMock(
                        status_code=200,
                        json=lambda: {
                            "success": True,
                            "data": {"validation_passed": True},
                        },
                    ),
                    # Second call for realtime-strategies (has conflict)
                    AsyncMock(
                        status_code=200,
                        json=lambda: {
                            "success": True,
                            "data": {
                                "validation_passed": False,
                                "errors": [{"message": "Invalid take profit"}],
                            },
                        },
                    ),
                ]
            )

            conflicts = await detect_cross_service_conflicts({"take_profit_pct": 0.1})

            assert len(conflicts) == 1
            assert conflicts[0].service == "realtime-strategies"
            assert conflicts[0].conflict_type == "VALIDATION_CONFLICT"

    @pytest.mark.asyncio
    async def test_detect_conflicts_position_limit_within_threshold(self):
        """Test that position limit differences within threshold don't create conflicts."""
        from unittest.mock import AsyncMock, patch

        from tradeengine.api_config_routes import detect_cross_service_conflicts

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Mock data-manager response with position limit within 20% threshold
            # Proposed: 10, Current: 9 (10% difference < 20% threshold)
            mock_client.get = AsyncMock(
                return_value=AsyncMock(
                    status_code=200,
                    json=lambda: {
                        "success": True,
                        "data": {"max_positions": 9},
                    },
                )
            )
            mock_client.post = AsyncMock(
                return_value=AsyncMock(
                    status_code=200,
                    json=lambda: {
                        "success": True,
                        "data": {"validation_passed": True},
                    },
                )
            )

            conflicts = await detect_cross_service_conflicts({"max_position_size": 10})

            # Should not create conflict (difference is within threshold)
            assert len(conflicts) == 0

    @pytest.mark.asyncio
    async def test_detect_conflicts_with_symbol_and_side(self):
        """Test conflict detection with symbol and side parameters."""
        from unittest.mock import AsyncMock, patch

        from tradeengine.api_config_routes import detect_cross_service_conflicts

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_client.get = AsyncMock(
                return_value=AsyncMock(
                    status_code=200,
                    json=lambda: {
                        "success": True,
                        "data": {"max_positions": 10},
                    },
                )
            )
            mock_client.post = AsyncMock(
                return_value=AsyncMock(
                    status_code=200,
                    json=lambda: {
                        "success": True,
                        "data": {"validation_passed": True},
                    },
                )
            )

            conflicts = await detect_cross_service_conflicts(
                {"leverage": 5}, symbol="BTCUSDT", side="LONG"
            )

            # Should work with symbol and side parameters
            assert len(conflicts) == 0

    @pytest.mark.asyncio
    async def test_detect_conflicts_invalid_type_for_position_limit(self):
        """Test conflict detection handles invalid types for position limit comparison."""
        from unittest.mock import AsyncMock, patch

        from tradeengine.api_config_routes import detect_cross_service_conflicts

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Mock data-manager response with invalid type (string that can't be converted to float)
            mock_client.get = AsyncMock(
                return_value=AsyncMock(
                    status_code=200,
                    json=lambda: {
                        "success": True,
                        "data": {"max_positions": "invalid"},  # Invalid type
                    },
                )
            )
            mock_client.post = AsyncMock(
                return_value=AsyncMock(
                    status_code=200,
                    json=lambda: {
                        "success": True,
                        "data": {"validation_passed": True},
                    },
                )
            )

            conflicts = await detect_cross_service_conflicts({"max_position_size": 10})

            # Should handle invalid type gracefully (no conflict added)
            assert len(conflicts) == 0

    @pytest.mark.asyncio
    async def test_detect_conflicts_data_manager_non_200_response(self):
        """Test conflict detection handles non-200 responses from data-manager."""
        from unittest.mock import AsyncMock, patch

        from tradeengine.api_config_routes import detect_cross_service_conflicts

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Mock data-manager response with non-200 status
            mock_client.get = AsyncMock(
                return_value=AsyncMock(
                    status_code=404,  # Not found
                    json=lambda: {"success": False},
                )
            )
            mock_client.post = AsyncMock(
                return_value=AsyncMock(
                    status_code=200,
                    json=lambda: {
                        "success": True,
                        "data": {"validation_passed": True},
                    },
                )
            )

            conflicts = await detect_cross_service_conflicts({"max_position_size": 10})

            # Should handle non-200 response gracefully (no conflict added)
            assert len(conflicts) == 0

    @pytest.mark.asyncio
    async def test_detect_conflicts_data_manager_no_data_in_response(self):
        """Test conflict detection handles missing data in data-manager response."""
        from unittest.mock import AsyncMock, patch

        from tradeengine.api_config_routes import detect_cross_service_conflicts

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Mock data-manager response without data field
            mock_client.get = AsyncMock(
                return_value=AsyncMock(
                    status_code=200,
                    json=lambda: {
                        "success": True,
                        # Missing "data" field
                    },
                )
            )
            mock_client.post = AsyncMock(
                return_value=AsyncMock(
                    status_code=200,
                    json=lambda: {
                        "success": True,
                        "data": {"validation_passed": True},
                    },
                )
            )

            conflicts = await detect_cross_service_conflicts({"max_position_size": 10})

            # Should handle missing data gracefully (no conflict added)
            assert len(conflicts) == 0

    @pytest.mark.asyncio
    async def test_detect_conflicts_ta_bot_non_200_response(self):
        """Test conflict detection handles non-200 responses from ta-bot."""
        from unittest.mock import AsyncMock, patch

        from tradeengine.api_config_routes import detect_cross_service_conflicts

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Mock data-manager response (no conflict)
            mock_client.get = AsyncMock(
                return_value=AsyncMock(
                    status_code=200,
                    json=lambda: {
                        "success": True,
                        "data": {"max_positions": 10},
                    },
                )
            )

            # Mock ta-bot response with non-200 status
            mock_client.post = AsyncMock(
                side_effect=[
                    # First call for ta-bot (non-200)
                    AsyncMock(
                        status_code=500,  # Server error
                        json=lambda: {"success": False},
                    ),
                    # Second call for realtime-strategies (no conflict)
                    AsyncMock(
                        status_code=200,
                        json=lambda: {
                            "success": True,
                            "data": {"validation_passed": True},
                        },
                    ),
                ]
            )

            conflicts = await detect_cross_service_conflicts({"leverage": 5})

            # Should handle non-200 response gracefully (no conflict added)
            assert len(conflicts) == 0
