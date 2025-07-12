import asyncio
import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
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
        self.engine: AsyncEngine | None = None
        self.async_session: sessionmaker[AsyncSession] | None = None
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
                    pool_recycle=3600,
                )

                # Create async session factory
                async_session = sessionmaker(
                    bind=self.engine, class_=AsyncSession, expire_on_commit=False
                )
                self.async_session = async_session

                # Test connection
                async with self.engine.begin() as conn:
                    await conn.execute(text("SELECT 1"))

                self.initialized = True
                logger.info("MySQL audit logger initialized successfully")
                return

            except Exception as e:
                logger.warning(
                    f"MySQL audit logger initialization attempt "
                    f"{attempt + 1} failed: {e}"
                )
                if attempt < self.retry_attempts - 1:
                    delay = self.retry_delay * (self.backoff_multiplier**attempt)
                    logger.info(f"Retrying MySQL initialization in {delay} seconds...")
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        "MySQL audit logger initialization failed after all retries"
                    )
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
            "timestamp": datetime.utcnow().isoformat(),
            "order_data": order,
            "result_data": result,
            "signal_meta": signal_meta or {},
        }

        # Non-blocking audit logging with retries
        for attempt in range(self.retry_attempts):
            try:
                async with self.async_session() as session:
                    # Insert audit record
                    await session.execute(
                        text(
                            """
                            INSERT INTO trade_audit_log 
                            (timestamp, order_data, result_data, signal_meta)
                            VALUES (:timestamp, :order_data, :result_data, :signal_meta)
                            """
                        ),
                        {
                            "timestamp": audit_record["timestamp"],
                            "order_data": json.dumps(audit_record["order_data"]),
                            "result_data": json.dumps(audit_record["result_data"]),
                            "signal_meta": json.dumps(audit_record["signal_meta"]),
                        },
                    )
                    await session.commit()

                    logger.debug(
                        f"Trade audit logged: "
                        f"{audit_record['order_data'].get('type', 'unknown')} "
                        f"{audit_record['order_data'].get('side', 'unknown')} order"
                    )
                    return

            except Exception as e:
                logger.warning(
                    f"Trade audit logging attempt {attempt + 1} failed: {e}"
                )
                if attempt < self.retry_attempts - 1:
                    delay = self.retry_delay * (self.backoff_multiplier**attempt)
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        f"Trade audit logging failed after all retries: {e}"
                    )

    async def close(self) -> None:
        """Close MySQL connection"""
        if self.engine:
            await self.engine.dispose()
            logger.info("MySQL audit logger connection closed")


# Global audit logger instance
audit_logger = AuditLogger()
