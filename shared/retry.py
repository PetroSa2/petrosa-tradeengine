"""
Transient-error classification and PersistResult for position persistence.

Ported from extractor/utils/error_classifier.py (same pattern, async-native).
Closes #448 Task 1.1.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from tradeengine.services.data_manager_client import APIError, ConnectionError

logger = logging.getLogger(__name__)

# HTTP status codes that indicate a transient server-side problem
_TRANSIENT_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


@dataclass
class PersistResult:
    """Structured result from a position-persistence operation."""

    ok: bool
    attempts: int = 1
    error: str = ""
    reason: str = ""
    operation: str = ""
    symbol: str = ""
    position_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        return not self.ok

    @property
    def is_transient(self) -> bool:
        return self.reason == "transient"


def is_transient_error(exc: Exception) -> bool:
    """Return True when *exc* represents a failure worth retrying."""
    if isinstance(exc, ConnectionError):
        return True
    if isinstance(exc, APIError):
        if exc.status_code is not None:
            return exc.status_code in _TRANSIENT_STATUS_CODES
        # No status code means transport failure — treat as transient
        return True
    return False
