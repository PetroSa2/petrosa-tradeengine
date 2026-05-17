"""Audit logging for trading operations.

Current implementation is a stdout stub: structured log lines are emitted via
Python's `logging` module. There is no remote persistence layer wired up here.

The previous version of this module exposed `connected = False` while leaving
the actual audit calls operational at the log level. Health probes reported
`audit_logger.connected = false` and dispatcher call sites gated on
`enabled and connected`, which silently no-op'd every audit write while
operators saw what looked like a connectivity problem.

The contract is now explicit:

- `enabled` controls whether anything is emitted at all.
- `backend` names where the records go (currently always ``"stdout"``).
- `mode` reports ``"stub"`` until a persistent backend is wired up.
- `is_persistent` is False until that happens.

The ``connected`` attribute is preserved for backwards compatibility (some
tests still patch it) but is no longer consulted by any gate. It mirrors
``is_persistent`` so the value remains truthful.

A separate MySQL audit logger lives in :mod:`shared.logger`; do not conflate
the two.
"""

import logging
from typing import Any

from shared.config import Settings


class AuditLogger:
    """Stdout audit logger stub for trading operations."""

    backend = "stdout"
    mode = "stub"
    is_persistent = False

    def __init__(self) -> None:
        self.settings = Settings()
        self.enabled = True
        # Deprecated. Kept for backwards-compat with tests that patch it.
        # Mirrors is_persistent (False for the stub backend).
        self.connected = self.is_persistent
        self.logger = logging.getLogger(__name__)

    def health(self) -> dict[str, Any]:
        """Return audit logger health/contract for /health payloads."""
        return {
            "status": "healthy" if self.enabled else "disabled",
            "enabled": self.enabled,
            "backend": self.backend,
            "mode": self.mode,
            "is_persistent": self.is_persistent,
        }

    def log_signal(self, signal_data: dict[str, Any]) -> None:
        """Log trading signal for audit purposes"""
        if not self.enabled:
            return

        try:
            self.logger.info(f"Signal logged: {signal_data}")
        except Exception as e:
            self.logger.error(f"Failed to log signal: {e}")

    def log_trade(self, trade_data: dict[str, Any]) -> None:
        """Log trade execution for audit purposes"""
        if not self.enabled:
            return

        try:
            self.logger.info(f"Trade logged: {trade_data}")
        except Exception as e:
            self.logger.error(f"Failed to log trade: {e}")

    def log_order(self, order_data: dict[str, Any], status: str | None = None) -> None:
        """Log order placement for audit purposes"""
        if not self.enabled:
            return
        try:
            self.logger.info(f"Order logged: {order_data}, status: {status}")
        except Exception as e:
            self.logger.error(f"Failed to log order: {e}")

    def log_error(
        self, error_data: dict[str, Any], context: dict[str, Any] | None = None
    ) -> None:
        """Log error for audit purposes"""
        if not self.enabled:
            return
        try:
            self.logger.error(f"Error logged: {error_data}, context: {context}")
        except Exception as e:
            self.logger.error(f"Failed to log error: {e}")

    def log_position(
        self, position_data: dict[str, Any], status: str | None = None
    ) -> None:
        """Log position update for audit purposes"""
        if not self.enabled:
            return
        try:
            self.logger.info(f"Position logged: {position_data}, status: {status}")
        except Exception as e:
            self.logger.error(f"Failed to log position: {e}")

    def log_event(self, event_type: str, event_data: dict[str, Any]) -> None:
        """Log generic event for audit purposes"""
        if not self.enabled:
            return
        try:
            self.logger.info(f"Event [{event_type}] logged: {event_data}")
        except Exception as e:
            self.logger.error(f"Failed to log event: {e}")

    def log_account(self, account_data: dict[str, Any]) -> None:
        """Log account update for audit purposes"""
        if not self.enabled:
            return

        try:
            self.logger.info(f"Account logged: {account_data}")
        except Exception as e:
            self.logger.error(f"Failed to log account: {e}")

    def log_risk(self, risk_data: dict[str, Any]) -> None:
        """Log risk management event for audit purposes"""
        if not self.enabled:
            return

        try:
            self.logger.info(f"Risk logged: {risk_data}")
        except Exception as e:
            self.logger.error(f"Failed to log risk: {e}")

    def log_performance(self, performance_data: dict[str, Any]) -> None:
        """Log performance metrics for audit purposes"""
        if not self.enabled:
            return

        try:
            self.logger.info(f"Performance logged: {performance_data}")
        except Exception as e:
            self.logger.error(f"Failed to log performance: {e}")


# Global audit logger instance
audit_logger = AuditLogger()
