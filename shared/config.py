"""
Configuration settings for Petrosa Trading Engine
"""

from typing import Any

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings with environment variable support"""

    # Environment
    environment: str = "development"
    debug: bool = False
    log_level: str = "INFO"

    # API Configuration
    host: str = "0.0.0.0"
    port: int = 8000
    reload: bool = False

    # Binance API Configuration
    binance_api_key: str | None = None
    binance_api_secret: str | None = None
    binance_testnet: bool = True
    binance_base_url: str = "https://testnet.binance.vision"

    # MongoDB Configuration (from Kubernetes configmap and secret)
    mongodb_uri: str | None = None  # From secret: petrosa-sensitive-credentials
    mongodb_database: str | None = None  # From configmap: petrosa-common-config

    # JWT Configuration
    jwt_secret_key: str | None = None
    jwt_algorithm: str = "HS256"
    jwt_expiration_hours: int = 24

    # Trading Configuration
    simulation_enabled: bool = True
    max_position_size_pct: float = 0.1  # 10%
    max_daily_loss_pct: float = 0.05  # 5%
    max_portfolio_exposure_pct: float = 0.8  # 80%
    risk_management_enabled: bool = True

    # Redis Configuration (for caching)
    redis_url: str | None = None
    redis_password: str | None = None
    redis_db: int = 0

    # Monitoring Configuration
    prometheus_enabled: bool = True
    health_check_interval: int = 30

    # Distributed Lock Configuration
    lock_timeout_seconds: int = 60
    heartbeat_interval_seconds: int = 10

    # NATS Configuration
    nats_enabled: bool = False
    nats_url: str | None = None
    nats_servers: str | None = None
    nats_signal_subject: str = "trading.signals"

    # API Configuration (for uvicorn)
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # MySQL Configuration (legacy support)
    mysql_uri: str | None = None

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "allow"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        # Set default MongoDB URL if not provided
        if not self.mongodb_uri:
            from shared.constants import get_mongodb_connection_string
            self.mongodb_uri = get_mongodb_connection_string()

        # Set NATS configuration from constants
        from shared.constants import NATS_ENABLED, get_nats_connection_string
        self.nats_enabled = NATS_ENABLED
        if self.nats_enabled:
            self.nats_url = get_nats_connection_string()
            self.nats_servers = self.nats_url
        else:
            self.nats_servers = None

    @property
    def is_production(self) -> bool:
        """Check if running in production environment"""
        return self.environment.lower() == "production"

    @property
    def is_development(self) -> bool:
        """Check if running in development environment"""
        return self.environment.lower() == "development"

    @property
    def is_testing(self) -> bool:
        """Check if running in testing environment"""
        return self.environment.lower() == "testing"

    def get_mongodb_connection_string(self) -> str:
        """Get MongoDB connection string"""
        from shared.constants import get_mongodb_connection_string
        return self.mongodb_uri or get_mongodb_connection_string()

    def validate_required_settings(self) -> None:
        """Validate that required settings are present"""
        if self.is_production:
            if not self.binance_api_key:
                raise ValueError("BINANCE_API_KEY is required in production")
            if not self.binance_api_secret:
                raise ValueError("BINANCE_API_SECRET is required in production")
            if not self.jwt_secret_key:
                raise ValueError("JWT_SECRET_KEY is required in production")
            if not self.mongodb_uri:
                raise ValueError("MONGODB_URI is required in production")


# Global settings instance
settings = Settings()
