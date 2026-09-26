"""Typed client for the MongoDB-backed trading state exposed by data-manager."""

import logging
from datetime import UTC, datetime

from shared.retry import PersistResult, is_transient_error
from tradeengine.services.data_manager_client import APIError, DataManagerClient

logger = logging.getLogger(__name__)


class TradingStoreClient:
    """Persist daily P&L through data-manager's typed trading endpoints."""

    def __init__(self) -> None:
        self.data_manager_client = DataManagerClient()

    async def get_daily_pnl(self, date: str) -> float | None:
        """Return the MongoDB daily-P&L row, treating a missing row as empty."""
        try:
            response = await self.data_manager_client._client.request(
                "GET", f"/api/v1/trading/daily-pnl/{date}"
            )
        except APIError as exc:
            if exc.status_code == 404:
                return None
            raise
        return float(response["daily_pnl"])

    async def update_daily_pnl(self, date: str, value: float) -> PersistResult:
        """Idempotently store an absolute daily-P&L value."""
        try:
            await self.data_manager_client._client.request(
                "PUT",
                f"/api/v1/trading/daily-pnl/{date}",
                json={
                    "daily_pnl": value,
                    "updated_at": datetime.now(UTC).isoformat(),
                },
            )
            logger.info(
                "Updated daily P&L for %s: %s via MongoDB trading store", date, value
            )
            return PersistResult(ok=True, operation="update_daily_pnl")
        except Exception as exc:
            logger.error(
                "Failed to update daily P&L for %s (store=mongodb): %s", date, exc
            )
            return PersistResult(
                ok=False,
                error=str(exc),
                reason="transient" if is_transient_error(exc) else "permanent",
                operation="update_daily_pnl",
            )


trading_store = TradingStoreClient()
