"""
Petrosa Trading Engine - Configuration Settings

This module provides backward compatibility with the existing settings system
while leveraging the new centralized constants.

For new code, prefer importing from shared.constants directly.
"""

from pydantic_settings import BaseSettings
from shared.constants import (
    MONGODB_URL, MONGODB_DATABASE, NATS_SERVERS, NATS_SIGNAL_SUBJECT,
    API_HOST, API_PORT, ENVIRONMENT, LOG_LEVEL
)


class Settings(BaseSettings):
    """Legacy settings class for backward compatibility"""
    
    # MongoDB settings
    mongodb_url: str = MONGODB_URL
    mongodb_database: str = MONGODB_DATABASE

    # NATS settings
    nats_servers: str = NATS_SERVERS
    nats_signal_subject: str = NATS_SIGNAL_SUBJECT

    # API settings
    api_host: str = API_HOST
    api_port: int = API_PORT

    # Environment
    environment: str = ENVIRONMENT
    log_level: str = LOG_LEVEL

    class Config:
        env_file = ".env"


# Global settings instance for backward compatibility
settings = Settings()


# Convenience imports for easy migration
def get_settings() -> Settings:
    """Get the settings instance (for backward compatibility)"""
    return settings


# Export commonly used constants for easy access
__all__ = [
    "settings",
    "get_settings",
    # Database
    "MONGODB_URL",
    "MONGODB_DATABASE", 
    "REDIS_URL",
    "REDIS_DB",
    # Messaging
    "NATS_SERVERS",
    "NATS_SIGNAL_SUBJECT",
    "NATS_QUEUE_GROUP",
    # API
    "API_HOST",
    "API_PORT",
    "API_RELOAD",
    # Trading
    "DEFAULT_BASE_AMOUNT",
    "SIMULATION_ENABLED",
    "SUPPORTED_SYMBOLS",
    # Exchange
    "BINANCE_API_KEY",
    "BINANCE_API_SECRET",
    "BINANCE_TESTNET",
    # Monitoring
    "LOG_LEVEL",
    "PROMETHEUS_ENABLED",
    # App
    "ENVIRONMENT",
    "DEBUG",
    "Environment",
    "LogLevel"
]
