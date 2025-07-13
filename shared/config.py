"""
Petrosa Trading Engine - Configuration Settings

This module provides backward compatibility with the existing settings system
while leveraging the new centralized constants.

For new code, prefer importing from shared.constants directly.
"""

from pydantic_settings import BaseSettings

from shared.constants import (
    API_HOST,
    API_PORT,
    ENVIRONMENT,
    LOG_LEVEL,
    MYSQL_DATABASE,
    MYSQL_URI,
    NATS_SERVERS,
    NATS_SIGNAL_SUBJECT,
)


class Settings(BaseSettings):
    """Legacy settings class for backward compatibility"""

    # MySQL settings
    mysql_uri: str = MYSQL_URI
    mysql_database: str = MYSQL_DATABASE

    # NATS settings
    nats_servers: str = NATS_SERVERS
    nats_signal_subject: str = NATS_SIGNAL_SUBJECT

    # API settings
    api_host: str = API_HOST
    api_port: int = API_PORT

    # Environment
    environment: str = ENVIRONMENT
    log_level: str = LOG_LEVEL

    model_config = {"env_file": ".env", "extra": "ignore"}


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
    "MYSQL_URI",
    "MYSQL_DATABASE",
    # Messaging
    "NATS_SERVERS",
    "NATS_SIGNAL_SUBJECT",
    # API
    "API_HOST",
    "API_PORT",
    # Environment
    "LOG_LEVEL",
    "ENVIRONMENT",
]
