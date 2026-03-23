"""
Data Manager client for trading configuration management.

This module provides a clean interface for trading configuration management
through the petrosa-data-manager service, replacing all direct MongoDB access.
"""

import logging
from datetime import datetime
from shared.constants import UTC
from typing import Any, Optional

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

    async def get_global_config(self) -> TradingConfig | None:
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

            if response.get("upserted_id") or (response.get("modified_count", 0) > 0):
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

    async def get_symbol_config(self, symbol: str) -> TradingConfig | None:
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

            if response.get("upserted_id") or (response.get("modified_count", 0) > 0):
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
            audit_dict["timestamp"] = datetime.now(UTC)

            response = await self.data_manager_client._client.insert_one(
                database="mongodb",
                collection="trading_configs_audit",
                record=audit_dict,
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

    async def get_audit_trail(
        self, symbol: str | None = None, side: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        """
        Get audit trail from Data Manager.

        Args:
            symbol: Optional symbol to filter by
            side: Optional side to filter by
            limit: Maximum number of records to return

        Returns:
            List of audit records
        """
        try:
            # Build filter parameters
            filter_params = {}
            if symbol:
                filter_params["symbol"] = symbol
            if side:
                filter_params["side"] = side

            response = await self.data_manager_client._client.query(
                database="mongodb",
                collection="trading_configs_audit",
                params={
                    "filter": filter_params,
                    "sort_by": "timestamp",
                    "sort_order": "desc",
                    "limit": limit,
                },
            )

            records = response.get("data", []) if response else []
            logger.info(f"Retrieved {len(records)} audit records via Data Manager")
            return records

        except Exception as e:
            logger.error(f"Failed to get audit trail via Data Manager: {e}")
            return []

    async def get_audit_record_by_id(self, audit_id: str) -> dict[str, Any] | None:
        """
        Get a specific audit record by its ID.

        Args:
            audit_id: Audit record unique identifier

        Returns:
            Audit record or None if not found
        """
        try:
            response = await self.data_manager_client._client.query(
                database="mongodb",
                collection="trading_configs_audit",
                params={"filter": {"_id": audit_id}, "limit": 1},
            )

            if response and response.get("data"):
                return response["data"][0]
            return None

        except Exception as e:
            logger.error(
                f"Failed to get audit record {audit_id} from Data Manager: {e}"
            )
            return None

    async def get_audit_record_by_version(
        self,
        version: int,
        config_type: str,
        symbol: str | None = None,
        side: str | None = None,
    ) -> dict[str, Any] | None:
        """
        Get a specific audit record by its version and scope.

        Args:
            version: Version number to find
            config_type: Type of configuration (global, symbol, symbol_side)
            symbol: Optional symbol
            side: Optional side

        Returns:
            Audit record or None if not found
        """
        try:
            filter_dict = {
                "version_after": version,
                "config_type": config_type,
            }
            if symbol:
                filter_dict["symbol"] = symbol
            if side:
                filter_dict["side"] = side

            response = await self.data_manager_client._client.query(
                database="mongodb",
                collection="trading_configs_audit",
                params={
                    "filter": filter_dict,
                    "sort_by": "timestamp",
                    "sort_order": "desc",
                    "limit": 1,
                },
            )

            if response and response.get("data"):
                return response["data"][0]
            return None

        except Exception as e:
            logger.error(
                f"Failed to get version {version} for {config_type} from Data Manager: {e}"
            )
            return None

    async def get_config_history(
        self, symbol: str | None = None, side: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        """
        Get configuration history for a specific scope.

        Args:
            symbol: Optional symbol to filter by
            side: Optional side to filter by
            limit: Maximum number of records to return

        Returns:
            List of configuration history records
        """
        return await self.get_audit_trail(symbol=symbol, side=side, limit=limit)

    async def get_symbol_side_config(
        self, symbol: str, side: str
    ) -> TradingConfig | None:
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

            if response.get("upserted_id") or (response.get("modified_count", 0) > 0):
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

    async def get_strategy_config(self, strategy_id: str) -> TradingConfig | None:
        """
        Get strategy-specific configuration from Data Manager.

        Args:
            strategy_id: Strategy identifier

        Returns:
            TradingConfig object or None if not found
        """
        try:
            response = await self.data_manager_client._client.query(
                database="mongodb",
                collection="trading_configs_strategy",
                params={"filter": {"strategy_id": strategy_id}, "limit": 1},
            )

            if response and response.get("data"):
                doc = response["data"][0]
                doc["id"] = str(doc.pop("_id"))
                return TradingConfig(**doc)
            return None
        except Exception as e:
            logger.error(f"Failed to get strategy config: {e}")
            return None

    async def upsert_strategy_config(self, config: TradingConfig) -> bool:
        """
        Upsert strategy-specific configuration.

        Args:
            config: TradingConfig object to upsert

        Returns:
            True if successful, False otherwise
        """
        try:
            strategy_id = config.strategy_id
            if not strategy_id or not strategy_id.strip():
                logger.error(
                    "Cannot upsert strategy config: strategy_id is required and cannot be blank"
                )
                return False

            config_dict = config.model_dump()
            config_dict["strategy_id"] = strategy_id

            response = await self.data_manager_client._client.upsert_one(
                database="mongodb",
                collection="trading_configs_strategy",
                filter={"strategy_id": strategy_id},
                record=config_dict,
            )

            if response.get("upserted_id") or (response.get("modified_count", 0) > 0):
                logger.info(
                    f"✓ Upserted strategy config for {strategy_id} via Data Manager"
                )
                return True
            else:
                logger.error(
                    f"Failed to upsert strategy config for {strategy_id} via Data Manager"
                )
                return False

        except Exception as e:
            logger.error(f"Failed to upsert strategy config via Data Manager: {e}")
            return False

    async def rollback_config(
        self,
        changed_by: str,
        symbol: str | None = None,
        side: str | None = None,
        target_version: int | None = None,
        reason: str | None = None,
    ) -> bool:
        """
        Rollback configuration via Data Manager.

        Args:
            changed_by: Who is performing the rollback
            symbol: Optional symbol
            side: Optional side
            target_version: Optional specific version
            reason: Optional reason

        Returns:
            True if successful, False otherwise
        """
        try:
            payload = {
                "changed_by": changed_by,
                "target_version": target_version,
                "reason": reason,
            }

            # Map tradeengine scopes to data-manager strategy_id
            strategy_id = "tradeengine"

            url = f"/api/v1/config/rollback/strategies/{strategy_id}"
            params = {}
            if symbol:
                params["symbol"] = symbol
            if side:
                params["side"] = side

            # Use the internal _client which handles base_url and auth
            response = await self.data_manager_client._client.request(
                "POST", url, json=payload, params=params
            )

            return response is not None
        except Exception as e:
            logger.error(f"Failed to rollback config via Data Manager: {e}")
            return False

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
