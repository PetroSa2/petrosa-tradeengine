"""
MySQL Repository for Trading Configuration Management.

Provides database operations for storing and retrieving trading configurations
from MySQL as a fallback to MongoDB.
"""

import logging
from typing import Optional

from contracts.trading_config import LeverageStatus, TradingConfig, TradingConfigAudit

logger = logging.getLogger(__name__)


class MySQLConfigRepository:
    """MySQL repository for trading configuration persistence (fallback)."""

    def __init__(self, mysql_uri: str):
        """
        Initialize MySQL repository.

        Args:
            mysql_uri: MySQL connection URI
        """
        self.mysql_uri = mysql_uri
        self.connected = False
        logger.info("MySQL config repository initialized (stub for MVP)")

    async def connect(self) -> None:
        """Establish MySQL connection."""
        # TODO: Implement MySQL connection
        # For MVP, we'll rely on MongoDB primarily
        self.connected = False
        logger.warning(
            "MySQL config repository not yet implemented - using MongoDB only"
        )

    async def disconnect(self) -> None:
        """Close MySQL connection."""
        pass

    # Stub methods - will implement if MongoDB fails
    async def get_global_config(self) -> Optional[TradingConfig]:
        """Get global config from MySQL."""
        return None

    async def set_global_config(self, config: TradingConfig) -> bool:
        """Set global config in MySQL."""
        return False

    async def get_symbol_config(self, symbol: str) -> Optional[TradingConfig]:
        """Get symbol config from MySQL."""
        return None

    async def set_symbol_config(self, config: TradingConfig) -> bool:
        """Set symbol config in MySQL."""
        return False

    async def get_symbol_side_config(
        self, symbol: str, side: str
    ) -> Optional[TradingConfig]:
        """Get symbol-side config from MySQL."""
        return None

    async def set_symbol_side_config(self, config: TradingConfig) -> bool:
        """Set symbol-side config in MySQL."""
        return False

    async def add_audit_record(self, audit: TradingConfigAudit) -> bool:
        """Add audit record to MySQL."""
        return False

    async def get_leverage_status(self, symbol: str) -> Optional[LeverageStatus]:
        """Get leverage status from MySQL."""
        return None

    async def set_leverage_status(self, status: LeverageStatus) -> bool:
        """Set leverage status in MySQL."""
        return False
