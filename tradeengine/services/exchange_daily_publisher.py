"""Publish Binance exchange truth to the daily ledger contract.

The publisher is deliberately independent of the dispatcher so the backfill CLI and
the scheduled job use exactly the same pagination, Decimal, and payload code.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sys
import uuid
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from tradeengine.metrics import (
    ledger_publish_last_success_timestamp,
    ledger_publish_total,
)

logger = logging.getLogger(__name__)

_INCOME_LIMIT = 1000


def utc_day_bounds(day: str) -> tuple[int, int]:
    start = datetime.fromisoformat(day).replace(tzinfo=UTC)
    return int(start.timestamp() * 1000), int(
        (start + timedelta(days=1)).timestamp() * 1000
    )


def decimal_string(value: Any) -> str:
    """Return an exchange amount without binary float drift."""
    return format(Decimal(str(value)), "f")


def _sum(values: list[str]) -> str:
    return decimal_string(sum((Decimal(value) for value in values), Decimal("0")))


class ExchangeDailyPublisher:
    """Collect and publish complete daily snapshots from an injected exchange."""

    def __init__(
        self,
        exchange: Any,
        data_manager: Any,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        run_id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
        grace_minutes: int | None = None,
    ) -> None:
        self.exchange = exchange
        self.data_manager = data_manager
        self.clock = clock
        self.run_id_factory = run_id_factory
        self.grace_minutes = (
            grace_minutes
            if grace_minutes is not None
            else int(os.getenv("TE_LEDGER_FINAL_GRACE_MINUTES", "5"))
        )

    @property
    def client(self) -> Any:
        client = getattr(self.exchange, "client", self.exchange)
        if client is None:
            raise RuntimeError("Binance client is not initialized")
        return client

    async def _call(self, method: str, **kwargs: Any) -> Any:
        return await asyncio.to_thread(getattr(self.client, method), **kwargs)

    async def income_rows(self, start_ms: int, end_ms: int) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        page_start = start_ms
        while True:
            page = await self._call(
                "futures_income_history",
                startTime=page_start,
                endTime=end_ms - 1,
                limit=_INCOME_LIMIT,
            )
            page = list(page or [])
            rows.extend(page)
            if len(page) < _INCOME_LIMIT:
                return rows
            last_time = int(page[-1].get("time", page_start))
            if last_time < page_start:
                raise RuntimeError("Binance income history pagination moved backwards")
            page_start = last_time + 1

    @staticmethod
    def aggregate(rows: list[dict[str, Any]], day: str) -> dict[str, Any]:
        grouped: dict[tuple[str, str], dict[str, list[str]]] = defaultdict(
            lambda: defaultdict(list)
        )
        times = [int(row["time"]) for row in rows]
        for row in rows:
            grouped[(str(row.get("symbol") or ""), str(row.get("asset") or ""))][
                str(row.get("incomeType") or "OTHER")
            ].append(decimal_string(row.get("income", "0")))
        payload_rows = [
            {
                "symbol": symbol,
                "asset": asset,
                "income_by_type": {
                    income_type: _sum(amounts)
                    for income_type, amounts in sorted(types.items())
                },
            }
            for (symbol, asset), types in sorted(grouped.items())
        ]
        return {
            "day": day,
            "source_run_id": "",
            "is_final": False,
            "row_count": len(payload_rows),
            "first_income_time_ms": min(times) if times else None,
            "last_income_time_ms": max(times) if times else None,
            "rows": payload_rows,
            "wallet_balance": "0",
            "balance_as_of_ms": 0,
            "income_after_day_end": {},
        }

    async def _account_snapshot(self, as_of_ms: int) -> tuple[str, int]:
        account = await self._call("futures_account")
        return decimal_string(account.get("totalWalletBalance", "0")), as_of_ms

    @staticmethod
    def _income_after_day_end(rows: list[dict[str, Any]]) -> dict[str, str]:
        totals: dict[str, list[str]] = defaultdict(list)
        for row in rows:
            totals[str(row.get("incomeType") or "OTHER")].append(
                decimal_string(row.get("income", "0"))
            )
        return {
            income_type: _sum(values) for income_type, values in sorted(totals.items())
        }

    async def _positions_snapshot(self, as_of_ms: int) -> None:
        positions = await self._call("futures_position_information")
        rows = []
        for position in positions or []:
            if Decimal(str(position.get("positionAmt", "0"))) == 0:
                continue
            rows.append(
                {
                    "symbol": str(position.get("symbol", "")),
                    "position_side": str(position.get("positionSide", "BOTH")),
                    "quantity": decimal_string(position.get("positionAmt", "0")),
                    "entry_price": decimal_string(position.get("entryPrice", "0")),
                    "mark_price": decimal_string(position.get("markPrice", "0")),
                    "unrealized_pnl": decimal_string(
                        position.get("unRealizedProfit", "0")
                    ),
                }
            )
        await self.data_manager.publish_exchange_positions_ledger(
            as_of_ms,
            {
                "as_of_ms": as_of_ms,
                "source_run_id": self.run_id_factory(),
                "rows": rows,
            },
        )

    async def publish_day(
        self, day: str, *, is_final: bool = False, apply: bool = True
    ) -> dict[str, Any]:
        start_ms, end_ms = utc_day_bounds(day)
        monitor = getattr(self.exchange, "rate_monitor", None)
        if monitor is not None and getattr(monitor, "polling_paused", False):
            ledger_publish_total.labels(result="throttled").inc()
            return {"day": day, "result": "throttled"}
        try:
            rows = await self.income_rows(start_ms, end_ms)
            payload = self.aggregate(rows, day)
            now_ms = int(self.clock().timestamp() * 1000)
            payload["source_run_id"] = self.run_id_factory()
            payload["is_final"] = is_final
            if is_final:
                _, day_end_ms = utc_day_bounds(day)
                after_rows = await self.income_rows(day_end_ms, now_ms)
                payload["income_after_day_end"] = self._income_after_day_end(after_rows)
            (
                payload["wallet_balance"],
                payload["balance_as_of_ms"],
            ) = await self._account_snapshot(now_ms)
            if apply:
                await self.data_manager.publish_exchange_daily_ledger(day, payload)
                await self._positions_snapshot(now_ms)
            ledger_publish_total.labels(result="success").inc()
            ledger_publish_last_success_timestamp.set(self.clock().timestamp())
            return payload
        except Exception:
            if monitor is not None:
                monitor.record_error(sys.exc_info()[1])
            result = "exchange_error"
            if isinstance(sys.exc_info()[1], ConnectionError | TimeoutError):
                result = "error"
            ledger_publish_total.labels(result=result).inc()
            logger.exception("Exchange ledger publish failed for %s", day)
            return {"day": day, "result": result}

    async def run_cycle(self, *, apply: bool = True) -> list[dict[str, Any]]:
        now = self.clock()
        day = now.date().isoformat()
        results = [await self.publish_day(day, apply=apply)]
        previous = (now - timedelta(days=1)).date().isoformat()
        if now.hour == 0 and now.minute < self.grace_minutes:
            return results
        results.append(await self.publish_day(previous, is_final=True, apply=apply))
        return results


def payload_hash(payload: dict[str, Any]) -> str:
    economic = {
        key: payload.get(key)
        for key in ("rows", "row_count", "last_income_time_ms", "is_final")
    }
    return hashlib.sha256(
        json.dumps(economic, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
