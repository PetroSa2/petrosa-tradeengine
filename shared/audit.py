import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from shared.config import settings

logger = logging.getLogger(__name__)


class AuditLogger:
    """
    Centralized audit logger for all trading events, orders, signals, positions, and errors.
    Writes to MongoDB for full audit reliability.
    """
    def __init__(self):
        self.mongo_url = settings.mongodb_url
        self.db_name = settings.mongodb_db or "petrosa_audit"
        self.client: Optional[AsyncIOMotorClient] = None
        self.db: Optional[AsyncIOMotorDatabase] = None
        self.enabled = True
        self.connected = False

    async def initialize(self):
        try:
            self.client = AsyncIOMotorClient(self.mongo_url, serverSelectionTimeoutMS=5000)
            self.db = self.client[self.db_name]
            # Test connection
            await self.db.command("ping")
            self.connected = True
            logger.info("AuditLogger: Connected to MongoDB at %s", self.mongo_url)
        except Exception as e:
            self.enabled = False
            self.connected = False
            logger.error(f"AuditLogger: Failed to connect to MongoDB: {e}")

    async def close(self):
        if self.client:
            self.client.close()
            self.connected = False
            logger.info("AuditLogger: MongoDB connection closed")

    async def log_signal(self, signal: Dict[str, Any], status: str = "received", extra: Optional[Dict[str, Any]] = None):
        await self._log("signals", {
            "type": "signal",
            "status": status,
            "signal": signal,
            "extra": extra or {},
            "timestamp": datetime.utcnow()
        })

    async def log_order(self, order: Dict[str, Any], result: Dict[str, Any], status: str = "executed", extra: Optional[Dict[str, Any]] = None):
        await self._log("orders", {
            "type": "order",
            "status": status,
            "order": order,
            "result": result,
            "extra": extra or {},
            "timestamp": datetime.utcnow()
        })

    async def log_position(self, position: Dict[str, Any], status: str = "updated", extra: Optional[Dict[str, Any]] = None):
        await self._log("positions", {
            "type": "position",
            "status": status,
            "position": position,
            "extra": extra or {},
            "timestamp": datetime.utcnow()
        })

    async def log_error(self, error: str, context: Optional[Dict[str, Any]] = None):
        await self._log("errors", {
            "type": "error",
            "error": error,
            "context": context or {},
            "timestamp": datetime.utcnow()
        })

    async def log_event(self, event_type: str, data: Dict[str, Any]):
        await self._log("events", {
            "type": event_type,
            "data": data,
            "timestamp": datetime.utcnow()
        })

    async def _log(self, collection: str, doc: Dict[str, Any]):
        if not self.enabled or not self.connected or not self.db:
            logger.error(f"AuditLogger: Logging disabled or not connected. Event: {doc.get('type')}")
            # Optionally, raise or queue for retry
            return
        try:
            await self.db[collection].insert_one(doc)
        except Exception as e:
            logger.error(f"AuditLogger: Failed to write to MongoDB: {e}")
            self.connected = False
            self.enabled = False

# Global audit logger instance
audit_logger = AuditLogger() 