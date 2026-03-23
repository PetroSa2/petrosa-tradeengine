import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from shared.constants import UTC
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from structlog.stdlib import LoggerFactory

from shared.config import settings


def configure_structlog():
    """Configure structlog for structured logging."""
    # Configure standard library logging
    log_level = getattr(logging, settings.log_level.upper())
    log_format = os.getenv("LOG_FORMAT", "text").lower()

    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    processors = [
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    if log_format == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        context_class=dict,
        logger_factory=LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


# Initialize structlog on import
configure_structlog()

logger = structlog.get_logger(__name__)


class AuditLogger:
    def __init__(self) -> None:
        self.engine: AsyncEngine | None = None
        self.async_session: async_sessionmaker[AsyncSession] | None = None
        self.initialized = False
        self.retry_attempts = 3
        self.retry_delay = 1.0
        self.backoff_multiplier = 2.0

    async def initialize(self) -> None:
        """Initialize MySQL connection with retries and backoff"""
        for attempt in range(self.retry_attempts):
            try:
                # Create async engine for SQLAlchemy
                mysql_uri = settings.mysql_uri or "mysql+aiomysql://localhost/petrosa"
                self.engine = create_async_engine(
                    mysql_uri,
                    echo=False,
                    pool_size=5,
                    max_overflow=10,
                    pool_pre_ping=True,
                    pool_recycle=3600,
                )

                # Create async session factory
                self.async_session = async_sessionmaker(
                    self.engine, class_=AsyncSession, expire_on_commit=False
                )

                # Test connection
                async with self.engine.begin() as conn:
                    await conn.run_sync(lambda c: None)

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
        audit_record: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "order_data": order,
            "result_data": result,
            "signal_meta": signal_meta or {},
        }

        for attempt in range(self.retry_attempts):
            try:
                async with self.async_session() as session:
                    await session.execute(
                        text(
                            "INSERT INTO trade_audit_log (timestamp, order_data, result_data, signal_meta) VALUES (:timestamp, :order_data, :result_data, :signal_meta)"
                        ),
                        {
                            "timestamp": audit_record["timestamp"],
                            "order_data": json.dumps(audit_record["order_data"]),
                            "result_data": json.dumps(audit_record["result_data"]),
                            "signal_meta": json.dumps(audit_record["signal_meta"]),
                        },
                    )
                    await session.commit()

                    order_data = audit_record["order_data"]
                    if isinstance(order_data, dict):
                        order_type = order_data.get("type", "unknown")
                        order_side = order_data.get("side", "unknown")
                    else:
                        order_type = order_side = "unknown"
                    logger.debug(f"Trade audit logged: {order_type} {order_side} order")
                    return

            except Exception as e:
                logger.warning(f"Trade audit logging attempt {attempt + 1} failed: {e}")
                if attempt < self.retry_attempts - 1:
                    delay = self.retry_delay * (self.backoff_multiplier**attempt)
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"Trade audit logging failed after all retries: {e}")

    async def close(self) -> None:
        """Close MySQL connection"""
        if self.engine:
            await self.engine.dispose()
            logger.info("MySQL audit logger connection closed")


# Global audit logger instance
audit_logger = AuditLogger()


def get_logger(name: str = __name__, *, stdlib: bool = False):
    """Get a logger instance.

    Args:
        name: The name of the logger.
        stdlib: If True, returns a standard logging.Logger instead of a structlog logger.
    """
    if stdlib:
        return logging.getLogger(name)
    return structlog.get_logger(name)
