"""
Petrosa Trading Engine - Centralized Constants and Configuration

This module provides a centralized location for all constants, configuration values,
secrets, and environment variables used throughout the Petrosa Trading Engine.

All modules should import constants from this file rather than defining their own.
"""

import os
import warnings
from enum import Enum
from typing import Any

# =============================================================================
# ENVIRONMENT CONFIGURATION
# =============================================================================


class Environment(str, Enum):
    """Application environments"""

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    TESTING = "testing"


class LogLevel(str, Enum):
    """Logging levels"""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


# =============================================================================
# APPLICATION CONSTANTS
# =============================================================================

# Application Info
APP_NAME = "Petrosa Trading Engine"
APP_VERSION = "0.1.0"
APP_DESCRIPTION = "Petrosa Trading Engine MVP - Signal-driven trading execution"
APP_AUTHOR = "Petrosa Team"

# Build Info
BUILD_DATE = os.getenv("BUILD_DATE", "2025-01-27")
GIT_COMMIT = os.getenv("GIT_COMMIT", "unknown")
GIT_BRANCH = os.getenv("GIT_BRANCH", "main")

# Environment
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
DEBUG = os.getenv("DEBUG", "true").lower() == "true"

# Timeouts
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))  # seconds
CONNECTION_TIMEOUT = int(os.getenv("CONNECTION_TIMEOUT", "10"))  # seconds

# Retry Configuration
MAX_RETRY_ATTEMPTS = int(os.getenv("MAX_RETRY_ATTEMPTS", "3"))
RETRY_DELAY = float(os.getenv("RETRY_DELAY", "1.0"))  # seconds
RETRY_BACKOFF_MULTIPLIER = float(os.getenv("RETRY_BACKOFF_MULTIPLIER", "2.0"))


# =============================================================================
# DATABASE CONFIGURATION
# =============================================================================

# MySQL
MYSQL_URI = os.getenv("MYSQL_URI", "mysql+pymysql://localhost:3306/petrosa")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "petrosa")
MYSQL_TIMEOUT_MS = int(os.getenv("MYSQL_TIMEOUT_MS", "5000"))
MYSQL_MAX_POOL_SIZE = int(os.getenv("MYSQL_MAX_POOL_SIZE", "10"))

# MongoDB configuration from Kubernetes configmap and secret
MONGODB_URI = os.getenv("MONGODB_URI")  # From secret: petrosa-sensitive-credentials
MONGODB_DATABASE = os.getenv(
    "MONGODB_DATABASE"
)  # From configmap: petrosa-common-config
MONGODB_TIMEOUT_MS = int(os.getenv("MONGODB_TIMEOUT_MS", "5000"))
MONGODB_MAX_POOL_SIZE = int(os.getenv("MONGODB_MAX_POOL_SIZE", "10"))


# MongoDB validation
def validate_mongodb_config() -> None:
    """Validate MongoDB configuration"""
    if not MONGODB_URI:
        raise ValueError(
            "CRITICAL: MongoDB URI is not configured. "
            "The MONGODB_URI environment variable must be set from the Kubernetes "
            "secret. Check that the secret 'petrosa-sensitive-credentials' with "
            "key 'mongodb-connection-string' exists."
        )

    if not MONGODB_DATABASE:
        raise ValueError(
            "CRITICAL: MongoDB database name is not configured. "
            "The MONGODB_DATABASE environment variable must be set from the Kubernetes "
            "configmap. Check that the configmap 'petrosa-common-config' with key "
            "'MONGODB_DATABASE' exists."
        )

    # Validate URL format
    if not MONGODB_URI.startswith(("mongodb://", "mongodb+srv://")):
        raise ValueError(
            f"CRITICAL: Invalid MongoDB URI format: {MONGODB_URI}. "
            "Must start with 'mongodb://' or 'mongodb+srv://'"
        )


def get_mongodb_connection_string() -> str:
    """Get MongoDB connection string with validation"""
    validate_mongodb_config()
    return f"{MONGODB_URI}/{MONGODB_DATABASE}"


# Redis (for future use)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
REDIS_DB = int(os.getenv("REDIS_DB", "0"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

# PostgreSQL (for future use)
POSTGRES_URL = os.getenv("POSTGRES_URL", "postgresql://localhost:5432/petrosa")
POSTGRES_USER = os.getenv("POSTGRES_USER", "petrosa")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")
POSTGRES_DB = os.getenv("POSTGRES_DB", "petrosa")


# =============================================================================
# MESSAGING CONFIGURATION
# =============================================================================

# NATS
NATS_SERVERS = os.getenv("NATS_SERVERS", "nats://localhost:4222")
NATS_ENABLED = os.getenv("NATS_ENABLED", "false").lower() == "true"
NATS_URL = os.getenv(
    "NATS_URL", "nats://nats-server:4222"
)  # From configmap: petrosa-common-config
NATS_SIGNAL_SUBJECT = os.getenv("NATS_SIGNAL_SUBJECT", "signals.trading")
NATS_QUEUE_GROUP = os.getenv("NATS_QUEUE_GROUP", "petrosa-tradeengine")
NATS_CONNECT_TIMEOUT = int(os.getenv("NATS_CONNECT_TIMEOUT", "5"))
NATS_RECONNECT_TIME_WAIT = int(os.getenv("NATS_RECONNECT_TIME_WAIT", "1"))
NATS_MAX_RECONNECT_ATTEMPTS = int(os.getenv("NATS_MAX_RECONNECT_ATTEMPTS", "10"))


# NATS validation
def validate_nats_config() -> None:
    """Validate NATS configuration"""
    if NATS_ENABLED and not NATS_URL:
        raise ValueError(
            "CRITICAL: NATS is enabled but NATS_URL is not configured. "
            "The NATS_URL environment variable must be set from the Kubernetes "
            "configmap. Check that the configmap 'petrosa-common-config' with "
            "key 'NATS_URL' exists."
        )


def get_nats_connection_string() -> str | None:
    """Get NATS connection string with validation"""
    if not NATS_ENABLED:
        return None
    validate_nats_config()
    return NATS_URL


# Kafka (for future use)
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
KAFKA_SIGNAL_TOPIC = os.getenv("KAFKA_SIGNAL_TOPIC", "trading-signals")
KAFKA_CONSUMER_GROUP = os.getenv("KAFKA_CONSUMER_GROUP", "petrosa-tradeengine")


# =============================================================================
# API CONFIGURATION
# =============================================================================

# FastAPI
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "8000"))
API_WORKERS = int(os.getenv("API_WORKERS", "1"))
API_RELOAD = os.getenv("API_RELOAD", "true").lower() == "true"

# CORS
CORS_ORIGINS = os.getenv(
    "CORS_ORIGINS", "http://localhost:3000,http://localhost:8080"
).split(",")
CORS_ALLOW_CREDENTIALS = os.getenv("CORS_ALLOW_CREDENTIALS", "true").lower() == "true"

# Rate Limiting
RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "100"))
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))  # seconds

# Security
API_KEY_HEADER = os.getenv("API_KEY_HEADER", "X-API-Key")
API_KEY_REQUIRED = os.getenv("API_KEY_REQUIRED", "false").lower() == "true"


# =============================================================================
# TRADING CONFIGURATION
# =============================================================================

# General Trading
DEFAULT_BASE_AMOUNT = float(os.getenv("DEFAULT_BASE_AMOUNT", "100.0"))
MAX_POSITION_SIZE = float(os.getenv("MAX_POSITION_SIZE", "10000.0"))
MIN_POSITION_SIZE = float(os.getenv("MIN_POSITION_SIZE", "10.0"))
DEFAULT_ORDER_TYPE = os.getenv("DEFAULT_ORDER_TYPE", "market")

# Risk Management
MAX_DAILY_TRADES = int(os.getenv("MAX_DAILY_TRADES", "100"))
MAX_DAILY_VOLUME = float(os.getenv("MAX_DAILY_VOLUME", "100000.0"))
MAX_DRAWDOWN_PERCENT = float(os.getenv("MAX_DRAWDOWN_PERCENT", "5.0"))
STOP_LOSS_DEFAULT = float(os.getenv("STOP_LOSS_DEFAULT", "2.0"))  # 2%
TAKE_PROFIT_DEFAULT = float(os.getenv("TAKE_PROFIT_DEFAULT", "5.0"))  # 5%

# Simulation
SIMULATION_ENABLED = os.getenv("SIMULATION_ENABLED", "true").lower() == "true"
SIMULATION_SLIPPAGE = float(os.getenv("SIMULATION_SLIPPAGE", "0.001"))  # 0.1%
SIMULATION_SUCCESS_RATE = float(os.getenv("SIMULATION_SUCCESS_RATE", "0.95"))  # 95%
SIMULATION_DELAY_MS = int(os.getenv("SIMULATION_DELAY_MS", "100"))

# Supported Symbols
SUPPORTED_SYMBOLS = os.getenv("SUPPORTED_SYMBOLS", "BTCUSDT,ETHUSDT,ADAUSDT").split(",")

# Order Types
SUPPORTED_ORDER_TYPES = ["market", "limit", "stop", "stop_limit"]
SUPPORTED_SIDES = ["buy", "sell"]
SUPPORTED_TIME_IN_FORCE = ["GTC", "IOC", "FOK"]

# Signal Aggregation and Conflict Resolution
SIGNAL_CONFLICT_RESOLUTION = os.getenv("SIGNAL_CONFLICT_RESOLUTION", "strongest_wins")
TIMEFRAME_CONFLICT_RESOLUTION = os.getenv(
    "TIMEFRAME_CONFLICT_RESOLUTION", "higher_timeframe_wins"
)
MAX_SIGNAL_AGE_SECONDS = int(os.getenv("MAX_SIGNAL_AGE_SECONDS", "300"))  # 5 minutes
RISK_MANAGEMENT_ENABLED = os.getenv("RISK_MANAGEMENT_ENABLED", "true").lower() == "true"
MAX_POSITION_SIZE_PCT = float(os.getenv("MAX_POSITION_SIZE_PCT", "0.1"))  # 10%
MAX_DAILY_LOSS_PCT = float(os.getenv("MAX_DAILY_LOSS_PCT", "0.05"))  # 5%

# Strategy Weights (for weighted average conflict resolution)
STRATEGY_WEIGHTS = {
    "momentum_strategy": float(os.getenv("MOMENTUM_STRATEGY_WEIGHT", "1.0")),
    "mean_reversion_strategy": float(
        os.getenv("MEAN_REVERSION_STRATEGY_WEIGHT", "0.8")
    ),
    "ml_strategy": float(os.getenv("ML_STRATEGY_WEIGHT", "1.2")),
    "llm_strategy": float(os.getenv("LLM_STRATEGY_WEIGHT", "1.5")),
    "default": float(os.getenv("DEFAULT_STRATEGY_WEIGHT", "1.0")),
}

# Timeframe Weights (for timeframe-based conflict resolution)
TIMEFRAME_WEIGHTS = {
    "tick": float(os.getenv("TICK_WEIGHT", "0.3")),
    "1m": float(os.getenv("MINUTE_1_WEIGHT", "0.5")),
    "3m": float(os.getenv("MINUTE_3_WEIGHT", "0.6")),
    "5m": float(os.getenv("MINUTE_5_WEIGHT", "0.7")),
    "15m": float(os.getenv("MINUTE_15_WEIGHT", "0.8")),
    "30m": float(os.getenv("MINUTE_30_WEIGHT", "0.9")),
    "1h": float(os.getenv("HOUR_1_WEIGHT", "1.0")),
    "2h": float(os.getenv("HOUR_2_WEIGHT", "1.1")),
    "4h": float(os.getenv("HOUR_4_WEIGHT", "1.2")),
    "6h": float(os.getenv("HOUR_6_WEIGHT", "1.3")),
    "8h": float(os.getenv("HOUR_8_WEIGHT", "1.4")),
    "12h": float(os.getenv("HOUR_12_WEIGHT", "1.5")),
    "1d": float(os.getenv("DAY_1_WEIGHT", "1.6")),
    "3d": float(os.getenv("DAY_3_WEIGHT", "1.7")),
    "1w": float(os.getenv("WEEK_1_WEIGHT", "1.8")),
    "1M": float(os.getenv("MONTH_1_WEIGHT", "2.0")),
}

# Strategy Mode Configuration
DETERMINISTIC_MODE_ENABLED = (
    os.getenv("DETERMINISTIC_MODE_ENABLED", "true").lower() == "true"
)
ML_LIGHT_MODE_ENABLED = os.getenv("ML_LIGHT_MODE_ENABLED", "true").lower() == "true"
LLM_REASONING_MODE_ENABLED = (
    os.getenv("LLM_REASONING_MODE_ENABLED", "true").lower() == "true"
)


# =============================================================================
# EXCHANGE CONFIGURATION
# =============================================================================

# Binance
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")
BINANCE_TESTNET = os.getenv("BINANCE_TESTNET", "true").lower() == "true"
BINANCE_BASE_URL = os.getenv(
    "BINANCE_BASE_URL",
    "https://testnet.binance.vision" if BINANCE_TESTNET else "https://api.binance.com",
)
BINANCE_WS_URL = os.getenv(
    "BINANCE_WS_URL",
    "wss://testnet.binance.vision/ws"
    if BINANCE_TESTNET
    else "wss://stream.binance.com:9443/ws",
)
BINANCE_TIMEOUT = int(os.getenv("BINANCE_TIMEOUT", "10"))
BINANCE_RETRY_ATTEMPTS = int(os.getenv("BINANCE_RETRY_ATTEMPTS", "3"))

# Coinbase (for future use)
COINBASE_API_KEY = os.getenv("COINBASE_API_KEY", "")
COINBASE_API_SECRET = os.getenv("COINBASE_API_SECRET", "")
COINBASE_PASSPHRASE = os.getenv("COINBASE_PASSPHRASE", "")
COINBASE_SANDBOX = os.getenv("COINBASE_SANDBOX", "true").lower() == "true"

# Kraken (for future use)
KRAKEN_API_KEY = os.getenv("KRAKEN_API_KEY", "")
KRAKEN_API_SECRET = os.getenv("KRAKEN_API_SECRET", "")
KRAKEN_SANDBOX = os.getenv("KRAKEN_SANDBOX", "true").lower() == "true"


# =============================================================================
# MONITORING CONFIGURATION
# =============================================================================

# Prometheus
PROMETHEUS_ENABLED = os.getenv("PROMETHEUS_ENABLED", "true").lower() == "true"
PROMETHEUS_PORT = int(os.getenv("PROMETHEUS_PORT", "9090"))
PROMETHEUS_PATH = os.getenv("PROMETHEUS_PATH", "/metrics")

# Health Checks
HEALTH_CHECK_INTERVAL = int(os.getenv("HEALTH_CHECK_INTERVAL", "30"))  # seconds
HEALTH_CHECK_TIMEOUT = int(os.getenv("HEALTH_CHECK_TIMEOUT", "5"))  # seconds

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FORMAT = os.getenv(
    "LOG_FORMAT", "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
LOG_FILE = os.getenv("LOG_FILE", "")
LOG_MAX_SIZE = int(os.getenv("LOG_MAX_SIZE", "10485760"))  # 10MB
LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "5"))

# Tracing
JAEGER_ENABLED = os.getenv("JAEGER_ENABLED", "false").lower() == "true"
JAEGER_HOST = os.getenv("JAEGER_HOST", "localhost")
JAEGER_PORT = int(os.getenv("JAEGER_PORT", "6831"))


# =============================================================================
# SECURITY CONFIGURATION
# =============================================================================

# JWT
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-secret-key-change-in-production")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = int(
    os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "30")
)
JWT_REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("JWT_REFRESH_TOKEN_EXPIRE_DAYS", "7"))

# API Keys
API_KEYS = {
    key.strip(): value.strip()
    for key, value in [
        pair.split("=") for pair in os.getenv("API_KEYS", "").split(",") if "=" in pair
    ]
}

# Rate Limiting
RATE_LIMIT_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "true").lower() == "true"
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))  # seconds
RATE_LIMIT_MAX_REQUESTS = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "100"))


# =============================================================================
# CACHE CONFIGURATION
# =============================================================================

# Redis Cache
CACHE_ENABLED = os.getenv("CACHE_ENABLED", "true").lower() == "true"
CACHE_TTL = int(os.getenv("CACHE_TTL", "300"))  # 5 minutes
CACHE_MAX_SIZE = int(os.getenv("CACHE_MAX_SIZE", "1000"))

# Market Data Cache
MARKET_DATA_CACHE_TTL = int(os.getenv("MARKET_DATA_CACHE_TTL", "60"))  # 1 minute
ORDER_BOOK_CACHE_TTL = int(os.getenv("ORDER_BOOK_CACHE_TTL", "5"))  # 5 seconds


# =============================================================================
# SIGNAL AGGREGATION AND STRATEGY MODES
# =============================================================================

# Signal Aggregation
MAX_SIGNAL_AGE_SECONDS = int(os.getenv("MAX_SIGNAL_AGE_SECONDS", "300"))  # 5 minutes
SIGNAL_CONFLICT_RESOLUTION = os.getenv(
    "SIGNAL_CONFLICT_RESOLUTION", "strongest_wins"
)  # strongest_wins, first_come_first_served, manual_review
SIGNAL_AGGREGATION_ENABLED = (
    os.getenv("SIGNAL_AGGREGATION_ENABLED", "true").lower() == "true"
)

# Strategy Modes
DETERMINISTIC_MODE_ENABLED = (
    os.getenv("DETERMINISTIC_MODE_ENABLED", "true").lower() == "true"
)
ML_LIGHT_MODE_ENABLED = os.getenv("ML_LIGHT_MODE_ENABLED", "false").lower() == "true"
LLM_REASONING_MODE_ENABLED = (
    os.getenv("LLM_REASONING_MODE_ENABLED", "false").lower() == "true"
)

# Risk Management
RISK_MANAGEMENT_ENABLED = os.getenv("RISK_MANAGEMENT_ENABLED", "true").lower() == "true"
MAX_POSITION_SIZE_PCT = float(os.getenv("MAX_POSITION_SIZE_PCT", "0.1"))  # 10%
MAX_DAILY_LOSS_PCT = float(os.getenv("MAX_DAILY_LOSS_PCT", "0.05"))  # 5%
MAX_PORTFOLIO_EXPOSURE_PCT = float(
    os.getenv("MAX_PORTFOLIO_EXPOSURE_PCT", "0.8")
)  # 80%

# Conditional Orders
CONDITIONAL_ORDER_TIMEOUT = int(
    os.getenv("CONDITIONAL_ORDER_TIMEOUT", "3600")
)  # 1 hour
PRICE_MONITORING_INTERVAL = int(
    os.getenv("PRICE_MONITORING_INTERVAL", "5")
)  # 5 seconds

# Strategy Weights (for deterministic mode)
STRATEGY_WEIGHTS = {
    key.strip(): float(value.strip())
    for key, value in [
        pair.split("=")
        for pair in os.getenv(
            "STRATEGY_WEIGHTS", "momentum_v1=1.0,mean_reversion_v1=0.8,arbitrage_v1=1.2"
        ).split(",")
        if "=" in pair
    ]
}

# ML Model Configuration
ML_MODEL_PATH = os.getenv("ML_MODEL_PATH", "models/")
ML_MODEL_UPDATE_INTERVAL = int(os.getenv("ML_MODEL_UPDATE_INTERVAL", "3600"))  # 1 hour
ML_FEATURE_CACHE_TTL = int(os.getenv("ML_FEATURE_CACHE_TTL", "300"))  # 5 minutes

# LLM Configuration
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai")  # openai, anthropic, local
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4")  # gpt-4, gpt-3.5-turbo, claude-3, etc.
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "2000"))
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.1"))
LLM_REASONING_TIMEOUT = int(os.getenv("LLM_REASONING_TIMEOUT", "30"))  # seconds


# =============================================================================
# ERROR CODES AND MESSAGES
# =============================================================================

# General Errors
ERROR_UNKNOWN = "UNKNOWN_ERROR"
ERROR_VALIDATION = "VALIDATION_ERROR"
ERROR_AUTHENTICATION = "AUTHENTICATION_ERROR"
ERROR_AUTHORIZATION = "AUTHORIZATION_ERROR"
ERROR_RATE_LIMIT = "RATE_LIMIT_ERROR"

# Trading Errors
ERROR_INSUFFICIENT_BALANCE = "INSUFFICIENT_BALANCE"
ERROR_INVALID_ORDER = "INVALID_ORDER"
ERROR_ORDER_NOT_FOUND = "ORDER_NOT_FOUND"
ERROR_EXCHANGE = "EXCHANGE_ERROR"
ERROR_SYMBOL_NOT_SUPPORTED = "SYMBOL_NOT_SUPPORTED"

# System Errors
ERROR_DATABASE = "DATABASE_ERROR"
ERROR_MESSAGING = "MESSAGING_ERROR"
ERROR_CONFIGURATION = "CONFIGURATION_ERROR"
ERROR_SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"


# =============================================================================
# ENVIRONMENT-SPECIFIC OVERRIDES
# =============================================================================

# Production overrides
if ENVIRONMENT == Environment.PRODUCTION:
    LOG_LEVEL = "WARNING"
    API_RELOAD = False
    SIMULATION_ENABLED = False
    JWT_SECRET_KEY = (
        os.getenv("JWT_SECRET_KEY") or "your-secret-key-change-in-production"
    )  # Must be set in production
    BINANCE_TESTNET = False
    COINBASE_SANDBOX = False
    KRAKEN_SANDBOX = False

# Staging overrides
elif ENVIRONMENT == Environment.STAGING:
    LOG_LEVEL = "INFO"
    API_RELOAD = False
    SIMULATION_ENABLED = True  # Use simulation in staging

# Testing overrides
elif ENVIRONMENT == Environment.TESTING:
    LOG_LEVEL = "DEBUG"
    API_RELOAD = True
    SIMULATION_ENABLED = True
    MONGODB_DATABASE = "petrosa_test"
    REDIS_DB = 1


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================


def get_config_summary() -> dict[str, Any]:
    """Get a summary of all configuration for debugging/logging"""
    return {
        "app": {
            "name": APP_NAME,
            "version": APP_VERSION,
            "environment": ENVIRONMENT,
            "debug": DEBUG,
        },
        "api": {
            "host": API_HOST,
            "port": API_PORT,
            "reload": API_RELOAD,
        },
        "database": {
            "mongodb_url": MONGODB_URI,
            "mongodb_database": MONGODB_DATABASE,
        },
        "messaging": {
            "nats_enabled": NATS_ENABLED,
            "nats_url": NATS_URL,
            "nats_servers": NATS_SERVERS,
            "nats_signal_subject": NATS_SIGNAL_SUBJECT,
        },
        "trading": {
            "simulation_enabled": SIMULATION_ENABLED,
            "default_base_amount": DEFAULT_BASE_AMOUNT,
            "supported_symbols": SUPPORTED_SYMBOLS,
        },
        "exchange": {
            "binance_testnet": BINANCE_TESTNET,
            "binance_api_key_set": bool(BINANCE_API_KEY),
        },
        "monitoring": {
            "log_level": LOG_LEVEL,
            "prometheus_enabled": PROMETHEUS_ENABLED,
        },
    }


def validate_configuration() -> list[str]:
    """Validate configuration and return list of issues"""
    issues = []

    # Check required production settings
    if ENVIRONMENT == Environment.PRODUCTION:
        if not BINANCE_API_KEY:
            issues.append("BINANCE_API_KEY is required in production")
        if not BINANCE_API_SECRET:
            issues.append("BINANCE_API_SECRET is required in production")
        if JWT_SECRET_KEY == "your-secret-key-change-in-production":
            issues.append("JWT_SECRET_KEY must be changed in production")

    # Check database connectivity
    if not MONGODB_URI:
        issues.append("MONGODB_URI is required")

    # Check NATS connectivity
    if NATS_ENABLED and not NATS_URL:
        issues.append("NATS_URL is required when NATS_ENABLED is true")

    return issues


# =============================================================================
# DEPRECATION WARNINGS
# =============================================================================


def deprecation_warning(old_name: str, new_name: str, version: str = "0.2.0") -> None:
    """Helper function to show deprecation warnings"""
    warnings.warn(
        f"{old_name} is deprecated and will be removed in version {version}. "
        f"Use {new_name} instead.",
        DeprecationWarning,
        stacklevel=2,
    )
