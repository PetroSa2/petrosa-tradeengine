import logging
from datetime import datetime
from typing import Dict, Any, Optional
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from shared.config import settings


# Setup standard logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper()),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)


class AuditLogger:
    def __init__(self):
        self.client: Optional[AsyncIOMotorClient] = None
        self.db: Optional[AsyncIOMotorDatabase] = None

    async def initialize(self):
        """Initialize MongoDB connection"""
        try:
            self.client = AsyncIOMotorClient(settings.mongodb_url)
            self.db = self.client[settings.mongodb_database]
            # Test connection
            await self.client.admin.command("ping")
            logger.info("MongoDB audit logger initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize MongoDB audit logger: {e}")
            # Continue without audit logging if MongoDB is not available
            self.client = None
            self.db = None

    async def log_trade(
        self,
        order: Dict[str, Any],
        result: Dict[str, Any],
        signal_meta: Optional[Dict[str, Any]] = None,
    ):
        """Log trade execution to MongoDB"""
        if not self.db:
            logger.warning("MongoDB not available, skipping audit log")
            return

        try:
            audit_record = {
                "timestamp": datetime.utcnow(),
                "order": order,
                "result": result,
                "signal_meta": signal_meta or {},
                "environment": settings.environment,
            }

            await self.db.trade_audit.insert_one(audit_record)
            logger.info(
                f"Trade audit logged: {order.get('type', 'unknown')} {order.get('side', 'unknown')} order"
            )

        except Exception as e:
            logger.error(f"Failed to log trade audit: {e}")

    async def close(self):
        """Close MongoDB connection"""
        if self.client:
            self.client.close()


# Global audit logger instance
audit_logger = AuditLogger()
