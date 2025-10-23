"""
Comprehensive tests for shared/constants.py to increase coverage
"""

from unittest.mock import patch

import pytest

from shared.constants import (
    APP_NAME,
    APP_VERSION,
    Environment,
    LogLevel,
    deprecation_warning,
    get_config_summary,
    get_mongodb_connection_string,
    get_nats_connection_string,
    validate_configuration,
    validate_mongodb_config,
    validate_nats_config,
)


class TestEnvironmentEnum:
    """Test Environment enum"""

    def test_environment_values(self):
        """Test all environment enum values"""
        assert Environment.DEVELOPMENT == "development"
        assert Environment.STAGING == "staging"
        assert Environment.PRODUCTION == "production"
        assert Environment.TESTING == "testing"


class TestLogLevelEnum:
    """Test LogLevel enum"""

    def test_log_level_values(self):
        """Test all log level enum values"""
        assert LogLevel.DEBUG == "DEBUG"
        assert LogLevel.INFO == "INFO"
        assert LogLevel.WARNING == "WARNING"
        assert LogLevel.ERROR == "ERROR"
        assert LogLevel.CRITICAL == "CRITICAL"


class TestApplicationConstants:
    """Test application constants"""

    def test_app_constants(self):
        """Test application constant values"""
        assert APP_NAME == "Petrosa Trading Engine"
        assert APP_VERSION == "0.1.0"
        assert isinstance(APP_NAME, str)
        assert isinstance(APP_VERSION, str)


class TestMongoDBValidation:
    """Test MongoDB configuration validation"""

    def test_validate_mongodb_config_missing_uri(self):
        """Test MongoDB validation logs warning when URI is missing (Data Manager mode)"""
        with patch("shared.constants.MONGODB_URI", None):
            with patch("logging.getLogger") as mock_get_logger:
                mock_logger = mock_get_logger.return_value
                validate_mongodb_config()
                mock_logger.warning.assert_called_once()
                assert "MongoDB URI not configured" in str(mock_logger.warning.call_args)

    def test_validate_mongodb_config_missing_database(self):
        """Test MongoDB validation logs warning when database is missing (Data Manager mode)"""
        with patch("shared.constants.MONGODB_URI", "mongodb://localhost:27017"):
            with patch("shared.constants.MONGODB_DATABASE", None):
                with patch("logging.getLogger") as mock_get_logger:
                    mock_logger = mock_get_logger.return_value
                    validate_mongodb_config()
                    mock_logger.warning.assert_called_once()
                    assert "MongoDB database not configured" in str(mock_logger.warning.call_args)

    def test_validate_mongodb_config_invalid_uri_format(self):
        """Test MongoDB validation fails with invalid URI format"""
        with patch("shared.constants.MONGODB_URI", "invalid://localhost"):
            with patch("shared.constants.MONGODB_DATABASE", "petrosa"):
                with pytest.raises(ValueError, match="Invalid MongoDB URI format"):
                    validate_mongodb_config()

    def test_validate_mongodb_config_valid(self):
        """Test MongoDB validation succeeds with valid config"""
        with patch("shared.constants.MONGODB_URI", "mongodb://localhost:27017"):
            with patch("shared.constants.MONGODB_DATABASE", "petrosa"):
                # Should not raise
                validate_mongodb_config()

    def test_validate_mongodb_config_valid_srv(self):
        """Test MongoDB validation succeeds with valid mongodb+srv URI"""
        with patch("shared.constants.MONGODB_URI", "mongodb+srv://cluster.mongodb.net"):
            with patch("shared.constants.MONGODB_DATABASE", "petrosa"):
                # Should not raise
                validate_mongodb_config()

    def test_get_mongodb_connection_string(self):
        """Test MongoDB connection string generation"""
        with patch("shared.constants.MONGODB_URI", "mongodb://localhost:27017"):
            with patch("shared.constants.MONGODB_DATABASE", "petrosa"):
                conn_str = get_mongodb_connection_string()
                assert conn_str == "mongodb://localhost:27017/petrosa"


class TestNATSValidation:
    """Test NATS configuration validation"""

    def test_validate_nats_config_enabled_missing_url(self):
        """Test NATS validation fails when enabled but URL is missing"""
        with patch("shared.constants.NATS_ENABLED", True):
            with patch("shared.constants.NATS_URL", None):
                with pytest.raises(
                    ValueError, match="NATS is enabled but NATS_URL is not configured"
                ):
                    validate_nats_config()

    def test_validate_nats_config_disabled(self):
        """Test NATS validation succeeds when disabled"""
        with patch("shared.constants.NATS_ENABLED", False):
            # Should not raise
            validate_nats_config()

    def test_validate_nats_config_enabled_with_url(self):
        """Test NATS validation succeeds when enabled with URL"""
        with patch("shared.constants.NATS_ENABLED", True):
            with patch("shared.constants.NATS_URL", "nats://localhost:4222"):
                # Should not raise
                validate_nats_config()

    def test_get_nats_connection_string_disabled(self):
        """Test NATS connection string returns None when disabled"""
        with patch("shared.constants.NATS_ENABLED", False):
            assert get_nats_connection_string() is None

    def test_get_nats_connection_string_enabled(self):
        """Test NATS connection string returns URL when enabled"""
        with patch("shared.constants.NATS_ENABLED", True):
            with patch("shared.constants.NATS_URL", "nats://localhost:4222"):
                conn_str = get_nats_connection_string()
                assert conn_str == "nats://localhost:4222"


class TestConfigurationValidation:
    """Test overall configuration validation"""

    def test_validate_configuration_production_missing_binance_key(self):
        """Test configuration validation in production with missing Binance key"""
        with patch("shared.constants.ENVIRONMENT", Environment.PRODUCTION):
            with patch("shared.constants.BINANCE_API_KEY", ""):
                with patch("shared.constants.BINANCE_API_SECRET", "secret"):
                    with patch("shared.constants.JWT_SECRET_KEY", "jwt_key"):
                        with patch(
                            "shared.constants.MONGODB_URI", "mongodb://localhost"
                        ):
                            issues = validate_configuration()
                            assert "BINANCE_API_KEY is required in production" in issues

    def test_validate_configuration_production_missing_binance_secret(self):
        """Test configuration validation in production with missing Binance secret"""
        with patch("shared.constants.ENVIRONMENT", Environment.PRODUCTION):
            with patch("shared.constants.BINANCE_API_KEY", "key"):
                with patch("shared.constants.BINANCE_API_SECRET", ""):
                    with patch("shared.constants.JWT_SECRET_KEY", "jwt_key"):
                        with patch(
                            "shared.constants.MONGODB_URI", "mongodb://localhost"
                        ):
                            issues = validate_configuration()
                            assert (
                                "BINANCE_API_SECRET is required in production" in issues
                            )

    def test_validate_configuration_production_default_jwt_secret(self):
        """Test configuration validation in production with default JWT secret"""
        with patch("shared.constants.ENVIRONMENT", Environment.PRODUCTION):
            with patch("shared.constants.BINANCE_API_KEY", "key"):
                with patch("shared.constants.BINANCE_API_SECRET", "secret"):
                    with patch(
                        "shared.constants.JWT_SECRET_KEY",
                        "your-secret-key-change-in-production",
                    ):
                        with patch(
                            "shared.constants.MONGODB_URI", "mongodb://localhost"
                        ):
                            issues = validate_configuration()
                            assert (
                                "JWT_SECRET_KEY must be changed in production" in issues
                            )

    def test_validate_configuration_missing_mongodb_uri(self):
        """Test configuration validation with missing MongoDB URI"""
        with patch("shared.constants.MONGODB_URI", None):
            issues = validate_configuration()
            assert "MONGODB_URI is required" in issues

    def test_validate_configuration_nats_enabled_missing_url(self):
        """Test configuration validation with NATS enabled but URL missing"""
        with patch("shared.constants.NATS_ENABLED", True):
            with patch("shared.constants.NATS_URL", None):
                with patch("shared.constants.MONGODB_URI", "mongodb://localhost"):
                    issues = validate_configuration()
                    assert "NATS_URL is required when NATS_ENABLED is true" in issues

    def test_validate_configuration_development(self):
        """Test configuration validation in development"""
        with patch("shared.constants.ENVIRONMENT", Environment.DEVELOPMENT):
            with patch("shared.constants.MONGODB_URI", "mongodb://localhost"):
                with patch("shared.constants.NATS_ENABLED", False):
                    issues = validate_configuration()
                    # Should have no issues in development
                    assert len(issues) == 0 or all(
                        "production" not in issue.lower() for issue in issues
                    )


class TestGetConfigSummary:
    """Test configuration summary generation"""

    def test_get_config_summary_structure(self):
        """Test that config summary has expected structure"""
        summary = get_config_summary()
        assert isinstance(summary, dict)
        assert "app" in summary
        assert "api" in summary
        assert "database" in summary
        assert "messaging" in summary
        assert "trading" in summary
        assert "exchange" in summary
        assert "monitoring" in summary

    def test_get_config_summary_app_section(self):
        """Test app section of config summary"""
        summary = get_config_summary()
        assert "name" in summary["app"]
        assert "version" in summary["app"]
        assert "environment" in summary["app"]
        assert "debug" in summary["app"]

    def test_get_config_summary_api_section(self):
        """Test API section of config summary"""
        summary = get_config_summary()
        assert "host" in summary["api"]
        assert "port" in summary["api"]
        assert "reload" in summary["api"]

    def test_get_config_summary_database_section(self):
        """Test database section of config summary"""
        summary = get_config_summary()
        assert "mongodb_url" in summary["database"]
        assert "mongodb_database" in summary["database"]

    def test_get_config_summary_messaging_section(self):
        """Test messaging section of config summary"""
        summary = get_config_summary()
        assert "nats_enabled" in summary["messaging"]
        assert "nats_url" in summary["messaging"]
        assert "nats_servers" in summary["messaging"]
        assert "nats_signal_subject" in summary["messaging"]

    def test_get_config_summary_trading_section(self):
        """Test trading section of config summary"""
        summary = get_config_summary()
        assert "simulation_enabled" in summary["trading"]
        assert "default_base_amount" in summary["trading"]
        assert "supported_symbols" in summary["trading"]

    def test_get_config_summary_exchange_section(self):
        """Test exchange section of config summary"""
        summary = get_config_summary()
        assert "binance_testnet" in summary["exchange"]
        assert "binance_api_key_set" in summary["exchange"]

    def test_get_config_summary_monitoring_section(self):
        """Test monitoring section of config summary"""
        summary = get_config_summary()
        assert "log_level" in summary["monitoring"]
        assert "prometheus_enabled" in summary["monitoring"]


class TestDeprecationWarning:
    """Test deprecation warning function"""

    def test_deprecation_warning(self):
        """Test deprecation warning is issued"""
        with pytest.warns(DeprecationWarning, match="old_function is deprecated"):
            deprecation_warning("old_function", "new_function")

    def test_deprecation_warning_custom_version(self):
        """Test deprecation warning with custom version"""
        with pytest.warns(DeprecationWarning, match="version 1.0.0"):
            deprecation_warning("old_function", "new_function", version="1.0.0")
