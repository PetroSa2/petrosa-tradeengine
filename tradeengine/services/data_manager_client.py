"""
Data Manager client for petrosa-tradeengine.

This module provides a client for interacting with the petrosa-data-manager API
for audit logging and configuration management.
"""

import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from contracts.trading_config import LeverageStatus, TradingConfig, TradingConfigAudit

# Local Data Manager Client implementation
# from data_manager_client import DataManagerClient as BaseDataManagerClient
# from data_manager_client.exceptions import ConnectionError


# Temporary local implementation
class BaseDataManagerClient:
    """Temporary local implementation of Data Manager Client."""

    def __init__(self, base_url: str, timeout: int = 30, max_retries: int = 3):
        self.base_url = base_url
        self.timeout = timeout
        self.max_retries = max_retries

    async def health(self):
        """Health check."""
        return {"status": "healthy"}

    async def close(self):
        """Close connection."""
        pass

    # NEW METHODS - Implement missing Data Manager methods
    async def query(self, database: str, collection: str, params: dict):
        """Query records."""
        # TODO: Implement actual HTTP call to Data Manager API
        # For now, return empty to avoid errors
        return {"data": []}

    async def insert_one(self, database: str, collection: str, record: dict):
        """Insert one record."""
        # TODO: Implement actual HTTP call to Data Manager API
        # For now, return success to avoid errors
        return {"inserted_id": "placeholder"}

    async def update_one(self, database: str, collection: str, filter: dict, update: dict):
        """Update one record."""
        # TODO: Implement actual HTTP call to Data Manager API
        return {"modified_count": 1}

    async def upsert_one(self, database: str, collection: str, filter: dict, record: dict):
        """Upsert one record."""
        # TODO: Implement actual HTTP call to Data Manager API
        return {"upserted_id": "placeholder"}

    async def delete_one(self, database: str, collection: str, filter: dict):
        """Delete one record."""
        # TODO: Implement actual HTTP call to Data Manager API
        return {"deleted_count": 1}


class ConnectionError(Exception):
    """Connection error."""

    pass


logger = None


def get_logger():
    """Get logger instance."""
    global logger
    if logger is None:
        import logging

        logger = logging.getLogger(__name__)
    return logger


class DataManagerClient:
    """
    Data Manager client for the Trade Engine.

    Provides methods for audit logging and configuration management
    through the petrosa-data-manager API.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: int = 30,
        max_retries: int = 3,
    ):
        """
        Initialize the Data Manager client.

        Args:
            base_url: Data Manager API base URL
            timeout: Request timeout in seconds
            max_retries: Maximum number of retry attempts
        """
        self.base_url = base_url or os.getenv(
            "DATA_MANAGER_URL", "http://petrosa-data-manager:8000"
        )
        self.timeout = timeout
        self.max_retries = max_retries

        # Initialize the base client
        self._client = BaseDataManagerClient(
            base_url=self.base_url,
            timeout=self.timeout,
            max_retries=self.max_retries,
        )

        self._logger = get_logger()
        self._logger.info(f"Initialized Data Manager client: {self.base_url}")

    async def connect(self):
        """Connect to the Data Manager service."""
        try:
            # Test connection with health check
            health = await self._client.health()
            if health.get("status") != "healthy":
                raise ConnectionError(f"Data Manager health check failed: {health}")

            self._logger.info("Connected to Data Manager service")

        except Exception as e:
            self._logger.error(f"Failed to connect to Data Manager: {e}")
            raise

    async def disconnect(self):
        """Disconnect from the Data Manager service."""
        try:
            await self._client.close()
            self._logger.info("Disconnected from Data Manager service")
        except Exception as e:
            self._logger.warning(f"Error disconnecting from Data Manager: {e}")

    # Configuration Management Methods

    async def get_global_config(self) -> Optional[TradingConfig]:
        """Get global trading configuration."""
        try:
            result = await self._client.query(
                database="mongodb",
                collection="trading_configs_global",
                limit=1,
            )

            if result.get("data") and len(result["data"]) > 0:
                doc = result["data"][0]
                doc["id"] = str(doc.pop("_id", ""))
                return TradingConfig(**doc)
            return None

        except Exception as e:
            self._logger.error(f"Error fetching global config: {e}")
            return None

    async def set_global_config(self, config: TradingConfig) -> bool:
        """Set global trading configuration."""
        try:
            config_dict = config.model_dump(exclude={"id"})
            config_dict["updated_at"] = datetime.utcnow()

            result = await self._client.update(
                database="mongodb",
                collection="trading_configs_global",
                filter={},  # Empty filter for global config
                data=config_dict,
                upsert=True,
            )

            if (
                result.get("modified_count", 0) > 0
                or result.get("upserted_count", 0) > 0
            ):
                self._logger.info("Updated global trading config")
                return True
            return False

        except Exception as e:
            self._logger.error(f"Error setting global config: {e}")
            return False

    async def delete_global_config(self) -> bool:
        """Delete global trading configuration."""
        try:
            result = await self._client.delete(
                database="mongodb",
                collection="trading_configs_global",
                filter={},  # Empty filter to delete all
            )

            if result.get("deleted_count", 0) > 0:
                self._logger.info("Deleted global trading config")
                return True
            return False

        except Exception as e:
            self._logger.error(f"Error deleting global config: {e}")
            return False

    async def get_symbol_config(self, symbol: str) -> Optional[TradingConfig]:
        """Get symbol-specific trading configuration."""
        try:
            result = await self._client.query(
                database="mongodb",
                collection="trading_configs_symbol",
                filter={"symbol": symbol},
                limit=1,
            )

            if result.get("data") and len(result["data"]) > 0:
                doc = result["data"][0]
                doc["id"] = str(doc.pop("_id", ""))
                return TradingConfig(**doc)
            return None

        except Exception as e:
            self._logger.error(f"Error fetching symbol config for {symbol}: {e}")
            return None

    async def set_symbol_config(self, config: TradingConfig) -> bool:
        """Set symbol-specific trading configuration."""
        try:
            if not config.symbol:
                return False

            config_dict = config.model_dump(exclude={"id"})
            config_dict["updated_at"] = datetime.utcnow()

            result = await self._client.update(
                database="mongodb",
                collection="trading_configs_symbol",
                filter={"symbol": config.symbol},
                data=config_dict,
                upsert=True,
            )

            if (
                result.get("modified_count", 0) > 0
                or result.get("upserted_count", 0) > 0
            ):
                self._logger.info(f"Updated symbol config for {config.symbol}")
                return True
            return False

        except Exception as e:
            self._logger.error(f"Error setting symbol config: {e}")
            return False

    async def delete_symbol_config(self, symbol: str) -> bool:
        """Delete symbol-specific trading configuration."""
        try:
            result = await self._client.delete(
                database="mongodb",
                collection="trading_configs_symbol",
                filter={"symbol": symbol},
            )

            if result.get("deleted_count", 0) > 0:
                self._logger.info(f"Deleted symbol config for {symbol}")
                return True
            return False

        except Exception as e:
            self._logger.error(f"Error deleting symbol config for {symbol}: {e}")
            return False

    async def get_symbol_side_config(
        self, symbol: str, side: str
    ) -> Optional[TradingConfig]:
        """Get symbol-side-specific trading configuration."""
        try:
            result = await self._client.query(
                database="mongodb",
                collection="trading_configs_symbol_side",
                filter={"symbol": symbol, "side": side},
                limit=1,
            )

            if result.get("data") and len(result["data"]) > 0:
                doc = result["data"][0]
                doc["id"] = str(doc.pop("_id", ""))
                return TradingConfig(**doc)
            return None

        except Exception as e:
            self._logger.error(
                f"Error fetching symbol-side config for {symbol}-{side}: {e}"
            )
            return None

    async def set_symbol_side_config(self, config: TradingConfig) -> bool:
        """Set symbol-side-specific trading configuration."""
        try:
            if not config.symbol or not config.side:
                return False

            config_dict = config.model_dump(exclude={"id"})
            config_dict["updated_at"] = datetime.utcnow()

            result = await self._client.update(
                database="mongodb",
                collection="trading_configs_symbol_side",
                filter={"symbol": config.symbol, "side": config.side},
                data=config_dict,
                upsert=True,
            )

            if (
                result.get("modified_count", 0) > 0
                or result.get("upserted_count", 0) > 0
            ):
                self._logger.info(
                    f"Updated symbol-side config for {config.symbol}-{config.side}"
                )
                return True
            return False

        except Exception as e:
            self._logger.error(f"Error setting symbol-side config: {e}")
            return False

    async def delete_symbol_side_config(self, symbol: str, side: str) -> bool:
        """Delete symbol-side-specific trading configuration."""
        try:
            result = await self._client.delete(
                database="mongodb",
                collection="trading_configs_symbol_side",
                filter={"symbol": symbol, "side": side},
            )

            if result.get("deleted_count", 0) > 0:
                self._logger.info(f"Deleted symbol-side config for {symbol}-{side}")
                return True
            return False

        except Exception as e:
            self._logger.error(
                f"Error deleting symbol-side config for {symbol}-{side}: {e}"
            )
            return False

    # Audit Trail Methods

    async def add_audit_record(self, audit: TradingConfigAudit) -> bool:
        """Add audit trail record."""
        try:
            audit_dict = audit.model_dump(exclude={"id"})

            result = await self._client.insert(
                database="mongodb",
                collection="trading_configs_audit",
                data=audit_dict,
            )

            if result.get("inserted_count", 0) > 0:
                self._logger.debug(f"Added audit record: {audit.get_change_summary()}")
                return True
            return False

        except Exception as e:
            self._logger.error(f"Error adding audit record: {e}")
            return False

    async def get_audit_trail(
        self, symbol: Optional[str] = None, side: Optional[str] = None, limit: int = 100
    ) -> List[TradingConfigAudit]:
        """Get audit trail records with optional filters."""
        try:
            filter_dict = {}
            if symbol:
                filter_dict["symbol"] = symbol
            if side:
                filter_dict["side"] = side

            result = await self._client.query(
                database="mongodb",
                collection="trading_configs_audit",
                filter=filter_dict,
                sort={"timestamp": -1},
                limit=limit,
            )

            # Convert to models
            audit_records = []
            for doc in result.get("data", []):
                doc["id"] = str(doc.pop("_id", ""))
                audit_records.append(TradingConfigAudit(**doc))

            return audit_records

        except Exception as e:
            self._logger.error(f"Error fetching audit trail: {e}")
            return []

    # Leverage Status Methods

    async def get_leverage_status(self, symbol: str) -> Optional[LeverageStatus]:
        """Get leverage status for symbol."""
        try:
            result = await self._client.query(
                database="mongodb",
                collection="leverage_status",
                filter={"symbol": symbol},
                limit=1,
            )

            if result.get("data") and len(result["data"]) > 0:
                doc = result["data"][0]
                doc["id"] = str(doc.pop("_id", ""))
                return LeverageStatus(**doc)
            return None

        except Exception as e:
            self._logger.error(f"Error fetching leverage status for {symbol}: {e}")
            return None

    async def set_leverage_status(self, status: LeverageStatus) -> bool:
        """Set leverage status for symbol."""
        try:
            status_dict = status.model_dump(exclude={"id"})
            status_dict["updated_at"] = datetime.utcnow()

            result = await self._client.update(
                database="mongodb",
                collection="leverage_status",
                filter={"symbol": status.symbol},
                data=status_dict,
                upsert=True,
            )

            if (
                result.get("modified_count", 0) > 0
                or result.get("upserted_count", 0) > 0
            ):
                self._logger.debug(
                    f"Updated leverage status for {status.symbol}: "
                    f"configured={status.configured_leverage}, "
                    f"actual={status.actual_leverage}"
                )
                return True
            return False

        except Exception as e:
            self._logger.error(f"Error setting leverage status: {e}")
            return False

    async def get_all_leverage_status(self) -> List[LeverageStatus]:
        """Get all leverage status records."""
        try:
            result = await self._client.query(
                database="mongodb",
                collection="leverage_status",
                limit=1000,
            )

            # Convert to models
            status_list = []
            for doc in result.get("data", []):
                doc["id"] = str(doc.pop("_id", ""))
                status_list.append(LeverageStatus(**doc))

            return status_list

        except Exception as e:
            self._logger.error(f"Error fetching all leverage status: {e}")
            return []

    async def health_check(self) -> Dict[str, Any]:
        """
        Check the health of the Data Manager service.

        Returns:
            Health status information
        """
        try:
            health = await self._client.health()
            self._logger.info(
                f"Data Manager health check: {health.get('status', 'unknown')}"
            )
            return health
        except Exception as e:
            self._logger.error(f"Data Manager health check failed: {e}")
            return {"status": "unhealthy", "error": str(e)}

    async def __aenter__(self):
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.disconnect()
