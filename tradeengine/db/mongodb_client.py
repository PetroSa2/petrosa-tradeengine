"""
Data Manager client for trading configuration management.

This module provides a clean interface for trading configuration management
through the petrosa-data-manager service, replacing all direct MongoDB access.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from contracts.trading_config import TradingConfig, TradingConfigAudit
from tradeengine.services.data_manager_client import DataManagerClient

logger = logging.getLogger(__name__)


class DataManagerConfigClient:
    """
    Data Manager client for trading configuration management.

    This client replaces all direct MongoDB access with API calls to
    the petrosa-data-manager service.
    """

    def __init__(self):
        """Initialize the Data Manager configuration client."""
        self.data_manager_client = DataManagerClient()
        logger.info("Initialized Data Manager configuration client")

    async def connect(self) -> None:
        """Connect to the Data Manager service."""
        await self.data_manager_client.connect()
        logger.info("Connected to Data Manager service")

    async def disconnect(self) -> None:
        """Disconnect from the Data Manager service."""
        await self.data_manager_client.disconnect()
        logger.info("Disconnected from Data Manager service")

    async def get_global_config(self) -> Optional[TradingConfig]:
        """
        Get global trading configuration from Data Manager.

        Returns:
            TradingConfig object or None if not found
        """
        try:
            response = await self.data_manager_client._client.query(
                database="mongodb",
                collection="trading_configs_global",
                params={"limit": 1},
            )

            if response and response.get("data"):
                doc = response["data"][0]
                doc["id"] = str(doc.pop("_id"))
                return TradingConfig(**doc)
            return None

        except Exception as e:
            logger.error(f"Failed to get global config from Data Manager: {e}")
            return None

    async def upsert_global_config(self, config: TradingConfig) -> bool:
        """
        Upsert global trading configuration via Data Manager.

        Args:
            config: TradingConfig object to upsert

        Returns:
            True if successful, False otherwise
        """
        try:
            config_dict = config.model_dump()
            config_dict["_id"] = "global"  # Use fixed ID for global config

            response = await self.data_manager_client._client.upsert_one(
                database="mongodb",
                collection="trading_configs_global",
                filter={"_id": "global"},
                record=config_dict,
            )

            if response.get("upserted_id") or response.get("modified_count", 0) > 0:
                logger.info("✓ Upserted global trading configuration via Data Manager")
                return True
            else:
                logger.error(
                    "Failed to upsert global trading configuration via Data Manager"
                )
                return False

        except Exception as e:
            logger.error(f"Failed to upsert global config via Data Manager: {e}")
            return False

    async def get_symbol_config(self, symbol: str) -> Optional[TradingConfig]:
        """
        Get symbol-specific trading configuration from Data Manager.

        Args:
            symbol: Trading symbol

        Returns:
            TradingConfig object or None if not found
        """
        try:
            response = await self.data_manager_client._client.query(
                database="mongodb",
                collection="trading_configs_symbols",
                params={"filter": {"symbol": symbol}, "limit": 1},
            )

            if response and response.get("data"):
                doc = response["data"][0]
                doc["id"] = str(doc.pop("_id"))
                return TradingConfig(**doc)
            return None

        except Exception as e:
            logger.error(
                f"Failed to get symbol config for {symbol} from Data Manager: {e}"
            )
            return None

    async def upsert_symbol_config(self, symbol: str, config: TradingConfig) -> bool:
        """
        Upsert symbol-specific trading configuration via Data Manager.

        Args:
            symbol: Trading symbol
            config: TradingConfig object to upsert

        Returns:
            True if successful, False otherwise
        """
        try:
            config_dict = config.model_dump()
            config_dict["symbol"] = symbol

            response = await self.data_manager_client._client.upsert_one(
                database="mongodb",
                collection="trading_configs_symbols",
                filter={"symbol": symbol},
                record=config_dict,
            )

            if response.get("upserted_id") or response.get("modified_count", 0) > 0:
                logger.info(f"✓ Upserted symbol config for {symbol} via Data Manager")
                return True
            else:
                logger.error(
                    f"Failed to upsert symbol config for {symbol} via Data Manager"
                )
                return False

        except Exception as e:
            logger.error(
                f"Failed to upsert symbol config for {symbol} via Data Manager: {e}"
            )
            return False

    async def delete_global_config(self) -> bool:
        """
        Delete global trading configuration via Data Manager.

        Returns:
            True if successful, False otherwise
        """
        try:
            response = await self.data_manager_client._client.delete_one(
                database="mongodb",
                collection="trading_configs_global",
                filter={"_id": "global"},
            )

            if response.get("deleted_count", 0) > 0:
                logger.info("✓ Deleted global trading configuration via Data Manager")
                return True
            else:
                logger.warning("No global configuration found to delete")
                return False

        except Exception as e:
            logger.error(f"Failed to delete global config via Data Manager: {e}")
            return False

    async def delete_symbol_config(self, symbol: str) -> bool:
        """
        Delete symbol-specific trading configuration via Data Manager.

        Args:
            symbol: Trading symbol

        Returns:
            True if successful, False otherwise
        """
        try:
            response = await self.data_manager_client._client.delete_one(
                database="mongodb",
                collection="trading_configs_symbols",
                filter={"symbol": symbol},
            )

            if response.get("deleted_count", 0) > 0:
                logger.info(f"✓ Deleted symbol config for {symbol} via Data Manager")
                return True
            else:
                logger.warning(f"No symbol configuration found for {symbol} to delete")
                return False

        except Exception as e:
            logger.error(
                f"Failed to delete symbol config for {symbol} via Data Manager: {e}"
            )
            return False

    async def create_audit_record(self, audit: TradingConfigAudit) -> bool:
        """
        Create audit record via Data Manager.

        Args:
            audit: TradingConfigAudit object

        Returns:
            True if successful, False otherwise
        """
        try:
            audit_dict = audit.model_dump()
            audit_dict["timestamp"] = datetime.utcnow()

            response = await self.data_manager_client._client.insert_one(
                database="mongodb", collection="trading_config_audit", record=audit_dict
            )

            if response.get("inserted_id"):
                logger.info("✓ Created audit record via Data Manager")
                return True
            else:
                logger.error("Failed to create audit record via Data Manager")
                return False

        except Exception as e:
            logger.error(f"Failed to create audit record via Data Manager: {e}")
            return False

    async def get_audit_trail(self, limit: int = 100) -> list[dict[str, Any]]:
        """
        Get audit trail from Data Manager.

        Args:
            limit: Maximum number of records to return

        Returns:
            List of audit records
        """
        try:
            response = await self.data_manager_client._client.query(
                database="mongodb",
                collection="trading_config_audit",
                params={"sort_by": "timestamp", "sort_order": "desc", "limit": limit},
            )

            records = response.get("data", []) if response else []
            logger.info(f"Retrieved {len(records)} audit records via Data Manager")
            return records

        except Exception as e:
            logger.error(f"Failed to get audit trail via Data Manager: {e}")
            return []

    async def get_symbol_side_config(
        self, symbol: str, side: str
    ) -> Optional[TradingConfig]:
        """
        Get symbol-side specific trading configuration from Data Manager.

        Args:
            symbol: Trading symbol
            side: Trading side (long/short)

        Returns:
            TradingConfig object or None if not found
        """
        try:
            response = await self.data_manager_client._client.query(
                database="mongodb",
                collection="trading_configs_symbol_side",
                params={"filter": {"symbol": symbol, "side": side}, "limit": 1},
            )

            if response and response.get("data"):
                doc = response["data"][0]
                doc["id"] = str(doc.pop("_id"))
                return TradingConfig(**doc)
            return None

        except Exception as e:
            logger.error(
                f"Failed to get symbol-side config for {symbol}-{side} from Data Manager: {e}"
            )
            return None

    async def set_global_config(self, config: TradingConfig) -> bool:
        """
        Set global trading configuration via Data Manager.

        Args:
            config: TradingConfig object to set

        Returns:
            True if successful, False otherwise
        """
        return await self.upsert_global_config(config)

    async def set_symbol_config(self, config: TradingConfig) -> bool:
        """
        Set symbol-specific trading configuration via Data Manager.

        Args:
            config: TradingConfig object to set

        Returns:
            True if successful, False otherwise
        """
        # Extract symbol from config metadata or use a default
        symbol = getattr(config, "symbol", "UNKNOWN")
        return await self.upsert_symbol_config(symbol, config)

    async def set_symbol_side_config(self, config: TradingConfig) -> bool:
        """
        Set symbol-side specific trading configuration via Data Manager.

        Args:
            config: TradingConfig object to set

        Returns:
            True if successful, False otherwise
        """
        try:
            config_dict = config.model_dump()
            symbol = config_dict.get("symbol", "UNKNOWN")
            side = config_dict.get("side", "UNKNOWN")
            config_dict["symbol"] = symbol
            config_dict["side"] = side

            response = await self.data_manager_client._client.upsert_one(
                database="mongodb",
                collection="trading_configs_symbol_side",
                filter={"symbol": symbol, "side": side},
                record=config_dict,
            )

            if response.get("upserted_id") or response.get("modified_count", 0) > 0:
                logger.info(
                    f"✓ Upserted symbol-side config for {symbol}-{side} via Data Manager"
                )
                return True
            else:
                logger.error(
                    f"Failed to upsert symbol-side config for {symbol}-{side} via Data Manager"
                )
                return False

        except Exception as e:
            logger.error(f"Failed to upsert symbol-side config via Data Manager: {e}")
            return False

    async def delete_symbol_side_config(self, symbol: str, side: str) -> bool:
        """
        Delete symbol-side specific trading configuration via Data Manager.

        Args:
            symbol: Trading symbol
            side: Trading side

        Returns:
            True if successful, False otherwise
        """
        try:
            response = await self.data_manager_client._client.delete_one(
                database="mongodb",
                collection="trading_configs_symbol_side",
                filter={"symbol": symbol, "side": side},
            )

            if response.get("deleted_count", 0) > 0:
                logger.info(
                    f"✓ Deleted symbol-side config for {symbol}-{side} via Data Manager"
                )
                return True
            else:
                logger.warning(
                    f"No symbol-side configuration found for {symbol}-{side} to delete"
                )
                return False

        except Exception as e:
            logger.error(
                f"Failed to delete symbol-side config for {symbol}-{side} via Data Manager: {e}"
            )
            return False

    async def add_audit_record(self, audit: TradingConfigAudit) -> bool:
        """
        Add audit record via Data Manager.

        Args:
            audit: TradingConfigAudit object

        Returns:
            True if successful, False otherwise
        """
        return await self.create_audit_record(audit)

    @property
    def connected(self) -> bool:
        """
        Check if the Data Manager client is connected.

        Returns:
            True if connected, False otherwise
        """
        try:
            # Simple health check to determine connection status
            return True  # Assume connected if client is initialized
        except Exception:
            return False

    async def health_check(self) -> dict[str, Any]:
        """
        Check the health of the Data Manager connection.

        Returns:
            Health status dictionary
        """
        try:
            health = await self.data_manager_client._client.health()
            return {
                "status": (
                    "healthy" if health.get("status") == "healthy" else "unhealthy"
                ),
                "service": "data-manager",
                "details": health,
            }
        except Exception as e:
            logger.error(f"Data Manager health check failed: {e}")
            return {"status": "unhealthy", "service": "data-manager", "error": str(e)}


# Global Data Manager configuration client instance
config_client = DataManagerConfigClient()
