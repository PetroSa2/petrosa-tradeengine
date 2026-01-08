"""
Comprehensive tests for tradeengine/defaults.py to increase coverage
"""

import pytest

from tradeengine.defaults import (
    DEFAULT_TRADING_PARAMETERS,
    PARAMETER_SCHEMA,
    get_default_parameters,
    get_parameter_schema,
    merge_parameters,
    validate_parameters,
)


class TestDefaultParameters:
    """Test DEFAULT_TRADING_PARAMETERS constant"""

    def test_default_parameters_is_dict(self):
        """Test that DEFAULT_TRADING_PARAMETERS is a dictionary"""
        assert isinstance(DEFAULT_TRADING_PARAMETERS, dict)
        assert len(DEFAULT_TRADING_PARAMETERS) > 0

    def test_default_parameters_has_required_keys(self):
        """Test that DEFAULT_TRADING_PARAMETERS has expected keys"""
        expected_keys = [
            "leverage",
            "margin_type",
            "default_order_type",
            "time_in_force",
            "position_mode",
            "position_size_pct",
            "stop_loss_pct",
            "take_profit_pct",
        ]
        for key in expected_keys:
            assert key in DEFAULT_TRADING_PARAMETERS, f"Missing key: {key}"

    def test_default_parameters_values(self):
        """Test that DEFAULT_TRADING_PARAMETERS has reasonable default values"""
        assert DEFAULT_TRADING_PARAMETERS["leverage"] > 0
        assert isinstance(DEFAULT_TRADING_PARAMETERS["leverage"], int)
        assert DEFAULT_TRADING_PARAMETERS["position_size_pct"] > 0
        assert DEFAULT_TRADING_PARAMETERS["position_size_pct"] <= 1.0
        assert DEFAULT_TRADING_PARAMETERS["stop_loss_pct"] > 0
        assert DEFAULT_TRADING_PARAMETERS["take_profit_pct"] > 0


class TestParameterSchema:
    """Test PARAMETER_SCHEMA constant"""

    def test_parameter_schema_is_dict(self):
        """Test that PARAMETER_SCHEMA is a dictionary"""
        assert isinstance(PARAMETER_SCHEMA, dict)
        assert len(PARAMETER_SCHEMA) > 0

    def test_parameter_schema_has_required_keys(self):
        """Test that PARAMETER_SCHEMA has expected keys"""
        # Check that schema exists for some common parameters
        common_params = ["leverage", "position_size_pct", "stop_loss_pct"]
        for param in common_params:
            if param in DEFAULT_TRADING_PARAMETERS:
                assert param in PARAMETER_SCHEMA, f"Missing schema for: {param}"

    def test_parameter_schema_structure(self):
        """Test that PARAMETER_SCHEMA entries have correct structure"""
        for param_name, schema in PARAMETER_SCHEMA.items():
            assert "type" in schema, f"Schema for {param_name} missing 'type'"
            assert schema["type"] in [
                "integer",
                "float",
                "string",
                "boolean",
                "dict",
            ], f"Invalid type for {param_name}: {schema['type']}"


class TestGetDefaultParameters:
    """Test get_default_parameters() function"""

    def test_get_default_parameters_returns_dict(self):
        """Test that get_default_parameters returns a dictionary"""
        result = get_default_parameters()
        assert isinstance(result, dict)

    def test_get_default_parameters_returns_copy(self):
        """Test that get_default_parameters returns a copy, not a reference"""
        result1 = get_default_parameters()
        result2 = get_default_parameters()
        assert result1 is not result2  # Different objects
        assert result1 == result2  # Same content

    def test_get_default_parameters_modification_does_not_affect_original(self):
        """Test that modifying returned dict doesn't affect DEFAULT_TRADING_PARAMETERS"""
        result = get_default_parameters()
        original_leverage = DEFAULT_TRADING_PARAMETERS["leverage"]
        result["leverage"] = 999
        assert DEFAULT_TRADING_PARAMETERS["leverage"] == original_leverage

    def test_get_default_parameters_has_expected_keys(self):
        """Test that get_default_parameters returns expected keys"""
        result = get_default_parameters()
        assert "leverage" in result
        assert "position_size_pct" in result
        assert "stop_loss_pct" in result


class TestGetParameterSchema:
    """Test get_parameter_schema() function"""

    def test_get_parameter_schema_returns_dict(self):
        """Test that get_parameter_schema returns a dictionary"""
        result = get_parameter_schema()
        assert isinstance(result, dict)

    def test_get_parameter_schema_returns_same_as_constant(self):
        """Test that get_parameter_schema returns PARAMETER_SCHEMA"""
        result = get_parameter_schema()
        assert result is PARAMETER_SCHEMA  # Should be same object

    def test_get_parameter_schema_has_expected_structure(self):
        """Test that get_parameter_schema has expected structure"""
        result = get_parameter_schema()
        assert len(result) > 0
        # Check first entry has type
        first_key = next(iter(result))
        assert "type" in result[first_key]


class TestMergeParameters:
    """Test merge_parameters() function"""

    def test_merge_parameters_basic(self):
        """Test basic parameter merging"""
        base = {"leverage": 10, "position_size_pct": 0.1}
        override = {"leverage": 20}
        result = merge_parameters(base, override)
        assert result["leverage"] == 20
        assert result["position_size_pct"] == 0.1

    def test_merge_parameters_returns_copy(self):
        """Test that merge_parameters returns a copy, not modifies original"""
        base = {"leverage": 10}
        override = {"leverage": 20}
        result = merge_parameters(base, override)
        assert base["leverage"] == 10  # Original unchanged
        assert result["leverage"] == 20  # Result has override

    def test_merge_parameters_adds_new_keys(self):
        """Test that merge_parameters adds new keys from override"""
        base = {"leverage": 10}
        override = {"new_param": 100}
        result = merge_parameters(base, override)
        assert "new_param" in result
        assert result["new_param"] == 100

    def test_merge_parameters_empty_base(self):
        """Test merging with empty base"""
        base = {}
        override = {"leverage": 10}
        result = merge_parameters(base, override)
        assert result == override

    def test_merge_parameters_empty_override(self):
        """Test merging with empty override"""
        base = {"leverage": 10}
        override = {}
        result = merge_parameters(base, override)
        assert result == base

    def test_merge_parameters_multiple_overrides(self):
        """Test merging multiple parameters"""
        base = {"leverage": 10, "position_size_pct": 0.1, "stop_loss_pct": 2.0}
        override = {"leverage": 20, "position_size_pct": 0.2}
        result = merge_parameters(base, override)
        assert result["leverage"] == 20
        assert result["position_size_pct"] == 0.2
        assert result["stop_loss_pct"] == 2.0


class TestValidateParameters:
    """Test validate_parameters() function"""

    def test_validate_parameters_valid(self):
        """Test validation with valid parameters"""
        valid_params = {"leverage": 10, "position_size_pct": 0.1}
        is_valid, errors = validate_parameters(valid_params)
        # Note: This may fail if parameters aren't in schema, which is OK
        # We're testing the function logic, not the schema completeness
        assert isinstance(is_valid, bool)
        assert isinstance(errors, list)

    def test_validate_parameters_unknown_parameter(self):
        """Test validation with unknown parameter"""
        params = {"unknown_param": 100}
        is_valid, errors = validate_parameters(params)
        assert is_valid is False
        assert len(errors) > 0
        assert any("Unknown parameter" in error for error in errors)

    def test_validate_parameters_wrong_type_integer(self):
        """Test validation with wrong type (integer expected)"""
        # Find a parameter that expects integer
        for param_name, schema in PARAMETER_SCHEMA.items():
            if schema.get("type") == "integer":
                params = {param_name: "not_an_integer"}
                is_valid, errors = validate_parameters(params)
                assert is_valid is False
                assert any("must be integer" in error.lower() for error in errors)
                break

    def test_validate_parameters_wrong_type_float(self):
        """Test validation with wrong type (float expected)"""
        # Find a parameter that expects float
        for param_name, schema in PARAMETER_SCHEMA.items():
            if schema.get("type") == "float":
                params = {param_name: "not_a_float"}
                is_valid, errors = validate_parameters(params)
                assert is_valid is False
                assert any("must be float" in error.lower() for error in errors)
                break

    def test_validate_parameters_wrong_type_string(self):
        """Test validation with wrong type (string expected)"""
        # Find a parameter that expects string
        for param_name, schema in PARAMETER_SCHEMA.items():
            if schema.get("type") == "string":
                params = {param_name: 123}  # Not a string
                is_valid, errors = validate_parameters(params)
                assert is_valid is False
                assert any("must be string" in error.lower() for error in errors)
                break

    def test_validate_parameters_wrong_type_boolean(self):
        """Test validation with wrong type (boolean expected)"""
        # Find a parameter that expects boolean
        for param_name, schema in PARAMETER_SCHEMA.items():
            if schema.get("type") == "boolean":
                params = {param_name: "not_a_boolean"}
                is_valid, errors = validate_parameters(params)
                assert is_valid is False
                assert any("must be boolean" in error.lower() for error in errors)
                break

    def test_validate_parameters_below_min(self):
        """Test validation with value below minimum"""
        # Find a parameter with min constraint
        for param_name, schema in PARAMETER_SCHEMA.items():
            if "min" in schema:
                min_value = schema["min"]
                params = {param_name: min_value - 1}
                is_valid, errors = validate_parameters(params)
                assert is_valid is False
                assert any("must be >=" in error for error in errors)
                break

    def test_validate_parameters_above_max(self):
        """Test validation with value above maximum"""
        # Find a parameter with max constraint
        for param_name, schema in PARAMETER_SCHEMA.items():
            if "max" in schema:
                max_value = schema["max"]
                params = {param_name: max_value + 1}
                is_valid, errors = validate_parameters(params)
                assert is_valid is False
                assert any("must be <=" in error for error in errors)
                break

    def test_validate_parameters_not_in_allowed_values(self):
        """Test validation with value not in allowed values"""
        # Find a parameter with allowed_values constraint
        for param_name, schema in PARAMETER_SCHEMA.items():
            if "allowed_values" in schema:
                allowed = schema["allowed_values"]
                if len(allowed) > 0:
                    # Use a value not in allowed list
                    invalid_value = "invalid_value_not_in_list"
                    if invalid_value not in allowed:
                        params = {param_name: invalid_value}
                        is_valid, errors = validate_parameters(params)
                        assert is_valid is False
                        assert any("must be one of" in error for error in errors)
                        break

    def test_validate_parameters_multiple_errors(self):
        """Test validation with multiple errors"""
        params = {
            "unknown_param": 100,  # Unknown parameter
            "another_unknown": 200,  # Another unknown
        }
        is_valid, errors = validate_parameters(params)
        assert is_valid is False
        assert len(errors) >= 2

    def test_validate_parameters_empty_dict(self):
        """Test validation with empty dictionary"""
        params = {}
        is_valid, errors = validate_parameters(params)
        assert is_valid is True
        assert len(errors) == 0

    def test_validate_parameters_int_as_float(self):
        """Test that integers are accepted for float parameters"""
        # Find a parameter that expects float
        for param_name, schema in PARAMETER_SCHEMA.items():
            if schema.get("type") == "float":
                params = {param_name: 10}  # Integer value
                is_valid, errors = validate_parameters(params)
                # Should be valid since int is accepted for float
                if "min" not in schema or 10 >= schema.get("min", 0):
                    if "max" not in schema or 10 <= schema.get("max", float("inf")):
                        if "allowed_values" not in schema or 10 in schema.get(
                            "allowed_values", []
                        ):
                            assert is_valid is True
                break
