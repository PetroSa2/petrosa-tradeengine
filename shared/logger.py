import asyncio
import logging
from datetime import datetime
from typing import Any
import json

import aiomysql
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from shared.config import settings

# Setup standard logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper()),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)


class AuditLogger:
    def __init__(self) -> None:
        self.engine = None
        self.async_session = None
        self.initialized = False
        self.retry_attempts = 3
        self.retry_delay = 1.0
        self.backoff_multiplier = 2.0

    async def initialize(self) -> None:
        """Initialize MySQL connection with retries and backoff"""
        for attempt in range(self.retry_attempts):
            try:
                # Create async engine for SQLAlchemy
                self.engine = create_async_engine(
                    settings.mysql_uri,
                    echo=False,
                    pool_size=5,
                    max_overflow=10,
                    pool_pre_ping=True,
                    pool_recycle=3600
                )
                
                # Create async session factory
                async_session = sessionmaker(
                    self.engine, class_=AsyncSession, expire_on_commit=False
                )
                self.async_session = async_session
                
                # Test connection
                async with self.engine.begin() as conn:
                    await conn.execute(text("SELECT 1"))
                
                self.initialized = True
                logger.info("MySQL audit logger initialized successfully")
                return
                
            except Exception as e:
                logger.warning(f"MySQL audit logger initialization attempt {attempt + 1} failed: {e}")
                if attempt < self.retry_attempts - 1:
                    delay = self.retry_delay * (self.backoff_multiplier ** attempt)
                    logger.info(f"Retrying MySQL initialization in {delay} seconds...")
                    await asyncio.sleep(delay)
                else:
                    logger.error("MySQL audit logger initialization failed after all retries")
                    self.engine = None
                    self.async_session = None
                    self.initialized = False

    async def log_trade(
        self,
        order: dict[str, Any],
        result: dict[str, Any],
        signal_meta: dict[str, Any] | None = None,
    ) -> None:
        """Log trade execution to MySQL - non-blocking with retries"""
        if not self.initialized or not self.async_session:
            logger.debug("MySQL audit logger not available, skipping audit log")
            return

        # Create audit record
        audit_record = {
            "timestamp": datetime.utcnow(),
            "order_data": json.dumps(order, default=str),
            "result_data": json.dumps(result, default=str),
            "signal_meta": json.dumps(signal_meta or {}, default=str),
            "environment": settings.environment,
        }

        # Fire and forget - don't block the main operation
        asyncio.create_task(self._log_trade_async(audit_record))

    async def _log_trade_async(self, audit_record: dict[str, Any]) -> None:
        """Async task to log trade with retries and backoff"""
        for attempt in range(self.retry_attempts):
            try:
                async with self.async_session() as session:
                    # Insert into audit table
                    insert_query = text("""
                        INSERT INTO trade_audit 
                        (timestamp, order_data, result_data, signal_meta, environment)
                        VALUES (:timestamp, :order_data, :result_data, :signal_meta, :environment)
                    """)
                    
                    await session.execute(insert_query, audit_record)
                    await session.commit()
                    
                    logger.debug(
                        f"Trade audit logged: {audit_record.get('order_data', {}).get('type', 'unknown')} "
                        f"{audit_record.get('order_data', {}).get('side', 'unknown')} order"
                    )
                    return
                    
            except Exception as e:
                logger.warning(f"Trade audit logging attempt {attempt + 1} failed: {e}")
                if attempt < self.retry_attempts - 1:
                    delay = self.retry_delay * (self.backoff_multiplier ** attempt)
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"Trade audit logging failed after all retries: {e}")

    async def close(self) -> None:
        """Close MySQL connection"""
        if self.engine:
            try:
                await self.engine.dispose()
                logger.info("MySQL audit logger connection closed")
            except Exception as e:
                logger.warning(f"Error closing MySQL audit logger: {e}")


# Global audit logger instance
audit_logger = AuditLogger()
