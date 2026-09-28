"""LEGACY — only ``strategy_position_manager``; removed by #632.

Data Manager client for strategy position tracking operations.

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

    async def _log_health_snapshot(self, pid: str) -> None:
        """Best-effort Data Manager health probe logged on a 0-insert failure.

        #596 AC: "Health check ... to catch DB issues early". A blocking
        pre-check before every position creation would add latency (and a
        new failure mode) to the hot trading path; instead this fires only
        on the failure path so operators get an immediate health snapshot
        correlated with the failed write, without slowing down the common
        case. Never raises — this is diagnostics only.
        """
        try:
            health = await self.health_check()
            logger.error(
                "Data Manager health at time of 0-insert for %s: %s", pid, health
            )
        except Exception as exc:  # pragma: no cover - defensive, diagnostics-only
            logger.warning("Health snapshot probe failed for %s: %s", pid, exc)

    # ------------------------------------------------------------------
    # Write methods — all return PersistResult
    # ------------------------------------------------------------------

    # Column names mirror scripts/create_strategy_positions_table.sql, which is
    # the checked-in copy of the strategy-positions-schema-job DDL.
    _STRATEGY_POSITION_COLUMNS = frozenset(
        {
            "strategy_position_id", "strategy_id", "signal_id", "symbol", "side",
            "entry_quantity", "entry_price", "entry_time", "entry_order_id",
            "take_profit_price", "stop_loss_price", "tp_order_id", "sl_order_id",
            "status", "exit_quantity", "exit_price", "exit_time", "exit_order_id",
            "close_reason", "realized_pnl", "realized_pnl_pct", "commission_total",
            "exchange_position_key", "strategy_metadata",
        }
    )
    _EXCHANGE_POSITION_COLUMNS = frozenset(
        {
            "exchange_position_key", "symbol", "side", "current_quantity",
            "weighted_avg_price", "unrealized_pnl", "first_entry_time",
            "last_update_time", "status", "contributing_strategies",
            "total_contributions",
        }
    )
    _CONTRIBUTION_COLUMNS = frozenset(
        {
            "contribution_id", "strategy_position_id", "exchange_position_key",
            "strategy_id", "symbol", "position_side", "contribution_quantity",
            "contribution_entry_price", "contribution_time", "position_sequence",
            "exchange_quantity_before", "exchange_quantity_after", "status",
            "close_reason", "exit_time", "exit_price", "contribution_pnl",
            "contribution_pnl_pct",
        }
    )

    @staticmethod
    def _columns(data: dict[str, Any], columns: frozenset[str]) -> dict[str, Any]:
        return {key: value for key, value in data.items() if key in columns}

    async def _create_dedicated(
        self,
        collection: str,
        data: dict[str, Any],
        key: str,
        columns: frozenset[str],
        operation: str,
    ) -> PersistResult:
        record = self._columns(data, columns)
        identifier = str(record.get(key, ""))
        symbol = str(record.get("symbol", ""))
        try:
            response = await self.data_manager_client._client.insert_one(
                database="mysql", collection=collection, record=record
            )
            inserted = bool(
                response.get("inserted_id") or response.get("inserted_count", 0)
            )
            duplicate = int(response.get("duplicates", 0) or 0) > 0
            return self._make_result(
                inserted or duplicate,
                operation=operation,
                symbol=symbol,
                position_id=identifier,
            )
        except Exception as exc:
            logger.error("Failed to create %s %s: %s", collection, identifier, exc)
            return self._make_result(
                False, exc, operation=operation, symbol=symbol, position_id=identifier
            )

    async def _update_dedicated(
        self,
        collection: str,
        key: str,
        value: str,
        data: dict[str, Any],
        columns: frozenset[str],
        operation: str,
    ) -> PersistResult:
        update = self._columns(data, columns)
        try:
            response = await self.data_manager_client._client.update_one(
                database="mysql", collection=collection, filter={key: value}, update=update
            )
            count = int(
                response.get("updated_count", response.get("modified_count", 0)) or 0
            )
            return self._make_result(
                count > 0, operation=operation, position_id=str(value)
            )
        except Exception as exc:
            logger.error("Failed to update %s %s=%s: %s", collection, key, value, exc)
            return self._make_result(
                False, exc, operation=operation, position_id=str(value)
            )

    async def create_strategy_position(self, position_data: dict[str, Any]) -> PersistResult:
        return await self._create_dedicated(
            "strategy_positions", position_data, "strategy_position_id",
            self._STRATEGY_POSITION_COLUMNS, "create_strategy_position",
        )

    async def update_strategy_position(
        self, strategy_position_id: str, update_data: dict[str, Any]
    ) -> PersistResult:
        return await self._update_dedicated(
            "strategy_positions", "strategy_position_id", strategy_position_id,
            update_data, self._STRATEGY_POSITION_COLUMNS, "update_strategy_position",
        )

    async def create_exchange_position(self, position_data: dict[str, Any]) -> PersistResult:
        return await self._create_dedicated(
            "exchange_positions", position_data, "exchange_position_key",
            self._EXCHANGE_POSITION_COLUMNS, "create_exchange_position",
        )

    async def update_exchange_position(
        self, exchange_position_key: str, update_data: dict[str, Any]
    ) -> PersistResult:
        return await self._update_dedicated(
            "exchange_positions", "exchange_position_key", exchange_position_key,
            update_data, self._EXCHANGE_POSITION_COLUMNS, "update_exchange_position",
        )

    async def create_position_contribution(
        self, contribution_data: dict[str, Any]
    ) -> PersistResult:
        return await self._create_dedicated(
            "position_contributions", contribution_data, "contribution_id",
            self._CONTRIBUTION_COLUMNS, "create_position_contribution",
        )

    async def update_position_contribution(
        self, contribution_id: str, update_data: dict[str, Any]
    ) -> PersistResult:
        return await self._update_dedicated(
            "position_contributions", "contribution_id", contribution_id,
            update_data, self._CONTRIBUTION_COLUMNS, "update_position_contribution",
        )

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
            inserted = bool(
                response.get("inserted_id") or response.get("inserted_count", 0)
            )
            duplicates = int(response.get("duplicates", 0) or 0)
            failed = int(response.get("failed", 0) or 0)
            # #598: `inserted_count == 0` is NOT automatically a failure. The
            # `positions` table has a UNIQUE `position_id`, so re-creating an
            # existing position takes data-manager's MySQL `INSERT IGNORE`
            # branch: the row is silently dropped, `inserted_count` is 0,
            # `duplicates` is 1 and the HTTP status is 200. That is an
            # idempotent no-op — the row we wanted is already in MySQL — so
            # the desired end state holds and the operation is a success.
            # Treating it as a failure was architecturally unwinnable: every
            # retry re-hit the same duplicate, burned all 5 attempts, and then
            # surfaced a bogus "never-persisted divergence" for a position
            # that demonstrably exists. A genuine failure (`failed > 0`, or a
            # 0-insert with no duplicate signal at all) still falls through to
            # the error path below.
            idempotent_duplicate = not inserted and failed == 0 and duplicates > 0
            legacy_duplicate = False
            if not inserted and failed == 0 and duplicates == 0 and pid:
                legacy_duplicate = await self.get_position(pid) is not None
            ok = inserted or idempotent_duplicate or legacy_duplicate
            if inserted:
                logger.info("Created position record %s via Data Manager", pid)
            elif idempotent_duplicate or legacy_duplicate:
                logger.info(
                    "Position %s already persisted (idempotent duplicate: "
                    "inserted_count=0, duplicates=%s, read_verified=%s) — "
                    "treating as success",
                    pid,
                    duplicates,
                    legacy_duplicate,
                )
            else:
                # #596: log the full Data Manager response instead of the bare
                # "0-insert" literal so the actual inserted_count/inserted_id
                # values (and any extra diagnostic fields data-manager returns)
                # are visible in logs, plus a reactive health snapshot to help
                # distinguish a DB-side outage from a constraint/schema issue.
                logger.error(
                    "Failed to create position %s via Data Manager: 0-insert "
                    "(response=%s)",
                    pid,
                    response,
                )
                await self._log_health_snapshot(pid)
            result = self._make_result(
                ok, operation="create_position", symbol=sym, position_id=pid
            )
            if idempotent_duplicate:
                # Surface the distinction to callers/metrics: this succeeded
                # because the row already existed, not because we wrote it.
                result.extra["idempotent_duplicate"] = True
            elif legacy_duplicate:
                result.extra["idempotent_duplicate"] = True
                result.extra["read_verified"] = True
            return result
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
                update=update_data,
            )
            updated_count = int(
                response.get("updated_count", response.get("modified_count", 0)) or 0
            )
            ok = updated_count > 0
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
                update=update_data,
            )
            updated_count = int(
                response.get("updated_count", response.get("modified_count", 0)) or 0
            )
            ok = updated_count > 0
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
        """Compatibility shim for pre-cutover callers; new code must not use it."""
        position_id = position_data.get("position_id")
        if not position_id:
            return self._make_result(
                False,
                ValueError("position_id is required"),
                operation="upsert_position",
            )
        try:
            await self.data_manager_client._client.upsert_one(
                database="mysql",
                collection="positions",
                filter={"position_id": position_id},
                record=position_data,
            )
            return self._make_result(
                True,
                operation="upsert_position",
                symbol=str(position_data.get("symbol", "")),
                position_id=str(position_id),
            )
        except Exception as exc:
            return self._make_result(
                False,
                exc,
                operation="upsert_position",
                symbol=str(position_data.get("symbol", "")),
                position_id=str(position_id),
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
                update=update_data,
            )
            updated_count = int(
                response.get("updated_count", response.get("modified_count", 0)) or 0
            )
            ok = updated_count > 0
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
                filter={"position_id": position_id},
                limit=1,
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
            filter=filter_dict,
            sort={"entry_time": -1},
        )
        positions = response.get("data", []) if response else []
        logger.info("Retrieved %d open positions via Data Manager", len(positions))
        return positions

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
