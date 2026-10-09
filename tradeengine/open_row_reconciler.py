"""Open position rows against the exchange position (petrosa-tradeengine#739).

Tradeengine writes one ``positions`` row per entry order, but the exchange holds one netted position per
(symbol, side). A row is closed only by the path that knows its ``position_id`` (its own OCO pair, the close
paths that carry its id), so when the exchange position is closed or reduced another way (a netted stop or
take-profit on the combined quantity, remediation, a manual close) the other rows of that side stay ``open`` for
ever and the ledger shows more open groups than the exchange has.

The model: **the exchange quantity is the truth for a (symbol, side)**. When the open rows add up to more than the
exchange holds, the excess is allocated oldest first (FIFO): older rows are closed in full, the next one is
reduced. The closes are booked without a P&L (``pnl_unknown``): no fill is attributed to them, and a made-up
price would be a made-up P&L. Rows younger than a grace period are left alone (the exchange may not show their
fill yet), and a plan must be seen on consecutive passes before it is applied. Mode ``dry_run`` (the default)
only logs and measures the plan; ``close`` applies it; ``off`` disables the pass.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from prometheus_client import Counter, Gauge

logger = logging.getLogger(__name__)

RowReconcileMode = Literal["off", "dry_run", "close"]
CLOSE_REASON = "reconciled_to_exchange"
_EPS = 1e-9

open_row_excess_quantity = Gauge(
    "tradeengine_open_row_excess_quantity",
    "Open position-row quantity above the exchange quantity, per (symbol, side) (#739)",
    ["symbol", "side"],
)
open_rows_reconciled_total = Counter(
    "tradeengine_open_rows_reconciled_total",
    "Open position rows closed or reduced to match the exchange quantity (#739)",
    ["symbol", "side", "action"],
)


def normalise_side(value: Any) -> str:
    """LONG or SHORT (BUY / SELL rows are the same sides)."""
    side = str(value or "").strip().upper()
    return {"BUY": "LONG", "SELL": "SHORT"}.get(side, side)


@dataclass
class RowAction:
    position_id: str
    action: Literal["close", "reduce"]
    quantity: float  # the quantity taken off the row
    row_quantity: float  # the row's open quantity before


@dataclass
class RowPlan:
    symbol: str
    side: str
    exchange_quantity: float
    ledger_quantity: float  # open rows older than the grace period
    excess: float
    actions: list[RowAction] = field(default_factory=list)
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "exchange_quantity": self.exchange_quantity,
            "ledger_quantity": self.ledger_quantity,
            "excess": self.excess,
            "note": self.note,
            "actions": [a.__dict__ for a in self.actions],
        }


def plan_rows(
    symbol: str,
    side: str,
    rows: list[dict[str, Any]],
    exchange_quantity: float,
) -> RowPlan:
    """Allocate the excess of the open rows over the exchange quantity, oldest row first.

    ``rows`` carry ``position_id``, ``quantity`` and ``entry_time`` (an ISO string or a datetime). Rows are
    taken in entry order; a row with an unknown entry time goes last. When the exchange holds more than the
    rows, nothing is planned (the note says so): the ledger is missing quantity, which closing rows cannot fix.
    """

    def key(row: dict[str, Any]) -> tuple[int, str]:
        entry = row.get("entry_time")
        text = entry.isoformat() if isinstance(entry, datetime) else str(entry or "")
        return (0 if text else 1, text)

    ordered = sorted(rows, key=key)
    ledger = sum(float(r.get("quantity") or 0.0) for r in ordered)
    excess = ledger - exchange_quantity
    plan = RowPlan(
        symbol=symbol,
        side=side,
        exchange_quantity=exchange_quantity,
        ledger_quantity=ledger,
        excess=max(excess, 0.0),
    )
    if excess <= _EPS:
        if excess < -_EPS:
            plan.note = "exchange_exceeds_ledger"
        return plan
    left = excess
    for row in ordered:
        if left <= _EPS:
            break
        quantity = float(row.get("quantity") or 0.0)
        if quantity <= _EPS:
            continue
        take = min(quantity, left)
        plan.actions.append(
            RowAction(
                position_id=str(row.get("position_id")),
                action="close" if take >= quantity - _EPS else "reduce",
                quantity=take,
                row_quantity=quantity,
            )
        )
        left -= take
    return plan


class OpenRowReconciler:
    """Plans (and in ``close`` mode applies) the row allocation for every (symbol, side) on each pass."""

    def __init__(
        self,
        position_manager: Any,
        mode: str = "dry_run",
        grace_seconds: float = 300.0,
        confirm_passes: int = 2,
        clock: Any = time.time,
    ) -> None:
        self._pm = position_manager
        self.mode: RowReconcileMode = self._coerce(mode)
        self._grace = float(grace_seconds)
        self._confirm = max(int(confirm_passes), 1)
        self._clock = clock
        self._seen: dict[tuple[str, str], tuple[float, int]] = {}
        self.last_plans: list[dict[str, Any]] = []

    @staticmethod
    def _coerce(mode: str) -> RowReconcileMode:
        normalized = (mode or "dry_run").strip().lower()
        if normalized not in ("off", "dry_run", "close"):
            logger.warning("OpenRowReconciler: unknown mode %r; using dry_run", mode)
            return "dry_run"
        return normalized  # type: ignore[return-value]

    def _open_rows(self) -> dict[tuple[str, str], list[dict[str, Any]]]:
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        now = self._clock()
        for record in list(getattr(self._pm, "position_records", {}).values()):
            if str(record.get("status", "open")).lower() not in (
                "open",
                "partially_closed",
            ):
                continue
            if float(record.get("quantity") or 0.0) <= _EPS or not record.get(
                "position_id"
            ):
                continue
            entry = record.get("entry_time")
            if isinstance(entry, str):
                try:
                    entry = datetime.fromisoformat(entry.replace("Z", "+00:00"))
                except ValueError:
                    entry = None
            if isinstance(entry, datetime):
                stamp = (
                    entry if entry.tzinfo else entry.replace(tzinfo=UTC)
                ).timestamp()
                if now - stamp < self._grace:
                    continue  # too young: the exchange may not show its fill yet
            key = (
                str(record.get("symbol")),
                normalise_side(record.get("position_side")),
            )
            grouped.setdefault(key, []).append(record)
        return grouped

    async def reconcile(
        self, binance_positions: dict[tuple[str, str], dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """One pass against the exchange positions of ``reconcile_once``; returns the plans with an excess."""
        if self.mode == "off":
            return []
        results: list[dict[str, Any]] = []
        grouped = self._open_rows()
        for (symbol, side), rows in sorted(grouped.items()):
            exchange = binance_positions.get((symbol, side))
            quantity = abs(float((exchange or {}).get("positionAmt", 0.0)))
            plan = plan_rows(symbol, side, rows, quantity)
            open_row_excess_quantity.labels(symbol=symbol, side=side).set(plan.excess)
            if not plan.actions:
                self._seen.pop((symbol, side), None)
                continue
            previous = self._seen.get((symbol, side))
            passes = (
                previous[1] + 1
                if previous is not None and abs(previous[0] - plan.excess) <= 1e-6
                else 1
            )
            self._seen[(symbol, side)] = (plan.excess, passes)
            record = {
                **plan.as_dict(),
                "passes": passes,
                "mode": self.mode,
                "applied": False,
            }
            logger.warning(
                "OPEN_ROWS_EXCESS %s/%s: ledger %.8f vs exchange %.8f, %d row(s) to take off (%s, pass %d/%d)",
                symbol,
                side,
                plan.ledger_quantity,
                plan.exchange_quantity,
                len(plan.actions),
                self.mode,
                passes,
                self._confirm,
            )
            if self.mode == "close" and passes >= self._confirm:
                record["applied"] = await self._apply(plan)
                if record["applied"]:
                    self._seen.pop((symbol, side), None)
            results.append(record)
        self.last_plans = results
        return results

    async def _apply(self, plan: RowPlan) -> bool:
        stamp = int(self._clock())
        ok = True
        for action in plan.actions:
            try:
                result = await self._pm.record_position_close(
                    position_id=action.position_id,
                    exit_price=None,
                    exit_qty=action.quantity,
                    exit_order_id=f"reconcile-{plan.symbol}-{plan.side}-{action.position_id}-{stamp}",
                    exit_time=datetime.now(UTC),
                    close_reason=CLOSE_REASON,
                    pnl_unknown=True,
                )
                if result is None:
                    ok = False
                    logger.error(
                        "OpenRowReconciler: close returned no mutation for row %s",
                        action.position_id,
                    )
                    continue
                open_rows_reconciled_total.labels(
                    symbol=plan.symbol, side=plan.side, action=action.action
                ).inc()
            except Exception:
                ok = False
                logger.exception(
                    "OpenRowReconciler: could not %s row %s",
                    action.action,
                    action.position_id,
                )
        return ok
