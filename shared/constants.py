"""
Petrosa Trading Engine - Centralized Constants and Configuration
"""

import logging
import os
import warnings
from datetime import datetime, timezone

try:
    from datetime import UTC
except ImportError:
    from datetime import timezone
    UTC = timezone.utc  # noqa: UP017
from typing import TYPE_CHECKING, Any

try:
    from enum import StrEnum
except ImportError:
    from enum import Enum
    class StrEnum(str, Enum):
        def __str__(self):
            return str(self.value)

# Configure local logger for validation warnings
logger = logging.getLogger(__name__)

# Timezone
UTC = UTC

class Environment(StrEnum):
    """Application environments"""
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    TESTING = "testing"

class LogLevel(StrEnum):
    """Logging levels"""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

# ... rest of the constants ...
APP_NAME = "Petrosa Trading Engine"
APP_VERSION = "0.1.0"
APP_DESCRIPTION = "Petrosa Trading Engine MVP - Signal-driven trading execution"
APP_AUTHOR = "Petrosa Team"
BUILD_DATE = os.getenv("BUILD_DATE", "2025-01-27")
GIT_COMMIT = os.getenv("GIT_COMMIT", "unknown")
GIT_BRANCH = os.getenv("GIT_BRANCH", "main")
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
DEBUG = os.getenv("DEBUG", "true").lower() == "true"
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))
CONNECTION_TIMEOUT = int(os.getenv("CONNECTION_TIMEOUT", "10"))
MAX_RETRY_ATTEMPTS = int(os.getenv("MAX_RETRY_ATTEMPTS", "3"))
RETRY_DELAY = float(os.getenv("RETRY_DELAY", "1.0"))
RETRY_BACKOFF_MULTIPLIER = float(os.getenv("RETRY_BACKOFF_MULTIPLIER", "2.0"))
DATA_MANAGER_URL = os.getenv("DATA_MANAGER_URL", "http://petrosa-data-manager:8000")
DATA_MANAGER_TIMEOUT = int(os.getenv("DATA_MANAGER_TIMEOUT", "30"))
DATA_MANAGER_MAX_RETRIES = int(os.getenv("DATA_MANAGER_MAX_RETRIES", "3"))
DATA_MANAGER_DATABASE = os.getenv("DATA_MANAGER_DATABASE", "mongodb")
USE_DATA_MANAGER = os.getenv("USE_DATA_MANAGER", "true").lower() == "true"
MYSQL_URI = os.getenv("MYSQL_URI", "mysql+pymysql://localhost:3306/petrosa")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "petrosa")
MYSQL_TIMEOUT_MS = int(os.getenv("MYSQL_TIMEOUT_MS", "5000"))
MYSQL_MAX_POOL_SIZE = int(os.getenv("MYSQL_MAX_POOL_SIZE", "10"))
MONGODB_URI = os.getenv("MONGODB_URI")
MONGODB_DATABASE = os.getenv("MONGODB_DATABASE")
MONGODB_TIMEOUT_MS = int(os.getenv("MONGODB_TIMEOUT_MS", "5000"))
MONGODB_MAX_POOL_SIZE = int(os.getenv("MONGODB_MAX_POOL_SIZE", "10"))

def validate_mongodb_config() -> None:
    if not MONGODB_URI:
        logger.warning("MongoDB URI not configured; data persistence may be limited.")
        return
    if not MONGODB_DATABASE:
        logger.warning("MongoDB database not configured; using default.")
        return
    if MONGODB_URI and not MONGODB_URI.startswith(("mongodb://", "mongodb+srv://")):
        raise ValueError(f"CRITICAL: Invalid MongoDB URI format: {MONGODB_URI}")

def get_mongodb_connection_string() -> str:
    validate_mongodb_config()
    if not MONGODB_URI:
        return ""
    return f"{MONGODB_URI}/{MONGODB_DATABASE}"

NATS_SERVERS = os.getenv("NATS_SERVERS", "nats://localhost:4222")
NATS_ENABLED = os.getenv("NATS_ENABLED", "false").lower() == "true"
NATS_URL = os.getenv("NATS_URL", "nats://nats-server:4222")
NATS_TOPIC_SIGNALS = os.getenv("NATS_TOPIC_SIGNALS", "signals.trading.*")
NATS_TOPIC_HEARTBEAT = os.getenv("NATS_TOPIC_HEARTBEAT", "cio.heartbeat")
NATS_QUEUE_GROUP = os.getenv("NATS_QUEUE_GROUP", "petrosa-tradeengine")
NATS_CONNECT_TIMEOUT = int(os.getenv("NATS_CONNECT_TIMEOUT", "5"))
NATS_RECONNECT_TIME_WAIT = int(os.getenv("NATS_RECONNECT_TIME_WAIT", "1"))
NATS_MAX_RECONNECT_ATTEMPTS = int(os.getenv("NATS_MAX_RECONNECT_ATTEMPTS", "10"))

def validate_nats_config() -> None:
    if NATS_ENABLED and not NATS_URL:
        raise ValueError("CRITICAL: NATS is enabled but NATS_URL is not configured.")

def get_nats_connection_string() -> str | None:
    if not NATS_ENABLED:
        return None
    validate_nats_config()
    return NATS_URL

API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "8000"))
API_WORKERS = int(os.getenv("API_WORKERS", "1"))
API_RELOAD = os.getenv("API_RELOAD", "true").lower() == "true"
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:8080").split(",")
CORS_ALLOW_CREDENTIALS = os.getenv("CORS_ALLOW_CREDENTIALS", "true").lower() == "true"
RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "100"))
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))
API_KEY_HEADER = os.getenv("API_KEY_HEADER", "X-API-Key")
API_KEY_REQUIRED = os.getenv("API_KEY_REQUIRED", "false").lower() == "true"
DEFAULT_BASE_AMOUNT = float(os.getenv("DEFAULT_BASE_AMOUNT", "100.0"))
MAX_POSITION_SIZE = float(os.getenv("MAX_POSITION_SIZE", "10000.0"))
MIN_POSITION_SIZE = float(os.getenv("MIN_POSITION_SIZE", "10.0"))
DEFAULT_ORDER_TYPE = os.getenv("DEFAULT_ORDER_TYPE", "market")
MAX_DAILY_TRADES = int(os.getenv("MAX_DAILY_TRADES", "100"))
MAX_DAILY_VOLUME = float(os.getenv("MAX_DAILY_VOLUME", "100000.0"))
MAX_DRAWDOWN_PERCENT = float(os.getenv("MAX_DRAWDOWN_PERCENT", "5.0"))
STOP_LOSS_DEFAULT = float(os.getenv("STOP_LOSS_DEFAULT", "2.0"))
TAKE_PROFIT_DEFAULT = float(os.getenv("TAKE_PROFIT_DEFAULT", "5.0"))
SIMULATION_ENABLED = os.getenv("SIMULATION_ENABLED", "true").lower() == "true"
SIMULATION_SLIPPAGE = float(os.getenv("SIMULATION_SLIPPAGE", "0.001"))
SIMULATION_SUCCESS_RATE = float(os.getenv("SIMULATION_SUCCESS_RATE", "0.95"))
SIMULATION_DELAY_MS = int(os.getenv("SIMULATION_DELAY_MS", "100"))
HEDGE_MODE_ENABLED = os.getenv("HEDGE_MODE_ENABLED", "true").lower() == "true"
POSITION_MODE = os.getenv("POSITION_MODE", "hedge")
POSITION_MODE_AWARE_CONFLICTS = os.getenv("POSITION_MODE_AWARE_CONFLICTS", "true").lower() == "true"
SAME_DIRECTION_CONFLICT_RESOLUTION = os.getenv("SAME_DIRECTION_CONFLICT_RESOLUTION", "accumulate")
SUPPORTED_SYMBOLS = os.getenv("SUPPORTED_SYMBOLS", "BTCUSDT,ETHUSDT,BNBUSDT,ADAUSDT,DOTUSDT,LINKUSDT,LTCUSDT,BCHUSDT,XLMUSDT,XRPUSDT").split(",")
SUPPORTED_TIMEFRAMES = os.getenv("SUPPORTED_TIMEFRAMES", "5m,15m,30m,1h,1d").split(",")
SUPPORTED_ORDER_TYPES = ["market", "limit", "stop", "stop_limit"]
SUPPORTED_SIDES = ["buy", "sell"]
SUPPORTED_TIME_IN_FORCE = ["GTC", "IOC", "FOK"]
SIGNAL_CONFLICT_RESOLUTION = os.getenv("SIGNAL_CONFLICT_RESOLUTION", "strongest_wins")
TIMEFRAME_CONFLICT_RESOLUTION = os.getenv("TIMEFRAME_CONFLICT_RESOLUTION", "higher_timeframe_wins")
MAX_SIGNAL_AGE_SECONDS = int(os.getenv("MAX_SIGNAL_AGE_SECONDS", "300"))
RISK_MANAGEMENT_ENABLED = os.getenv("RISK_MANAGEMENT_ENABLED", "true").lower() == "true"
MAX_POSITION_SIZE_PCT = float(os.getenv("MAX_POSITION_SIZE_PCT", "0.1"))
MAX_DAILY_LOSS_PCT = float(os.getenv("MAX_DAILY_LOSS_PCT", "0.05"))
MAX_ACCUMULATIONS_PER_POSITION = int(os.getenv("MAX_ACCUMULATIONS_PER_POSITION", "10"))
ACCUMULATION_COOLDOWN_SECONDS = int(os.getenv("ACCUMULATION_COOLDOWN_SECONDS", "300"))
MAX_TOTAL_POSITIONS = int(os.getenv("MAX_TOTAL_POSITIONS", "10"))
STRATEGY_WEIGHTS = {"default": 1.0}
TIMEFRAME_WEIGHTS = {"1h": 1.0}
DETERMINISTIC_MODE_ENABLED = os.getenv("DETERMINISTIC_MODE_ENABLED", "true").lower() == "true"
ML_LIGHT_MODE_ENABLED = os.getenv("ML_LIGHT_MODE_ENABLED", "true").lower() == "true"
LLM_REASONING_MODE_ENABLED = os.getenv("LLM_REASONING_MODE_ENABLED", "true").lower() == "true"
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")
BINANCE_TESTNET = os.getenv("BINANCE_TESTNET", "true").lower() == "true"
BINANCE_BASE_URL = os.getenv("BINANCE_BASE_URL", "https://testnet.binance.vision" if BINANCE_TESTNET else "https://api.binance.com")
BINANCE_FUTURES_BASE_URL = os.getenv("BINANCE_FUTURES_BASE_URL", "https://testnet.binancefuture.com" if BINANCE_TESTNET else "https://fapi.binance.com")
BINANCE_WS_URL = os.getenv("BINANCE_WS_URL", "wss://testnet.binance.vision/ws" if BINANCE_TESTNET else "wss://stream.binance.com:9443/ws")
BINANCE_TIMEOUT = int(os.getenv("BINANCE_TIMEOUT", "10"))
BINANCE_RETRY_ATTEMPTS = int(os.getenv("BINANCE_RETRY_ATTEMPTS", "3"))
PROMETHEUS_ENABLED = os.getenv("PROMETHEUS_ENABLED", "true").lower() == "true"
PROMETHEUS_PORT = int(os.getenv("PROMETHEUS_PORT", "9090"))
PROMETHEUS_PATH = os.getenv("PROMETHEUS_PATH", "/metrics")
HEALTH_CHECK_INTERVAL = int(os.getenv("HEALTH_CHECK_INTERVAL", "30"))
HEALTH_CHECK_TIMEOUT = int(os.getenv("HEALTH_CHECK_TIMEOUT", "5"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FORMAT = os.getenv("LOG_FORMAT", "%(asctime)s - %(name)s - %(levelname)s - %(message)s")
LOG_FILE = os.getenv("LOG_FILE", "")
LOG_MAX_SIZE = int(os.getenv("LOG_MAX_SIZE", "10485760"))
LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "5"))
JAEGER_ENABLED = os.getenv("JAEGER_ENABLED", "false").lower() == "true"
JAEGER_HOST = os.getenv("JAEGER_HOST", "localhost")
JAEGER_PORT = int(os.getenv("JAEGER_PORT", "6831"))
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-secret-key-change-in-production")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
JWT_REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("JWT_REFRESH_TOKEN_EXPIRE_DAYS", "7"))
API_KEYS = {}
RATE_LIMIT_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "true").lower() == "true"
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))
RATE_LIMIT_MAX_REQUESTS = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "100"))
CACHE_ENABLED = os.getenv("CACHE_ENABLED", "true").lower() == "true"
CACHE_TTL = int(os.getenv("CACHE_TTL", "300"))
CACHE_MAX_SIZE = int(os.getenv("CACHE_MAX_SIZE", "1000"))
MARKET_DATA_CACHE_TTL = int(os.getenv("MARKET_DATA_CACHE_TTL", "60"))
ORDER_BOOK_CACHE_TTL = int(os.getenv("ORDER_BOOK_CACHE_TTL", "5"))
MAX_SIGNAL_AGE_SECONDS = int(os.getenv("MAX_SIGNAL_AGE_SECONDS", "300"))
SIGNAL_CONFLICT_RESOLUTION = os.getenv("SIGNAL_CONFLICT_RESOLUTION", "strongest_wins")
SIGNAL_AGGREGATION_ENABLED = os.getenv("SIGNAL_AGGREGATION_ENABLED", "true").lower() == "true"
RISK_MANAGEMENT_ENABLED = os.getenv("RISK_MANAGEMENT_ENABLED", "true").lower() == "true"
MAX_POSITION_SIZE_PCT = float(os.getenv("MAX_POSITION_SIZE_PCT", "0.1"))
MAX_DAILY_LOSS_PCT = float(os.getenv("MAX_DAILY_LOSS_PCT", "0.05"))
MAX_PORTFOLIO_EXPOSURE_PCT = float(os.getenv("MAX_PORTFOLIO_EXPOSURE_PCT", "0.8"))
CONDITIONAL_ORDER_TIMEOUT = int(os.getenv("CONDITIONAL_ORDER_TIMEOUT", "3600"))
PRICE_MONITORING_INTERVAL = int(os.getenv("PRICE_MONITORING_INTERVAL", "5"))
ML_MODEL_PATH = os.getenv("ML_MODEL_PATH", "models/")
ML_MODEL_UPDATE_INTERVAL = int(os.getenv("ML_MODEL_UPDATE_INTERVAL", "3600"))
ML_FEATURE_CACHE_TTL = int(os.getenv("ML_FEATURE_CACHE_TTL", "300"))
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "2000"))
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.1"))
LLM_REASONING_TIMEOUT = int(os.getenv("LLM_REASONING_TIMEOUT", "30"))
ERROR_UNKNOWN = "UNKNOWN_ERROR"
ERROR_VALIDATION = "VALIDATION_ERROR"
ERROR_AUTHENTICATION = "AUTHENTICATION_ERROR"
ERROR_AUTHORIZATION = "AUTHORIZATION_ERROR"
ERROR_RATE_LIMIT = "RATE_LIMIT_ERROR"
ERROR_INSUFFICIENT_BALANCE = "INSUFFICIENT_BALANCE"
ERROR_INVALID_ORDER = "INVALID_ORDER"
ERROR_ORDER_NOT_FOUND = "ORDER_NOT_FOUND"
ERROR_EXCHANGE = "EXCHANGE_ERROR"
ERROR_SYMBOL_NOT_SUPPORTED = "SYMBOL_NOT_SUPPORTED"
ERROR_DATABASE = "DATABASE_ERROR"
ERROR_MESSAGING = "MESSAGING_ERROR"
ERROR_CONFIGURATION = "CONFIGURATION_ERROR"
ERROR_SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"

if ENVIRONMENT == Environment.PRODUCTION:
    LOG_LEVEL = "WARNING"
    API_RELOAD = False
    SIMULATION_ENABLED = False
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY") or "your-secret-key-change-in-production"
    COINBASE_SANDBOX = False
    KRAKEN_SANDBOX = False
elif ENVIRONMENT == Environment.STAGING:
    LOG_LEVEL = "INFO"
    API_RELOAD = False
    SIMULATION_ENABLED = True
elif ENVIRONMENT == Environment.TESTING:
    LOG_LEVEL = "DEBUG"
    API_RELOAD = True
    SIMULATION_ENABLED = True
    MONGODB_DATABASE = "petrosa_test"
    REDIS_DB = 1

def get_config_summary() -> dict[str, Any]:
    return {
        "app": {"name": APP_NAME, "version": APP_VERSION, "environment": ENVIRONMENT, "debug": DEBUG},
        "api": {"host": API_HOST, "port": API_PORT, "reload": API_RELOAD},
        "database": {"mongodb_url": MONGODB_URI, "mongodb_database": MONGODB_DATABASE},
        "messaging": {
            "nats_enabled": NATS_ENABLED,
            "nats_url": NATS_URL,
            "nats_servers": NATS_SERVERS,
            "nats_topic_signals": NATS_TOPIC_SIGNALS
        },
        "trading": {"simulation_enabled": SIMULATION_ENABLED, "default_base_amount": DEFAULT_BASE_AMOUNT, "supported_symbols": SUPPORTED_SYMBOLS},
        "exchange": {"binance_testnet": BINANCE_TESTNET, "binance_api_key_set": bool(BINANCE_API_KEY)},
        "monitoring": {"log_level": LOG_LEVEL, "prometheus_enabled": PROMETHEUS_ENABLED},
    }

def validate_configuration() -> list[str]:
    issues = []
    if ENVIRONMENT == Environment.PRODUCTION:
        if not BINANCE_API_KEY:
            issues.append("BINANCE_API_KEY is required in production")
        if not BINANCE_API_SECRET:
            issues.append("BINANCE_API_SECRET is required in production")
        if JWT_SECRET_KEY == "your-secret-key-change-in-production":
            issues.append("JWT_SECRET_KEY must be changed in production")
    if not MONGODB_URI:
        issues.append("MONGODB_URI is required")
    if NATS_ENABLED and not NATS_URL:
        issues.append("NATS_URL is required when NATS_ENABLED is true")
    return issues

def deprecation_warning(old_name: str, new_name: str, version: str = "0.2.0") -> None:
    warnings.warn(f"{old_name} is deprecated and will be removed in version {version}. Use {new_name} instead.", DeprecationWarning, stacklevel=2)
