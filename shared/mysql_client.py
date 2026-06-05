"""
Data Manager client for position tracking operations.

Provides a typed interface over petrosa-data-manager HTTP API.
All write methods return PersistResult (not bare bool) so callers can observe
failures without relying on exception propagation.  Closes #448 Tasks 1.1-1.2.
"""

import logging
from datetime import datetime
from typing import Any, Optional

from shared.constants import UTC
from shared.retry import PersistResult, is_transient_error
from tradeengine.services.data_manager_client import (
    APIError,
    ConnectionError,
    DataManagerClient,
)

logger = logging.getLogger(__name__)


class DataManagerPositionClient:
    """
    Data Manager client for position tracking operations.

    All write methods return :class:`~shared.retry.PersistResult`; callers must
    check ``result.ok`` rather than relying on exceptions.  ``get_open_positions``
    raises on transient errors (letting the caller decide whether to retry or
    use the in-memory fallback).
    """

    def __init__(self) -> None:
        self.data_manager_client = DataManagerClient()
        logger.info("Initialized Data Manager position client")

    async def connect(self) -> None:
        await self.data_manager_client.connect()
        logger.info("Connected to Data Manager service")

    async def disconnect(self) -> None:
        await self.data_manager_client.disconnect()
        logger.info("Disconnected from Data Manager service")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _make_result(
        self,
        ok: bool,
        exc: Exception | None = None,
        *,
        operation: str = "",
        symbol: str = "",
        position_id: str = "",
    ) -> PersistResult:
        if ok:
            return PersistResult(
                ok=True, operation=operation, symbol=symbol, position_id=position_id
            )
        reason = (
            "transient" if exc is not None and is_transient_error(exc) else "permanent"
        )
        return PersistResult(
            ok=False,
            error=str(exc) if exc else "unknown",
            reason=reason,
            operation=operation,
            symbol=symbol,
            position_id=position_id,
        )

    # ------------------------------------------------------------------
    # Write methods — all return PersistResult
    # ------------------------------------------------------------------

    async def create_position(self, position_data: dict[str, Any]) -> PersistResult:
        """Insert a position record; returns PersistResult."""
        pid = str(
            position_data.get("position_id", position_data.get("contribution_id", ""))
        )
        sym = str(position_data.get("symbol", ""))
        try:
            response = await self.data_manager_client._client.insert_one(
                database="mysql", collection="positions", record=position_data
            )
            ok = bool(response.get("inserted_id") or response.get("inserted_count", 0))
            if ok:
                logger.info("Created position record %s via Data Manager", pid)
            else:
                logger.error(
                    "Failed to create position %s via Data Manager: 0-insert", pid
                )
            return self._make_result(
                ok, operation="create_position", symbol=sym, position_id=pid
            )
        except Exception as exc:
            logger.error("Failed to create position %s via Data Manager: %s", pid, exc)
            return self._make_result(
                False, exc, operation="create_position", symbol=sym, position_id=pid
            )

    async def update_position(
        self, position_id: str, update_data: dict[str, Any]
    ) -> PersistResult:
        """Update a position record; returns PersistResult."""
        sym = str(update_data.get("symbol", ""))
        try:
            response = await self.data_manager_client._client.update_one(
                database="mysql",
                collection="positions",
                filter={"position_id": position_id},
                update={"$set": update_data},
            )
            ok = response.get("modified_count", 0) > 0
            if not ok:
                logger.warning("No position found to update: %s", position_id)
            else:
                logger.info("Updated position record %s via Data Manager", position_id)
            return self._make_result(
                ok, operation="update_position", symbol=sym, position_id=position_id
            )
        except Exception as exc:
            logger.error(
                "Failed to update position %s via Data Manager: %s", position_id, exc
            )
            return self._make_result(
                False,
                exc,
                operation="update_position",
                symbol=sym,
                position_id=position_id,
            )

    async def update_position_risk_orders(
        self, position_id: str, update_data: dict[str, Any]
    ) -> PersistResult:
        """Update position risk orders; returns PersistResult."""
        try:
            response = await self.data_manager_client._client.update_one(
                database="mysql",
                collection="positions",
                filter={"position_id": position_id},
                update={"$set": update_data},
            )
            ok = response.get("modified_count", 0) > 0
            if not ok:
                logger.warning(
                    "No position found to update risk orders: %s", position_id
                )
            else:
                logger.info(
                    "Updated position %s risk orders via Data Manager", position_id
                )
            return self._make_result(
                ok, operation="update_position_risk_orders", position_id=position_id
            )
        except Exception as exc:
            logger.error(
                "Failed to update position risk orders %s: %s", position_id, exc
            )
            return self._make_result(
                False,
                exc,
                operation="update_position_risk_orders",
                position_id=position_id,
            )

    async def upsert_position(self, position_data: dict[str, Any]) -> PersistResult:
        """Upsert a position record; returns PersistResult."""
        sym = str(position_data.get("symbol", ""))
        pos_side = str(position_data.get("position_side", "LONG"))
        try:
            await self.data_manager_client._client.upsert_one(
                database="mysql",
                collection="positions",
                filter={"symbol": sym, "position_side": pos_side, "status": "open"},
                record=position_data,
            )
            logger.info("Upserted position %s %s via Data Manager", sym, pos_side)
            return self._make_result(True, operation="upsert_position", symbol=sym)
        except Exception as exc:
            logger.error("Failed to upsert position %s %s: %s", sym, pos_side, exc)
            return self._make_result(
                False, exc, operation="upsert_position", symbol=sym
            )

    async def close_position(
        self, symbol: str, position_side: str, update_data: dict[str, Any]
    ) -> PersistResult:
        """Mark a position as closed; returns PersistResult."""
        try:
            response = await self.data_manager_client._client.update_one(
                database="mysql",
                collection="positions",
                filter={
                    "symbol": symbol,
                    "position_side": position_side,
                    "status": "open",
                },
                update={"$set": update_data},
            )
            ok = response.get("modified_count", 0) > 0
            if not ok:
                logger.warning(
                    "No open position found to close: %s %s", symbol, position_side
                )
            else:
                logger.info(
                    "Closed position %s %s via Data Manager", symbol, position_side
                )
            return self._make_result(ok, operation="close_position", symbol=symbol)
        except Exception as exc:
            logger.error(
                "Failed to close position %s %s: %s", symbol, position_side, exc
            )
            return self._make_result(
                False, exc, operation="close_position", symbol=symbol
            )

    # ------------------------------------------------------------------
    # Read methods
    # ------------------------------------------------------------------

    async def get_position(self, position_id: str) -> dict[str, Any] | None:
        try:
            response = await self.data_manager_client._client.query(
                database="mysql",
                collection="positions",
                params={"filter": {"position_id": position_id}, "limit": 1},
            )
            if response and response.get("data"):
                return response["data"][0]
            return None
        except Exception as exc:
            logger.error(
                "Failed to get position %s via Data Manager: %s", position_id, exc
            )
            return None

    async def get_open_positions(
        self, strategy_id: str | None = None
    ) -> list[dict[str, Any]]:
        """
        Return open positions from data-manager.

        Raises APIError / ConnectionError on failure so the caller can decide
        whether to retry (AC1.5: no silent [] on transient error).
        """
        filter_dict: dict[str, Any] = {"status": "open"}
        if strategy_id:
            filter_dict["strategy_id"] = strategy_id
        response = await self.data_manager_client._client.query(
            database="mysql",
            collection="positions",
            params={
                "filter": filter_dict,
                "sort_by": "entry_time",
                "sort_order": "desc",
            },
        )
        positions = response.get("data", []) if response else []
        logger.info("Retrieved %d open positions via Data Manager", len(positions))
        return positions

    async def get_daily_pnl(self, date: str) -> float | None:
        try:
            response = await self.data_manager_client._client.query(
                database="mysql",
                collection="daily_pnl",
                params={"filter": {"date": date}, "limit": 1},
            )
            if response and response.get("data"):
                return response["data"][0].get("daily_pnl")
            return None
        except Exception as exc:
            logger.error("Failed to get daily P&L for %s: %s", date, exc)
            return None

    async def update_daily_pnl(self, date: str, daily_pnl: float) -> PersistResult:
        try:
            await self.data_manager_client._client.upsert_one(
                database="mysql",
                collection="daily_pnl",
                filter={"date": date},
                record={
                    "date": date,
                    "daily_pnl": daily_pnl,
                    "updated_at": datetime.now(UTC),
                },
            )
            logger.info(
                "Updated daily P&L for %s: %s via Data Manager", date, daily_pnl
            )
            return self._make_result(True, operation="update_daily_pnl")
        except Exception as exc:
            logger.error("Failed to update daily P&L for %s: %s", date, exc)
            return self._make_result(False, exc, operation="update_daily_pnl")

    async def health_check(self) -> dict[str, Any]:
        try:
            health = await self.data_manager_client._client.health()
            return {
                "status": "healthy"
                if health.get("status") == "healthy"
                else "unhealthy",
                "service": "data-manager",
                "details": health,
            }
        except Exception as exc:
            logger.error("Data Manager health check failed: %s", exc)
            return {"status": "unhealthy", "service": "data-manager", "error": str(exc)}


# Global Data Manager position client instance
position_client = DataManagerPositionClient()
