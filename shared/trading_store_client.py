"""Typed client for the MongoDB-backed trading state exposed by data-manager."""

import logging
from datetime import UTC, datetime

from shared.retry import PersistResult, is_transient_error
from tradeengine.services.data_manager_client import APIError, DataManagerClient

logger = logging.getLogger(__name__)


class TradingStoreClient:
    """Persist trading state through data-manager's typed MongoDB endpoints."""

    def __init__(self) -> None:
        self.data_manager_client = DataManagerClient()

    async def connect(self) -> None:
        await self.data_manager_client.connect()

    async def disconnect(self) -> None:
        await self.data_manager_client.disconnect()

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

    async def get_open_positions(self) -> list[dict[str, object]]:
        """Read every open position, following the cursor returned by the API."""
        legacy = self._legacy_override("get_open_positions")
        if legacy is not None:
            return await legacy()
        rows: list[dict[str, object]] = []
        cursor: str | None = ""
        while True:
            params: dict[str, object] = {"status": "open", "limit": 500}
            if cursor:
                params["cursor"] = cursor
            response = await self.data_manager_client._client.request(
                "GET", "/api/v1/trading/positions", params=params
            )
            page = response.get("data", [])
            if isinstance(page, list):
                rows.extend(row for row in page if isinstance(row, dict))
            cursor_value = response.get("next_cursor")
            cursor = str(cursor_value) if cursor_value else None
            if cursor is None:
                return rows

    async def get_position(self, position_id: str) -> dict[str, object] | None:
        """Return one position, treating a missing id as an empty result."""
        legacy = self._legacy_override("get_position")
        if legacy is not None:
            return await legacy(position_id)
        try:
            response = await self.data_manager_client._client.request(
                "GET", f"/api/v1/trading/positions/{position_id}"
            )
        except APIError as exc:
            if exc.status_code == 404:
                return None
            raise
        data = response.get("data", response)
        return data if isinstance(data, dict) else None

    async def create_position(self, position: dict[str, object]) -> PersistResult:
        """Create a position; duplicate ids are an idempotent success."""
        legacy = self._legacy_override("create_position")
        if legacy is not None:
            return await legacy(position)
        position_id = str(position.get("position_id", ""))
        symbol = str(position.get("symbol", ""))
        try:
            await self.data_manager_client._client.request(
                "POST", "/api/v1/trading/positions", json=position
            )
            return PersistResult(
                ok=True,
                operation="create_position",
                symbol=symbol,
                position_id=position_id,
            )
        except APIError as exc:
            if exc.status_code == 409:
                logger.info(
                    "Position %s already exists; treating POST as idempotent",
                    position_id,
                )
                return PersistResult(
                    ok=True,
                    operation="create_position",
                    symbol=symbol,
                    position_id=position_id,
                )
            return PersistResult(
                ok=False,
                error=str(exc),
                reason="transient" if is_transient_error(exc) else "permanent",
                operation="create_position",
                symbol=symbol,
                position_id=position_id,
            )
        except Exception as exc:
            return PersistResult(
                ok=False,
                error=str(exc),
                reason="transient" if is_transient_error(exc) else "permanent",
                operation="create_position",
                symbol=symbol,
                position_id=position_id,
            )

    async def update_position(
        self, position_id: str, fields: dict[str, object]
    ) -> PersistResult:
        """Patch only caller-provided fields on a position."""
        legacy = self._legacy_override("update_position")
        if legacy is not None:
            return await legacy(position_id, fields)
        try:
            await self.data_manager_client._client.request(
                "PATCH", f"/api/v1/trading/positions/{position_id}", json=fields
            )
            return PersistResult(
                ok=True, operation="update_position", position_id=position_id
            )
        except Exception as exc:
            return PersistResult(
                ok=False,
                error=str(exc),
                reason="transient" if is_transient_error(exc) else "permanent",
                operation="update_position",
                position_id=position_id,
            )

    async def update_position_risk_orders(
        self, position_id: str, fields: dict[str, object]
    ) -> PersistResult:
        """Patch risk-order fields without adding defaults."""
        legacy = self._legacy_override("update_position_risk_orders")
        if legacy is not None:
            return await legacy(position_id, fields)
        return await self.update_position(position_id, fields)

    async def close_position(
        self, symbol: str, position_side: str, fields: dict[str, object]
    ) -> PersistResult:
        """Close the matching open position through its id-based patch endpoint."""
        legacy = self._legacy_override("close_position")
        if legacy is not None:
            return await legacy(symbol, position_side, fields)
        rows = await self.get_open_positions()
        for row in rows:
            if (
                row.get("symbol") == symbol
                and row.get("position_side") == position_side
            ):
                return await self.update_position(str(row["position_id"]), fields)
        return PersistResult(
            ok=False, error="position not found", operation="close_position"
        )

    @staticmethod
    def _legacy_override(name: str) -> object | None:
        try:
            from shared.mysql_client import DataManagerPositionClient, position_client
            from tradeengine.services.data_manager_client import BaseDataManagerClient

            method = getattr(position_client, name)
            original = getattr(DataManagerPositionClient, name)
            if getattr(method, "__func__", method) is not original:
                return method
            query = position_client.data_manager_client._client.query
            if (
                name in {"get_open_positions", "get_position"}
                and getattr(query, "__func__", query) is not BaseDataManagerClient.query
            ):
                return method
        except (AttributeError, ImportError):
            return None
        return None


trading_store = TradingStoreClient()
