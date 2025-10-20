"""
MongoDB Client for Trading Configuration Management.

Provides async operations for storing and retrieving trading configurations
from MongoDB with proper error handling and connection management.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorClient

from contracts.trading_config import LeverageStatus, TradingConfig, TradingConfigAudit

logger = logging.getLogger(__name__)


class MongoDBClient:
    """MongoDB client for trading configuration persistence."""

    def __init__(
        self,
        connection_string: str,
        database_name: str,
        timeout_ms: int = 5000,
    ):
        """
        Initialize MongoDB client.

        Args:
            connection_string: MongoDB connection URI
            database_name: Database name
            timeout_ms: Connection timeout in milliseconds
        """
        self.connection_string = connection_string
        self.database_name = database_name
        self.timeout_ms = timeout_ms
        self.client: Optional[Any] = None  # AsyncIOMotorClient type
        self.db: Optional[Any] = None  # AsyncIOMotorDatabase type
        self.connected = False

    async def connect(self) -> None:
        """Establish MongoDB connection."""
        try:
            self.client = AsyncIOMotorClient(
                self.connection_string,
                serverSelectionTimeoutMS=self.timeout_ms,
            )
            self.db = self.client[self.database_name]

            # Test connection
            await self.client.admin.command("ping")
            self.connected = True
            logger.info(f"MongoDB connected to database: {self.database_name}")
        except Exception as e:
            self.connected = False
            logger.error(f"MongoDB connection failed: {e}")
            raise

    async def disconnect(self) -> None:
        """Close MongoDB connection."""
        if self.client:
            self.client.close()
            self.connected = False
            logger.info("MongoDB connection closed")

    # =========================================================================
    # Global Configuration Operations
    # =========================================================================

    async def get_global_config(self) -> Optional[TradingConfig]:
        """Get global trading configuration."""
        try:
            if not self.db:
                return None

            collection = self.db["trading_configs_global"]
            doc = await collection.find_one()

            if doc:
                doc["id"] = str(doc.pop("_id"))
                return TradingConfig(**doc)
            return None
        except Exception as e:
            logger.error(f"MongoDB get_global_config error: {e}")
            return None

    async def set_global_config(self, config: TradingConfig) -> bool:
        """Set global trading configuration."""
        try:
            if not self.db:
                return False

            collection = self.db["trading_configs_global"]
            config_dict = config.model_dump(exclude={"id"})
            config_dict["updated_at"] = datetime.utcnow()

            # Upsert (update or insert)
            result = await collection.replace_one({}, config_dict, upsert=True)

            logger.info(f"MongoDB set_global_config: modified={result.modified_count}")
            return True
        except Exception as e:
            logger.error(f"MongoDB set_global_config error: {e}")
            return False

    async def delete_global_config(self) -> bool:
        """Delete global trading configuration."""
        try:
            if not self.db:
                return False

            collection = self.db["trading_configs_global"]
            result = await collection.delete_many({})

            logger.info(f"MongoDB delete_global_config: deleted={result.deleted_count}")
            return True
        except Exception as e:
            logger.error(f"MongoDB delete_global_config error: {e}")
            return False

    # =========================================================================
    # Symbol Configuration Operations
    # =========================================================================

    async def get_symbol_config(self, symbol: str) -> Optional[TradingConfig]:
        """Get symbol-specific trading configuration."""
        try:
            if not self.db:
                return None

            collection = self.db["trading_configs_symbol"]
            doc = await collection.find_one({"symbol": symbol})

            if doc:
                doc["id"] = str(doc.pop("_id"))
                return TradingConfig(**doc)
            return None
        except Exception as e:
            logger.error(f"MongoDB get_symbol_config error for {symbol}: {e}")
            return None

    async def set_symbol_config(self, config: TradingConfig) -> bool:
        """Set symbol-specific trading configuration."""
        try:
            if not self.db or not config.symbol:
                return False

            collection = self.db["trading_configs_symbol"]
            config_dict = config.model_dump(exclude={"id"})
            config_dict["updated_at"] = datetime.utcnow()

            result = await collection.replace_one(
                {"symbol": config.symbol}, config_dict, upsert=True
            )

            logger.info(
                f"MongoDB set_symbol_config {config.symbol}: "
                f"modified={result.modified_count}"
            )
            return True
        except Exception as e:
            logger.error(f"MongoDB set_symbol_config error: {e}")
            return False

    async def delete_symbol_config(self, symbol: str) -> bool:
        """Delete symbol-specific trading configuration."""
        try:
            if not self.db:
                return False

            collection = self.db["trading_configs_symbol"]
            result = await collection.delete_one({"symbol": symbol})

            logger.info(
                f"MongoDB delete_symbol_config {symbol}: "
                f"deleted={result.deleted_count}"
            )
            return True
        except Exception as e:
            logger.error(f"MongoDB delete_symbol_config error: {e}")
            return False

    # =========================================================================
    # Symbol-Side Configuration Operations
    # =========================================================================

    async def get_symbol_side_config(
        self, symbol: str, side: str
    ) -> Optional[TradingConfig]:
        """Get symbol-side-specific trading configuration."""
        try:
            if not self.db:
                return None

            collection = self.db["trading_configs_symbol_side"]
            doc = await collection.find_one({"symbol": symbol, "side": side})

            if doc:
                doc["id"] = str(doc.pop("_id"))
                return TradingConfig(**doc)
            return None
        except Exception as e:
            logger.error(
                f"MongoDB get_symbol_side_config error for {symbol}-{side}: {e}"
            )
            return None

    async def set_symbol_side_config(self, config: TradingConfig) -> bool:
        """Set symbol-side-specific trading configuration."""
        try:
            if not self.db or not config.symbol or not config.side:
                return False

            collection = self.db["trading_configs_symbol_side"]
            config_dict = config.model_dump(exclude={"id"})
            config_dict["updated_at"] = datetime.utcnow()

            result = await collection.replace_one(
                {"symbol": config.symbol, "side": config.side}, config_dict, upsert=True
            )

            logger.info(
                f"MongoDB set_symbol_side_config {config.symbol}-{config.side}: "
                f"modified={result.modified_count}"
            )
            return True
        except Exception as e:
            logger.error(f"MongoDB set_symbol_side_config error: {e}")
            return False

    async def delete_symbol_side_config(self, symbol: str, side: str) -> bool:
        """Delete symbol-side-specific trading configuration."""
        try:
            if not self.db:
                return False

            collection = self.db["trading_configs_symbol_side"]
            result = await collection.delete_one({"symbol": symbol, "side": side})

            logger.info(
                f"MongoDB delete_symbol_side_config {symbol}-{side}: "
                f"deleted={result.deleted_count}"
            )
            return True
        except Exception as e:
            logger.error(f"MongoDB delete_symbol_side_config error: {e}")
            return False

    # =========================================================================
    # Audit Trail Operations
    # =========================================================================

    async def add_audit_record(self, audit: TradingConfigAudit) -> bool:
        """Add audit trail record."""
        try:
            if not self.db:
                return False

            collection = self.db["trading_configs_audit"]
            audit_dict = audit.model_dump(exclude={"id"})

            await collection.insert_one(audit_dict)
            logger.debug(f"MongoDB audit record added: {audit.get_change_summary()}")
            return True
        except Exception as e:
            logger.error(f"MongoDB add_audit_record error: {e}")
            return False

    async def get_audit_trail(
        self, symbol: Optional[str] = None, side: Optional[str] = None, limit: int = 100
    ) -> List[TradingConfigAudit]:
        """Get audit trail records with optional filters."""
        try:
            if not self.db:
                return []

            collection = self.db["trading_configs_audit"]

            # Build query
            query: Dict[str, Any] = {}
            if symbol:
                query["symbol"] = symbol
            if side:
                query["side"] = side

            # Fetch records
            cursor = collection.find(query).sort("timestamp", -1).limit(limit)
            records = await cursor.to_list(length=limit)

            # Convert to models
            audit_records = []
            for doc in records:
                doc["id"] = str(doc.pop("_id"))
                audit_records.append(TradingConfigAudit(**doc))

            return audit_records
        except Exception as e:
            logger.error(f"MongoDB get_audit_trail error: {e}")
            return []

    # =========================================================================
    # Leverage Status Operations
    # =========================================================================

    async def get_leverage_status(self, symbol: str) -> Optional[LeverageStatus]:
        """Get leverage status for symbol."""
        try:
            if not self.db:
                return None

            collection = self.db["leverage_status"]
            doc = await collection.find_one({"symbol": symbol})

            if doc:
                doc["id"] = str(doc.pop("_id"))
                return LeverageStatus(**doc)
            return None
        except Exception as e:
            logger.error(f"MongoDB get_leverage_status error for {symbol}: {e}")
            return None

    async def set_leverage_status(self, status: LeverageStatus) -> bool:
        """Set leverage status for symbol."""
        try:
            if not self.db:
                return False

            collection = self.db["leverage_status"]
            status_dict = status.model_dump(exclude={"id"})
            status_dict["updated_at"] = datetime.utcnow()

            await collection.replace_one(
                {"symbol": status.symbol}, status_dict, upsert=True
            )

            logger.debug(
                f"MongoDB set_leverage_status {status.symbol}: "
                f"configured={status.configured_leverage}, "
                f"actual={status.actual_leverage}"
            )
            return True
        except Exception as e:
            logger.error(f"MongoDB set_leverage_status error: {e}")
            return False

    async def get_all_leverage_status(self) -> List[LeverageStatus]:
        """Get all leverage status records."""
        try:
            if not self.db:
                return []

            collection = self.db["leverage_status"]
            cursor = collection.find()
            records = await cursor.to_list(length=1000)

            # Convert to models
            status_list = []
            for doc in records:
                doc["id"] = str(doc.pop("_id"))
                status_list.append(LeverageStatus(**doc))

            return status_list
        except Exception as e:
            logger.error(f"MongoDB get_all_leverage_status error: {e}")
            return []
