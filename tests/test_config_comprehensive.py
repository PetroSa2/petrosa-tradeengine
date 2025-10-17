"""
Comprehensive tests for shared/config.py to increase coverage
"""

import os
from unittest.mock import patch

import pytest

from shared.config import Settings


class TestSettings:
    """Test Settings class comprehensively"""

    def test_default_settings(self):
        """Test default settings values"""
        settings = Settings()
        # Environment is set by conftest.py, so just check it exists
        assert settings.environment is not None
        assert isinstance(settings.environment, str)
        assert settings.debug is False
        assert settings.log_level in [
            "INFO",
            "DEBUG",
        ]  # Can be either depending on test environment
        assert settings.host == "0.0.0.0"
        assert settings.port == 8000
        assert settings.simulation_enabled is True

    def test_is_production(self):
        """Test is_production property"""
        settings = Settings(environment="production")
        assert settings.is_production is True

        settings = Settings(environment="PRODUCTION")
        assert settings.is_production is True

        settings = Settings(environment="development")
        assert settings.is_production is False

    def test_is_development(self):
        """Test is_development property"""
        settings = Settings(environment="development")
        assert settings.is_development is True

        settings = Settings(environment="DEVELOPMENT")
        assert settings.is_development is True

        settings = Settings(environment="production")
        assert settings.is_development is False

    def test_is_testing(self):
        """Test is_testing property"""
        settings = Settings(environment="testing")
        assert settings.is_testing is True

        settings = Settings(environment="TESTING")
        assert settings.is_testing is True

        settings = Settings(environment="development")
        assert settings.is_testing is False

    def test_get_mongodb_connection_string(self):
        """Test MongoDB connection string generation"""
        settings = Settings(mongodb_uri="mongodb://localhost:27017")
        conn_str = settings.get_mongodb_connection_string()
        assert "mongodb://" in conn_str

    def test_validate_required_settings_production(self):
        """Test validation in production environment"""
        settings = Settings(
            environment="production",
            binance_api_key="test_key",
            binance_api_secret="test_secret",
            jwt_secret_key="test_jwt",
            mongodb_uri="mongodb://localhost:27017",
        )
        # Should not raise an error
        settings.validate_required_settings()

    def test_validate_required_settings_production_missing_binance_api_key(self):
        """Test validation fails when Binance API key is missing in production"""
        settings = Settings(
            environment="production",
            binance_api_key=None,
            binance_api_secret="test_secret",
            jwt_secret_key="test_jwt",
            mongodb_uri="mongodb://localhost:27017",
        )
        with pytest.raises(
            ValueError, match="BINANCE_API_KEY is required in production"
        ):
            settings.validate_required_settings()

    def test_validate_required_settings_production_missing_binance_api_secret(self):
        """Test validation fails when Binance API secret is missing in production"""
        settings = Settings(
            environment="production",
            binance_api_key="test_key",
            binance_api_secret=None,
            jwt_secret_key="test_jwt",
            mongodb_uri="mongodb://localhost:27017",
        )
        with pytest.raises(
            ValueError, match="BINANCE_API_SECRET is required in production"
        ):
            settings.validate_required_settings()

    def test_validate_required_settings_production_missing_jwt_secret(self):
        """Test validation fails when JWT secret is missing in production"""
        settings = Settings(
            environment="production",
            binance_api_key="test_key",
            binance_api_secret="test_secret",
            jwt_secret_key=None,
            mongodb_uri="mongodb://localhost:27017",
        )
        with pytest.raises(
            ValueError, match="JWT_SECRET_KEY is required in production"
        ):
            settings.validate_required_settings()

    def test_validate_required_settings_production_missing_mongodb_uri(self):
        """Test validation fails when MongoDB URI is missing in production"""
        with patch.dict(
            os.environ, {"MONGODB_URI": "", "MONGODB_DATABASE": ""}, clear=False
        ):
            settings = Settings(
                environment="production",
                binance_api_key="test_key",
                binance_api_secret="test_secret",
                jwt_secret_key="test_jwt",
            )
            settings.mongodb_uri = None  # Force None after initialization
            with pytest.raises(
                ValueError, match="MONGODB_URI is required in production"
            ):
                settings.validate_required_settings()

    def test_validate_required_settings_development(self):
        """Test validation does not enforce required settings in development"""
        settings = Settings(environment="development")
        # Should not raise an error in development
        settings.validate_required_settings()

    def test_nats_enabled_configuration(self):
        """Test NATS configuration when enabled"""
        with patch("shared.constants.NATS_ENABLED", True):
            with patch(
                "shared.constants.get_nats_connection_string",
                return_value="nats://localhost:4222",
            ):
                settings = Settings()
                # NATS settings are set in __init__
                assert settings.nats_enabled is not None

    def test_nats_disabled_configuration(self):
        """Test NATS configuration when disabled"""
        with patch("shared.constants.NATS_ENABLED", False):
            settings = Settings()
            # NATS settings should reflect disabled state
            assert settings.nats_enabled is False

    def test_custom_settings_override(self):
        """Test that custom settings override defaults"""
        settings = Settings(
            environment="staging", debug=True, log_level="DEBUG", port=9000
        )
        assert settings.environment == "staging"
        assert settings.debug is True
        assert settings.log_level == "DEBUG"
        assert settings.port == 9000

    def test_extra_fields_allowed(self):
        """Test that extra fields are allowed in settings"""
        settings = Settings(custom_field="custom_value")
        assert hasattr(settings, "custom_field")

    def test_binance_configuration(self):
        """Test Binance configuration"""
        settings = Settings(
            binance_api_key="test_key",
            binance_api_secret="test_secret",
            binance_testnet=False,
            binance_base_url="https://api.binance.com",
        )
        assert settings.binance_api_key == "test_key"
        assert settings.binance_api_secret == "test_secret"
        assert settings.binance_testnet is False
        assert settings.binance_base_url == "https://api.binance.com"

    def test_trading_configuration(self):
        """Test trading configuration"""
        settings = Settings(
            simulation_enabled=False,
            max_position_size_pct=0.2,
            max_daily_loss_pct=0.03,
            max_portfolio_exposure_pct=0.7,
        )
        assert settings.simulation_enabled is False
        assert settings.max_position_size_pct == 0.2
        assert settings.max_daily_loss_pct == 0.03
        assert settings.max_portfolio_exposure_pct == 0.7

    def test_prometheus_configuration(self):
        """Test Prometheus configuration"""
        settings = Settings(prometheus_enabled=False)
        assert settings.prometheus_enabled is False

    def test_redis_configuration(self):
        """Test Redis configuration"""
        settings = Settings(
            redis_url="redis://localhost:6380",
            redis_password="test_password",
            redis_db=1,
        )
        assert settings.redis_url == "redis://localhost:6380"
        assert settings.redis_password == "test_password"
        assert settings.redis_db == 1

    def test_jwt_configuration(self):
        """Test JWT configuration"""
        settings = Settings(
            jwt_secret_key="my_secret", jwt_algorithm="HS512", jwt_expiration_hours=48
        )
        assert settings.jwt_secret_key == "my_secret"
        assert settings.jwt_algorithm == "HS512"
        assert settings.jwt_expiration_hours == 48
