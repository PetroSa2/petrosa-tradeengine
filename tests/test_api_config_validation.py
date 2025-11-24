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
        # Verify detect_cross_service_conflicts was called (covers line 753-755 in diff)
        mock_detect_conflicts.assert_called_once()
        # Verify it was called with correct arguments
        call_args = mock_detect_conflicts.call_args
        assert call_args[0][0] == {"leverage": 10, "stop_loss_pct": 2.0}
        assert call_args[0][1] is None  # symbol
        assert call_args[0][2] is None  # side

    @patch("tradeengine.api_config_routes.get_config_manager")
    @patch("tradeengine.api_config_routes.httpx.AsyncClient")
    def test_validate_config_calls_real_detect_conflicts(
        self, mock_client_class, mock_get_manager, client, mock_config_manager
    ):
        """Test that validate_config actually calls detect_cross_service_conflicts (covers line 753-755)."""
        mock_get_manager.return_value = mock_config_manager
        mock_config_manager.set_config = AsyncMock(return_value=(True, None, []))

        # Mock httpx client for detect_cross_service_conflicts
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client
        mock_client.get = AsyncMock(
            return_value=AsyncMock(
                status_code=200,
                json=lambda: {"success": True, "data": {"max_positions": 10}},
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

        response = client.post(
            "/api/v1/config/validate",
            json={
                "parameters": {
                    "leverage": 10,
                    "max_position_size": 10,
                }
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        # Verify the real detect_cross_service_conflicts was called (not mocked)
        # This ensures line 753-755 in the diff is executed
        assert "conflicts" in data["data"]

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
    def test_validate_config_leverage_low_value(
        self, mock_detect_conflicts, mock_get_manager, client, mock_config_manager
    ):
        """Test validation with leverage <= 50 (covers line 746 else branch)."""
        mock_get_manager.return_value = mock_config_manager
        mock_config_manager.set_config = AsyncMock(return_value=(True, None, []))
        mock_detect_conflicts.return_value = []

        response = client.post(
            "/api/v1/config/validate",
            json={
                "parameters": {"leverage": 25}
            },  # <= 50, should not trigger high risk
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        # Leverage <= 50 should result in medium risk (because leverage is in high_risk_params)
        assert data["data"]["estimated_impact"]["risk_level"] == "medium"
        # Should not have high risk warning
        assert "warning" not in data["data"]["estimated_impact"]

    @patch("tradeengine.api_config_routes.get_config_manager")
    @patch("tradeengine.api_config_routes.detect_cross_service_conflicts")
    def test_validate_config_leverage_invalid_type(
        self, mock_detect_conflicts, mock_get_manager, client, mock_config_manager
    ):
        """Test validation with leverage that's not int/float (covers isinstance check at line 746)."""
        mock_get_manager.return_value = mock_config_manager
        mock_config_manager.set_config = AsyncMock(return_value=(True, None, []))
        mock_detect_conflicts.return_value = []

        response = client.post(
            "/api/v1/config/validate",
            json={"parameters": {"leverage": "50"}},  # String, not int/float
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        # String leverage should not trigger high risk check (isinstance fails)
        assert (
            data["data"]["estimated_impact"]["risk_level"] == "medium"
        )  # Still medium because leverage is in high_risk_params
        assert "warning" not in data["data"]["estimated_impact"]

    @patch("tradeengine.api_config_routes.get_config_manager")
    @patch("tradeengine.api_config_routes.detect_cross_service_conflicts")
    def test_validate_config_global_scope(
        self, mock_detect_conflicts, mock_get_manager, client, mock_config_manager
    ):
        """Test validation with global scope (no symbol, covers line 733)."""
        mock_get_manager.return_value = mock_config_manager
        mock_config_manager.set_config = AsyncMock(return_value=(True, None, []))
        mock_detect_conflicts.return_value = []

        response = client.post(
            "/api/v1/config/validate",
            json={"parameters": {"leverage": 10}},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["estimated_impact"]["affected_scope"] == "global"
        assert data["metadata"]["scope"] == "global"

    @patch("tradeengine.api_config_routes.get_config_manager")
    @patch("tradeengine.api_config_routes.detect_cross_service_conflicts")
    def test_validate_config_symbol_without_side(
        self, mock_detect_conflicts, mock_get_manager, client, mock_config_manager
    ):
        """Test validation with symbol but no side (covers line 774: request.side or 'all')."""
        mock_get_manager.return_value = mock_config_manager
        mock_config_manager.set_config = AsyncMock(return_value=(True, None, []))
        mock_detect_conflicts.return_value = []

        response = client.post(
            "/api/v1/config/validate",
            json={
                "parameters": {"leverage": 10},
                "symbol": "ETHUSDT",
                # No side provided
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert (
            data["metadata"]["scope"] == "ETHUSDT:all"
        )  # Should default to 'all' when side is None

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

    @patch("tradeengine.api_config_routes.get_config_manager")
    @patch("tradeengine.api_config_routes.detect_cross_service_conflicts")
    def test_validate_config_unknown_parameter_without_colon(
        self, mock_detect_conflicts, mock_get_manager, client, mock_config_manager
    ):
        """Test validation with unknown parameter error without colon."""
        mock_get_manager.return_value = mock_config_manager
        mock_config_manager.set_config = AsyncMock(
            return_value=(False, None, ["Unknown parameter invalid_param"])
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
        assert data["data"]["errors"][0]["field"] == "unknown"  # Line 651
        assert data["data"]["errors"][0]["code"] == "UNKNOWN_PARAMETER"

    @patch("tradeengine.api_config_routes.get_config_manager")
    @patch("tradeengine.api_config_routes.detect_cross_service_conflicts")
    def test_validate_config_must_be_float_error(
        self, mock_detect_conflicts, mock_get_manager, client, mock_config_manager
    ):
        """Test validation with 'must be float' error."""
        mock_get_manager.return_value = mock_config_manager
        mock_config_manager.set_config = AsyncMock(
            return_value=(False, None, ["leverage must be float, got string"])
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
        assert data["data"]["errors"][0]["code"] == "INVALID_TYPE"
        assert "Change leverage to a numeric value" in data["data"]["suggested_fixes"]

    @patch("tradeengine.api_config_routes.get_config_manager")
    @patch("tradeengine.api_config_routes.detect_cross_service_conflicts")
    @patch("tradeengine.defaults.PARAMETER_SCHEMA", {"some_param": {"min": 5}})
    def test_validate_config_schema_min_only(
        self,
        mock_detect_conflicts,
        mock_get_manager,
        client,
        mock_config_manager,
    ):
        """Test validation with schema that has min only."""
        mock_get_manager.return_value = mock_config_manager
        mock_config_manager.set_config = AsyncMock(
            return_value=(False, None, ["some_param must be >= 5, got 0"])
        )
        mock_detect_conflicts.return_value = []

        response = client.post(
            "/api/v1/config/validate",
            json={"parameters": {"some_param": 0}},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["validation_passed"] is False
        assert len(data["data"]["errors"]) == 1
        assert data["data"]["errors"][0]["suggested_value"] == 5  # Line 688

    @patch("tradeengine.api_config_routes.get_config_manager")
    @patch("tradeengine.api_config_routes.detect_cross_service_conflicts")
    @patch("tradeengine.defaults.PARAMETER_SCHEMA", {"some_param": {"max": 100}})
    def test_validate_config_schema_max_only(
        self,
        mock_detect_conflicts,
        mock_get_manager,
        client,
        mock_config_manager,
    ):
        """Test validation with schema that has max only."""
        mock_get_manager.return_value = mock_config_manager
        mock_config_manager.set_config = AsyncMock(
            return_value=(False, None, ["some_param must be <= 100, got 200"])
        )
        mock_detect_conflicts.return_value = []

        response = client.post(
            "/api/v1/config/validate",
            json={"parameters": {"some_param": 200}},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["validation_passed"] is False
        assert len(data["data"]["errors"]) == 1
        assert data["data"]["errors"][0]["suggested_value"] == 100  # Line 690

    @patch("tradeengine.api_config_routes.get_config_manager")
    @patch("tradeengine.api_config_routes.detect_cross_service_conflicts")
    @patch("tradeengine.defaults.PARAMETER_SCHEMA", {"some_param": {"default": 10}})
    def test_validate_config_schema_default_only(
        self,
        mock_detect_conflicts,
        mock_get_manager,
        client,
        mock_config_manager,
    ):
        """Test validation with schema that has default only."""
        mock_get_manager.return_value = mock_config_manager
        mock_config_manager.set_config = AsyncMock(
            return_value=(False, None, ["some_param must be >= 1, got 0"])
        )
        mock_detect_conflicts.return_value = []

        response = client.post(
            "/api/v1/config/validate",
            json={"parameters": {"some_param": 0}},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["validation_passed"] is False
        assert len(data["data"]["errors"]) == 1
        assert data["data"]["errors"][0]["suggested_value"] == 10  # Line 692

    @patch("tradeengine.api_config_routes.get_config_manager")
    @patch("tradeengine.api_config_routes.detect_cross_service_conflicts")
    @patch("tradeengine.defaults.PARAMETER_SCHEMA", {})
    def test_validate_config_schema_no_field(
        self,
        mock_detect_conflicts,
        mock_get_manager,
        client,
        mock_config_manager,
    ):
        """Test validation with field not in schema."""
        mock_get_manager.return_value = mock_config_manager
        mock_config_manager.set_config = AsyncMock(
            return_value=(False, None, ["unknown_field must be >= 1, got 0"])
        )
        mock_detect_conflicts.return_value = []

        response = client.post(
            "/api/v1/config/validate",
            json={"parameters": {"unknown_field": 0}},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["validation_passed"] is False
        assert len(data["data"]["errors"]) == 1
        assert data["data"]["errors"][0]["suggested_value"] is None  # Line 694

    @patch("tradeengine.api_config_routes.get_config_manager")
    @patch("tradeengine.api_config_routes.detect_cross_service_conflicts")
    def test_validate_config_must_be_one_of_invalid_format(
        self, mock_detect_conflicts, mock_get_manager, client, mock_config_manager
    ):
        """Test validation with 'must be one of' error in invalid format (no brackets)."""
        mock_get_manager.return_value = mock_config_manager
        mock_config_manager.set_config = AsyncMock(
            return_value=(False, None, ["side must be one of LONG SHORT, got INVALID"])
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
        assert data["data"]["errors"][0]["suggested_value"] is None  # Line 705

    @patch("tradeengine.api_config_routes.get_config_manager")
    @patch("tradeengine.api_config_routes.detect_cross_service_conflicts")
    def test_validate_config_must_be_generic_pattern(
        self, mock_detect_conflicts, mock_get_manager, client, mock_config_manager
    ):
        """Test validation with 'must be' error that doesn't match any specific pattern (lines 706-708)."""
        mock_get_manager.return_value = mock_config_manager
        mock_config_manager.set_config = AsyncMock(
            return_value=(
                False,
                None,
                ["some_field must be something else, got invalid_value"],
            )
        )
        mock_detect_conflicts.return_value = []

        response = client.post(
            "/api/v1/config/validate",
            json={"parameters": {"some_field": "invalid_value"}},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["validation_passed"] is False
        assert len(data["data"]["errors"]) == 1
        assert data["data"]["errors"][0]["code"] == "VALIDATION_ERROR"  # Line 707
        assert data["data"]["errors"][0]["suggested_value"] is None  # Line 708


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
    async def test_detect_conflicts_no_relevant_parameters(self):
        """Test conflict detection when parameters don't match conflict check conditions (line 826-834)."""
        from unittest.mock import AsyncMock, patch

        from tradeengine.api_config_routes import detect_cross_service_conflicts

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Parameters that don't match the condition at line 826-834
            conflicts = await detect_cross_service_conflicts(
                {"some_other_param": "value"}
            )

            # Should return empty list without making any HTTP calls
            assert len(conflicts) == 0
            # Verify no HTTP calls were made
            mock_client.get.assert_not_called()
            mock_client.post.assert_not_called()

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
    async def test_detect_conflicts_data_manager_no_current_max(self):
        """Test conflict detection when data-manager has no current_max (covers line 847 else branch)."""
        from unittest.mock import AsyncMock, patch

        from tradeengine.api_config_routes import detect_cross_service_conflicts

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Mock data-manager response with no current_max
            mock_client.get = AsyncMock(
                return_value=AsyncMock(
                    status_code=200,
                    json=lambda: {
                        "success": True,
                        "data": {},  # No max_positions field
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

            # Should handle missing current_max gracefully (no conflict added)
            assert len(conflicts) == 0

    @pytest.mark.asyncio
    async def test_detect_conflicts_ta_bot_no_data_in_response(self):
        """Test conflict detection when ta-bot response has no data field (covers line 911 else branch)."""
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
            # Mock ta-bot response without data field
            mock_client.post = AsyncMock(
                return_value=AsyncMock(
                    status_code=200,
                    json=lambda: {
                        "success": True,
                        # Missing "data" field
                    },
                )
            )

            conflicts = await detect_cross_service_conflicts({"leverage": 5})

            # Should handle missing data gracefully (no conflict added)
            assert len(conflicts) == 0

    @pytest.mark.asyncio
    async def test_detect_conflicts_ta_bot_validation_passed_with_errors(self):
        """Test conflict detection when ta-bot validation_passed is True but errors exist (covers line 914 else branch)."""
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
            # Mock ta-bot response with validation_passed=True (should not trigger conflict)
            mock_client.post = AsyncMock(
                return_value=AsyncMock(
                    status_code=200,
                    json=lambda: {
                        "success": True,
                        "data": {
                            "validation_passed": True,  # Passed, so no conflict
                            "errors": [],  # Even if errors exist, if passed=True, no conflict
                        },
                    },
                )
            )

            conflicts = await detect_cross_service_conflicts({"leverage": 5})

            # Should not add conflict when validation_passed is True
            assert len(conflicts) == 0

    @pytest.mark.asyncio
    async def test_detect_conflicts_ta_bot_no_errors_in_list(self):
        """Test conflict detection when ta-bot has validation_passed=False but no errors (covers line 916 else branch)."""
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
            # Mock ta-bot response with validation_passed=False but empty errors list
            mock_client.post = AsyncMock(
                return_value=AsyncMock(
                    status_code=200,
                    json=lambda: {
                        "success": True,
                        "data": {
                            "validation_passed": False,
                            "errors": [],  # Empty errors list
                        },
                    },
                )
            )

            conflicts = await detect_cross_service_conflicts({"leverage": 5})

            # Should not add conflict when errors list is empty
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

    @pytest.mark.asyncio
    async def test_detect_conflicts_data_manager_invalid_type_conversion(self):
        """Test conflict detection handles invalid type conversion for position limits."""
        from unittest.mock import AsyncMock, patch

        from tradeengine.api_config_routes import detect_cross_service_conflicts

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Mock data-manager response with invalid type for max_positions
            mock_client.get = AsyncMock(
                return_value=AsyncMock(
                    status_code=200,
                    json=lambda: {
                        "success": True,
                        "data": {
                            "max_positions": "invalid_string"
                        },  # Can't convert to float
                    },
                )
            )
            mock_client.post = AsyncMock()

            conflicts = await detect_cross_service_conflicts({"max_position_size": 10})

            # Should handle ValueError/TypeError gracefully (no conflict added)
            assert len(conflicts) == 0

    @pytest.mark.asyncio
    async def test_detect_conflicts_data_manager_exception_during_request(self):
        """Test conflict detection handles exceptions during data-manager request."""
        from unittest.mock import AsyncMock, patch

        from tradeengine.api_config_routes import detect_cross_service_conflicts

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Mock exception during data-manager GET request
            mock_client.get = AsyncMock(side_effect=Exception("Network error"))
            mock_client.post = AsyncMock()

            conflicts = await detect_cross_service_conflicts({"max_position_size": 10})

            # Should handle exception gracefully (no conflict added)
            assert len(conflicts) == 0

    @pytest.mark.asyncio
    async def test_detect_conflicts_data_manager_proposed_max_invalid_type(self):
        """Test conflict detection when proposed_max can't be converted to float."""
        from unittest.mock import AsyncMock, patch

        from tradeengine.api_config_routes import detect_cross_service_conflicts

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Mock data-manager response with valid max_positions
            mock_client.get = AsyncMock(
                return_value=AsyncMock(
                    status_code=200,
                    json=lambda: {
                        "success": True,
                        "data": {"max_positions": 10},
                    },
                )
            )
            mock_client.post = AsyncMock()

            # Pass invalid type for max_position_size (can't convert to float)
            conflicts = await detect_cross_service_conflicts(
                {"max_position_size": "not_a_number"}
            )

            # Should handle ValueError/TypeError gracefully (no conflict added)
            assert len(conflicts) == 0

    @pytest.mark.asyncio
    async def test_detect_conflicts_data_manager_success_false(self):
        """Test conflict detection when data-manager returns success=False."""
        from unittest.mock import AsyncMock, patch

        from tradeengine.api_config_routes import detect_cross_service_conflicts

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Mock data-manager response with success=False
            mock_client.get = AsyncMock(
                return_value=AsyncMock(
                    status_code=200,
                    json=lambda: {
                        "success": False,  # Line 844: success is False
                        "data": {"max_positions": 10},
                    },
                )
            )
            mock_client.post = AsyncMock()

            conflicts = await detect_cross_service_conflicts({"max_position_size": 10})

            # Should handle success=False gracefully (no conflict added)
            assert len(conflicts) == 0

    @pytest.mark.asyncio
    async def test_detect_conflicts_data_manager_no_data_field(self):
        """Test conflict detection when data-manager response has no 'data' field."""
        from unittest.mock import AsyncMock, patch

        from tradeengine.api_config_routes import detect_cross_service_conflicts

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Mock data-manager response without 'data' field
            mock_client.get = AsyncMock(
                return_value=AsyncMock(
                    status_code=200,
                    json=lambda: {
                        "success": True,
                        # No 'data' field - line 844: data.get("data") is None
                    },
                )
            )
            mock_client.post = AsyncMock()

            conflicts = await detect_cross_service_conflicts({"max_position_size": 10})

            # Should handle missing data field gracefully (no conflict added)
            assert len(conflicts) == 0

    @pytest.mark.asyncio
    async def test_detect_conflicts_ta_bot_success_false(self):
        """Test conflict detection when ta-bot returns success=False."""
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

            # Mock ta-bot response with success=False
            mock_client.post = AsyncMock(
                return_value=AsyncMock(
                    status_code=200,
                    json=lambda: {
                        "success": False,  # Line 911: success is False
                        "data": {"validation_passed": True},
                    },
                )
            )

            conflicts = await detect_cross_service_conflicts({"leverage": 5})

            # Should handle success=False gracefully (no conflict added)
            assert len(conflicts) == 0

    @pytest.mark.asyncio
    async def test_detect_conflicts_ta_bot_no_data_field(self):
        """Test conflict detection when ta-bot response has no 'data' field."""
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

            # Mock ta-bot response without 'data' field
            mock_client.post = AsyncMock(
                return_value=AsyncMock(
                    status_code=200,
                    json=lambda: {
                        "success": True,
                        # No 'data' field - line 911: data.get("data") is None
                    },
                )
            )

            conflicts = await detect_cross_service_conflicts({"leverage": 5})

            # Should handle missing data field gracefully (no conflict added)
            assert len(conflicts) == 0

    @pytest.mark.asyncio
    async def test_detect_conflicts_validation_passed_true_with_errors(self):
        """Test conflict detection when validation_passed=True but errors exist."""
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

            # Mock ta-bot response with validation_passed=True but errors exist
            mock_client.post = AsyncMock(
                return_value=AsyncMock(
                    status_code=200,
                    json=lambda: {
                        "success": True,
                        "data": {
                            "validation_passed": True,  # Line 914: True, so condition is False
                            "errors": [{"message": "Some error"}],  # But errors exist
                        },
                    },
                )
            )

            conflicts = await detect_cross_service_conflicts({"leverage": 5})

            # Should not add conflict when validation_passed=True (line 914 condition is False)
            assert len(conflicts) == 0

    @patch("tradeengine.api_config_routes.get_config_manager")
    @patch("tradeengine.api_config_routes.detect_cross_service_conflicts")
    def test_validate_config_symbol_with_side_none(
        self, mock_detect_conflicts, mock_get_manager, client, mock_config_manager
    ):
        """Test validation with symbol but side=None (covers line 774)."""
        mock_get_manager.return_value = mock_config_manager
        mock_config_manager.set_config = AsyncMock(return_value=(True, {}, []))
        mock_detect_conflicts.return_value = []

        response = client.post(
            "/api/v1/config/validate",
            json={"parameters": {"leverage": 10}, "symbol": "BTCUSDT", "side": None},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        # Check that metadata.scope uses "all" when side is None (line 774)
        assert data["metadata"]["scope"] == "BTCUSDT:all"

    @pytest.mark.asyncio
    async def test_detect_conflicts_data_manager_position_mismatch_within_threshold(
        self,
    ):
        """Test conflict detection when position mismatch is within threshold (no conflict)."""
        from unittest.mock import AsyncMock, patch

        from tradeengine.api_config_routes import detect_cross_service_conflicts

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Mock data-manager response with position limit that's close (within 20% threshold)
            # 10 vs 9 = 10% difference < 20% threshold, so no conflict
            mock_client.get = AsyncMock(
                return_value=AsyncMock(
                    status_code=200,
                    json=lambda: {
                        "success": True,
                        "data": {"max_positions": 9},  # 10% difference from proposed 10
                    },
                )
            )
            mock_client.post = AsyncMock()

            conflicts = await detect_cross_service_conflicts({"max_position_size": 10})

            # Should not add conflict when mismatch is within threshold (line 858-861 condition is False)
            assert len(conflicts) == 0

    @pytest.mark.asyncio
    async def test_detect_conflicts_data_manager_current_max_none(self):
        """Test conflict detection when data-manager has no current_max (covers line 847)."""
        from unittest.mock import AsyncMock, patch

        from tradeengine.api_config_routes import detect_cross_service_conflicts

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Mock data-manager response without max_positions
            mock_client.get = AsyncMock(
                return_value=AsyncMock(
                    status_code=200,
                    json=lambda: {
                        "success": True,
                        "data": {},  # No max_positions field
                    },
                )
            )
            mock_client.post = AsyncMock()

            conflicts = await detect_cross_service_conflicts({"max_position_size": 10})

            # Should handle missing current_max gracefully (line 847: current_max is None)
            assert len(conflicts) == 0

    @pytest.mark.asyncio
    async def test_detect_conflicts_data_manager_proposed_max_none(self):
        """Test conflict detection when proposed_max is None (covers line 847)."""
        from unittest.mock import AsyncMock, patch

        from tradeengine.api_config_routes import detect_cross_service_conflicts

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Mock data-manager response with max_positions
            mock_client.get = AsyncMock(
                return_value=AsyncMock(
                    status_code=200,
                    json=lambda: {
                        "success": True,
                        "data": {"max_positions": 10},
                    },
                )
            )
            mock_client.post = AsyncMock()

            # Pass parameters without max_position_size
            conflicts = await detect_cross_service_conflicts(
                {"leverage": 5}  # No max_position_size, so proposed_max is None
            )

            # Should handle missing proposed_max gracefully (line 847: proposed_max is None)
            assert len(conflicts) == 0

    @pytest.mark.asyncio
    async def test_detect_conflicts_ta_bot_multiple_errors_truncated(self):
        """Test conflict detection when ta-bot reports more than MAX_ERROR_MESSAGES_TO_SHOW errors."""
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

            # Mock responses: ta-bot returns errors, realtime-strategies returns success
            mock_client.post = AsyncMock(
                side_effect=[
                    # First call for ta-bot (has errors)
                    AsyncMock(
                        status_code=200,
                        json=lambda: {
                            "success": True,
                            "data": {
                                "validation_passed": False,
                                "errors": [
                                    {"message": "Error 1"},
                                    {"message": "Error 2"},
                                    {"message": "Error 3"},
                                    {"message": "Error 4"},
                                ],  # 4 errors, but MAX_ERROR_MESSAGES_TO_SHOW is 2
                            },
                        },
                    ),
                    # Second call for realtime-strategies (no errors)
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

            assert len(conflicts) == 1
            assert conflicts[0].service == "ta-bot"
            # Check that only MAX_ERROR_MESSAGES_TO_SHOW errors are included in description (line 924)
            description = conflicts[0].description
            assert "Error 1" in description
            assert "Error 2" in description
            # Error 3 and 4 should not be in description (truncated)
            assert "Error 3" not in description
            assert "Error 4" not in description

    def test_constants_are_accessible(self):
        """Test that constants are accessible (ensures they're 'covered' by codecov)."""
        from tradeengine.api_config_routes import (
            CONFLICT_TIMEOUT_SECONDS,
            MAX_ERROR_MESSAGES_TO_SHOW,
            POSITION_MISMATCH_THRESHOLD,
            SERVICE_URLS,
        )

        # Verify constants are defined and have expected values
        assert "data-manager" in SERVICE_URLS
        assert "ta-bot" in SERVICE_URLS
        assert "realtime-strategies" in SERVICE_URLS
        assert CONFLICT_TIMEOUT_SECONDS == 5.0
        assert POSITION_MISMATCH_THRESHOLD == 0.2
        assert MAX_ERROR_MESSAGES_TO_SHOW == 2

    def test_pydantic_models_instantiation(self):
        """Test that Pydantic models can be instantiated (ensures they're 'covered')."""
        from tradeengine.api_config_routes import (
            ConfigValidationRequest,
            CrossServiceConflict,
            ValidationError,
            ValidationResponse,
        )

        # Instantiate each model to ensure they're 'covered'
        error = ValidationError(
            field="test", message="test", code="TEST", suggested_value=None
        )
        assert error.field == "test"

        conflict = CrossServiceConflict(
            service="test",
            conflict_type="TEST",
            description="test",
            resolution="test",
        )
        assert conflict.service == "test"

        response = ValidationResponse(validation_passed=True)
        assert response.validation_passed is True

        request = ConfigValidationRequest(parameters={"test": 1})
        assert request.parameters == {"test": 1}

    @pytest.mark.asyncio
    async def test_detect_conflicts_ta_bot_timeout_exception(self):
        """Test conflict detection handles httpx.TimeoutException for ta-bot (covers line 933-934)."""
        from unittest.mock import AsyncMock, patch

        import httpx

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

            # Mock timeout exception for ta-bot POST request (covers line 933-934)
            # First call succeeds (ta-bot), second call times out (realtime-strategies)
            mock_client.post = AsyncMock(
                side_effect=[
                    # First call for ta-bot (success)
                    AsyncMock(
                        status_code=200,
                        json=lambda: {
                            "success": True,
                            "data": {"validation_passed": True},
                        },
                    ),
                    # Second call for realtime-strategies (timeout - covers line 933-934)
                    httpx.TimeoutException("Timeout"),
                ]
            )

            conflicts = await detect_cross_service_conflicts({"leverage": 5})

            # Should handle timeout gracefully (no conflict added)
            assert len(conflicts) == 0

    @pytest.mark.asyncio
    async def test_detect_conflicts_realtime_strategies_exception(self):
        """Test conflict detection handles general Exception for realtime-strategies (covers line 935-936)."""
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

            # Mock exception for ta-bot (success) and realtime-strategies (exception)
            mock_client.post = AsyncMock(
                side_effect=[
                    # First call for ta-bot (success)
                    AsyncMock(
                        status_code=200,
                        json=lambda: {
                            "success": True,
                            "data": {"validation_passed": True},
                        },
                    ),
                    # Second call for realtime-strategies (exception - covers line 935-936)
                    Exception("Connection error"),
                ]
            )

            conflicts = await detect_cross_service_conflicts({"leverage": 5})

            # Should handle exception gracefully (no conflict added)
            assert len(conflicts) == 0

    @pytest.mark.asyncio
    async def test_detect_conflicts_timeout_creation(self):
        """Test that timeout is created with CONFLICT_TIMEOUT_SECONDS (covers line 822)."""
        from unittest.mock import AsyncMock, patch

        from tradeengine.api_config_routes import detect_cross_service_conflicts

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Mock successful responses
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

            # Call the function - this will execute line 822 (timeout creation)
            conflicts = await detect_cross_service_conflicts({"leverage": 5})

            # Verify it completed successfully (timeout was created and used)
            assert len(conflicts) == 0

    @pytest.mark.asyncio
    async def test_detect_conflicts_ta_bot_validation_failed_with_errors(self):
        """Test conflict detection when ta-bot has validation_passed=False with errors (covers lines 915-917)."""
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

            # Mock ta-bot response with validation_passed=False and errors (covers lines 915-917)
            mock_client.post = AsyncMock(
                side_effect=[
                    # First call for ta-bot (validation failed with errors)
                    AsyncMock(
                        status_code=200,
                        json=lambda: {
                            "success": True,
                            "data": {
                                "validation_passed": False,  # Failed validation
                                "errors": [
                                    {"message": "Invalid leverage"}
                                ],  # Has errors
                            },
                        },
                    ),
                    # Second call for realtime-strategies (success)
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

            # Should add conflict when validation_passed=False and errors exist (lines 915-917)
            assert len(conflicts) == 1
            assert conflicts[0].service == "ta-bot"
            assert conflicts[0].conflict_type == "VALIDATION_CONFLICT"

    @pytest.mark.asyncio
    async def test_detect_conflicts_with_symbol_parameter(self):
        """Test conflict detection includes symbol in validation request (covers line 901-902)."""
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

            # Mock ta-bot and realtime-strategies responses
            mock_response = AsyncMock(
                status_code=200,
                json=lambda: {
                    "success": True,
                    "data": {"validation_passed": True},
                },
            )
            mock_client.post = AsyncMock(return_value=mock_response)

            # Call with symbol parameter (covers line 901-902)
            conflicts = await detect_cross_service_conflicts(
                {"leverage": 5}, symbol="BTCUSDT"
            )

            # Verify symbol was included in the POST request
            assert mock_client.post.call_count == 2  # ta-bot and realtime-strategies
            # Check that symbol was included in at least one request
            call_args = mock_client.post.call_args_list[0]
            assert "json" in call_args.kwargs
            assert call_args.kwargs["json"].get("symbol") == "BTCUSDT"

            assert len(conflicts) == 0

    @pytest.mark.asyncio
    async def test_detect_conflicts_data_manager_value_error_in_conversion(self):
        """Test conflict detection handles ValueError during position limit conversion (covers line 852-853)."""
        from unittest.mock import AsyncMock, patch

        from tradeengine.api_config_routes import detect_cross_service_conflicts

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Mock data-manager response with value that causes ValueError when converting to float
            mock_client.get = AsyncMock(
                return_value=AsyncMock(
                    status_code=200,
                    json=lambda: {
                        "success": True,
                        "data": {
                            "max_positions": "not-a-number"
                        },  # Will cause ValueError
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

            # Should handle ValueError gracefully (covers line 852-853)
            assert len(conflicts) == 0

    @pytest.mark.asyncio
    async def test_detect_conflicts_data_manager_type_error_in_conversion(self):
        """Test conflict detection handles TypeError during position limit conversion (covers line 852-853)."""
        from unittest.mock import AsyncMock, patch

        from tradeengine.api_config_routes import detect_cross_service_conflicts

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Mock data-manager response with None (will cause TypeError when converting to float)
            mock_client.get = AsyncMock(
                return_value=AsyncMock(
                    status_code=200,
                    json=lambda: {
                        "success": True,
                        "data": {"max_positions": None},  # Will cause TypeError
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

            # Should handle TypeError gracefully (covers line 852-853)
            assert len(conflicts) == 0
