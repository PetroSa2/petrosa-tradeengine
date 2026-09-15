import logging
import os
import sys

import structlog
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


def get_logger(name: str = __name__, *, stdlib: bool = False):
    """Get a logger instance.

    Args:
        name: The name of the logger.
        stdlib: If True, returns a standard logging.Logger instead of a structlog logger.
    """
    if stdlib:
        return logging.getLogger(name)
    return structlog.get_logger(name)
