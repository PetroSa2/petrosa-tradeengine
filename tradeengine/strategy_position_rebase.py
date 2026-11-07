"""One-shot re-base of persisted open strategy rows onto what the exchange holds (petrosa-tradeengine#837).

Persisted ``strategy_positions`` rows that are not in the in-memory tracker (written before a restart) were
never reconciled, so they piled up as ``open``. For each (symbol, side) this module plans, then optionally
applies, a ledger-only re-base:

- the exchange holds nothing on the key: rows older than the grace period are closed ``closed_externally`` with
  reason ``no_exchange_position``, through #831's closing-fills lookup (realized P&L when attributable, else a
  named reason);
- the exchange holds quantity Q on the key: the NEWEST rows whose remaining quantity covers Q are kept (the last
  one possibly partially) and marked ``inferred_coverage: newest-first``; the older excess is closed with reason
  ``exceeds_exchange_quantity`` and ``pnl_source: unattributed`` (no invented P&L).

Rows inside the grace period are always kept. A one-way (``BOTH``) key is skipped, never guessed. Every write is
audited in ``strategy_metadata.reconcile_rebase`` with the row's previous values and the ``run_id``, so a run can
be rolled back from those fields alone (:func:`build_rollback_update`).

No order is placed, cancelled or amended: the only writes are strategy-row updates through data-manager.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from shared.constants import UTC
from tradeengine.external_close_fills import (
    PNL_SOURCE_EXCHANGE_FILLS,
    PNL_SOURCE_UNATTRIBUTED,
    FillsFetcher,
    fetch_closure,
    remaining_quantity,
)
from tradeengine.metrics import strategy_position_external_close_total

logger = logging.getLogger(__name__)

OPEN_STATUSES = ("open", "partially_closed")
REASON_NO_EXCHANGE_POSITION = "no_exchange_position"
REASON_EXCEEDS_EXCHANGE_QUANTITY = "exceeds_exchange_quantity"
COVERAGE_METHOD = "newest-first"
DEFAULT_GRACE_SECONDS = 300
#: The row fields a closure changes; their previous values are kept for the rollback.
RESTORABLE_FIELDS = (
    "status",
    "exit_time",
    "exit_price",
    "exit_quantity",
    "exit_order_id",
    "close_reason",
    "realized_pnl",
    "gross_realized_pnl",
    "realized_pnl_pct",
    "commission_total",
)
#: The row fields an attributed (``exchange_fills``) closure writes, from the manager's own close logic.
_ATTRIBUTED_FIELDS = (
    "exit_time",
    "exit_price",
    "exit_quantity",
    "exit_order_id",
    "realized_pnl",
    "gross_realized_pnl",
    "realized_pnl_pct",
    "commission_total",
)


def _dec(value: Any) -> Decimal:
    try:
        return abs(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _entry_time(row: dict[str, Any]) -> datetime | None:
    value = row.get("entry_time")
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


@dataclass
class KeyPlan:
    symbol: str
    side: str
    exchange_quantity: Decimal | None
    keep: list[dict[str, Any]] = field(default_factory=list)
    close: list[dict[str, Any]] = field(default_factory=list)
    reason: str | None = None
    skipped: str | None = None

    def summary(self) -> dict[str, Any]:
        kept_qty = sum((remaining_quantity(k["row"]) for k in self.keep), Decimal("0"))
        return {
            "symbol": self.symbol,
            "side": self.side,
            "exchange_quantity": None
            if self.exchange_quantity is None
            else str(self.exchange_quantity),
            "rows_kept": len(self.keep),
            "kept_remaining_quantity": str(kept_qty),
            "partial_row": next(
                (
                    k["row"].get("strategy_position_id")
                    for k in self.keep
                    if k["partial"]
                ),
                None,
            ),
            "rows_to_close": len(self.close),
            "close_reason": self.reason if self.close else None,
            "close_remaining_quantity": str(
                sum((remaining_quantity(r) for r in self.close), Decimal("0"))
            ),
            "skipped": self.skipped,
        }


def plan_rebase(
    rows: list[dict[str, Any]],
    exchange_quantities: dict[tuple[str, str], Decimal],
    now: datetime,
    grace_seconds: int = DEFAULT_GRACE_SECONDS,
) -> list[KeyPlan]:
    """Per (symbol, side): which open rows are kept, which are closed and why. Pure: no I/O."""
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("status") not in OPEN_STATUSES or not row.get(
            "strategy_position_id"
        ):
            continue
        key = (str(row.get("symbol") or ""), str(row.get("side") or "").upper())
        by_key[key].append(row)

    plans: list[KeyPlan] = []
    for (symbol, side), key_rows in sorted(by_key.items()):
        if (symbol, "BOTH") in exchange_quantities and (
            symbol,
            side,
        ) not in exchange_quantities:
            plans.append(
                KeyPlan(symbol, side, None, skipped="one_way_mode_not_guessed")
            )
            continue
        quantity = exchange_quantities.get((symbol, side)) or Decimal("0")
        held = quantity > 0
        plan = KeyPlan(
            symbol,
            side,
            quantity,
            reason=REASON_EXCEEDS_EXCHANGE_QUANTITY
            if held
            else REASON_NO_EXCHANGE_POSITION,
        )
        newest_first = sorted(
            key_rows,
            key=lambda r: _entry_time(r) or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        covered = Decimal("0")
        for row in newest_first:
            entry = _entry_time(row)
            young = entry is not None and (now - entry).total_seconds() < grace_seconds
            remaining = remaining_quantity(row)
            if young:
                plan.keep.append({"row": row, "partial": False, "covered": remaining})
                covered += remaining
            elif held and covered < quantity:
                cover = min(remaining, quantity - covered)
                plan.keep.append(
                    {"row": row, "partial": cover < remaining, "covered": cover}
                )
                covered += remaining
            else:
                plan.close.append(row)
        plans.append(plan)
    return plans


def _closure_fields(
    row: dict[str, Any], reason: str, closure: dict[str, Any], at: datetime
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The row fields and the ``external_close`` record for ``closure``, as an organic external close writes them.

    #837 follow-up: an attributed (``exchange_fills``) closure goes through
    ``StrategyPositionManager._apply_external_closure``, the same code as the reconciler's evictions, so the
    row carries the closing fills' exit price and quantity, gross and net realized P&L and fees. Any other
    closure writes no P&L (null), never an invented number.
    """
    from tradeengine.strategy_position_manager import StrategyPositionManager

    closed = {**row, "exit_time": at, "close_reason": reason}
    StrategyPositionManager._apply_external_closure(closed, closure)
    record = dict((closed.get("strategy_metadata") or {}).get("external_close") or {})
    record["reason"] = reason
    if closure.get("reason") and closure.get("reason") != reason:
        record["closure_reason"] = closure["reason"]
    if closure.get("pnl_source") == PNL_SOURCE_EXCHANGE_FILLS:
        fields = {f: closed.get(f) for f in _ATTRIBUTED_FIELDS}
    else:
        fields = {
            "exit_time": at,
            "realized_pnl": None,
            "gross_realized_pnl": None,
            "realized_pnl_pct": None,
        }
    return fields, record


def build_closure_update(
    row: dict[str, Any],
    reason: str,
    closure: dict[str, Any],
    run_id: str,
    at: datetime,
) -> dict[str, Any]:
    """The data-manager update that closes ``row``, with everything needed to undo it."""
    metadata = dict(row.get("strategy_metadata") or {})
    metadata["reconcile_rebase"] = {
        "run_id": run_id,
        "at": at.isoformat(),
        "reason": reason,
        "prev_status": row.get("status"),
        "prev_remaining_qty": str(remaining_quantity(row)),
        "prev_fields": {f: _iso(row.get(f)) for f in RESTORABLE_FIELDS},
        "prev_external_close": metadata.get("external_close"),
    }
    fields, record = _closure_fields(row, reason, closure, at)
    metadata["external_close"] = record
    return {
        **fields,
        "status": "closed_externally",
        "close_reason": reason,
        "strategy_metadata": metadata,
    }


def build_pnl_repair_update(
    row: dict[str, Any], closure: dict[str, Any], run_id: str, at: datetime
) -> dict[str, Any] | None:
    """#837 follow-up: write the P&L an attributed closure of ``run_id`` left null; None when nothing to repair.

    Only a row this run closed, whose closure is attributed (``exchange_fills``) and whose realized P&L is
    null, is repaired, and only from a closure re-read now that is attributed too. The repair is audited in
    ``reconcile_rebase.pnl_repair`` with the values it replaced, so it can be undone from the row alone.
    """
    metadata = dict(row.get("strategy_metadata") or {})
    audit = dict(metadata.get("reconcile_rebase") or {})
    close = metadata.get("external_close") or {}
    if (
        audit.get("run_id") != run_id
        or row.get("status") != "closed_externally"
        or close.get("pnl_source") != PNL_SOURCE_EXCHANGE_FILLS
        or row.get("realized_pnl") is not None
        or closure.get("pnl_source") != PNL_SOURCE_EXCHANGE_FILLS
    ):
        return None
    reason = str(row.get("close_reason") or audit.get("reason"))
    fields, record = _closure_fields(row, reason, closure, at)
    audit["pnl_repair"] = {
        "at": at.isoformat(),
        "replaced": {f: _iso(row.get(f)) for f in _ATTRIBUTED_FIELDS},
        "replaced_external_close": close,
    }
    metadata["reconcile_rebase"] = audit
    metadata["external_close"] = record
    fields["exit_time"] = row.get("exit_time") or fields.get("exit_time")
    return {**fields, "strategy_metadata": metadata}


def build_rollback_update(row: dict[str, Any], run_id: str) -> dict[str, Any] | None:
    """Undo one run's closure from the row's own audit record; None when ``row`` was not closed by ``run_id``."""
    metadata = dict(row.get("strategy_metadata") or {})
    audit = metadata.get("reconcile_rebase") or {}
    if audit.get("run_id") != run_id or row.get("status") != "closed_externally":
        return None
    update: dict[str, Any] = dict(audit.get("prev_fields") or {})
    update["status"] = audit.get("prev_status") or "open"
    previous_close = audit.get("prev_external_close")
    metadata.pop("reconcile_rebase", None)
    if previous_close is None:
        metadata.pop("external_close", None)
    else:
        metadata["external_close"] = previous_close
    metadata["reconcile_rebase_rolled_back"] = {
        "run_id": run_id,
        "at": datetime.now(UTC).isoformat(),
    }
    update["strategy_metadata"] = metadata
    return update


def coverage_update(entry: dict[str, Any], run_id: str, at: datetime) -> dict[str, Any]:
    row = entry["row"]
    metadata = dict(row.get("strategy_metadata") or {})
    metadata["inferred_coverage"] = {
        "method": COVERAGE_METHOD,
        "covered_qty": str(entry["covered"]),
        "partial": entry["partial"],
        "run_id": run_id,
        "at": at.isoformat(),
    }
    return {"strategy_metadata": metadata}


async def run_rebase(
    *,
    rows: list[dict[str, Any]],
    exchange_quantities: dict[tuple[str, str], Decimal],
    update_row: Any,
    manager: Any,
    fills_fetcher: FillsFetcher | None,
    run_id: str,
    dry_run: bool = True,
    max_closures: int = 2000,
    grace_seconds: int = DEFAULT_GRACE_SECONDS,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Plan and (unless ``dry_run``) apply. ``update_row(strategy_position_id, update) -> bool``."""
    at = now or datetime.now(UTC)
    plans = plan_rebase(rows, exchange_quantities, at, grace_seconds)
    closures = sum(len(p.close) for p in plans)
    report: dict[str, Any] = {
        "run_id": run_id,
        "dry_run": dry_run,
        "at": at.isoformat(),
        "rows_read": len(rows),
        "keys": [p.summary() for p in plans],
        "rows_to_close": closures,
        "rows_kept": sum(len(p.keep) for p in plans),
        "max_closures": max_closures,
    }
    if dry_run:
        return report
    if closures > max_closures:
        report["refused"] = f"{closures} closures exceed max_closures={max_closures}"
        return report

    closed: dict[str, int] = defaultdict(int)
    failed: list[str] = []
    trades_cache: dict[str, Any] = {}
    for plan in plans:
        for row in sorted(plan.close, key=lambda r: _entry_time(r) or at):
            spid = str(row["strategy_position_id"])
            if plan.reason == REASON_NO_EXCHANGE_POSITION:
                closure = await fetch_closure(
                    fills_fetcher,
                    row,
                    plan.close + [k["row"] for k in plan.keep],
                    trades_cache,
                )
            else:
                closure = {
                    "pnl_source": PNL_SOURCE_UNATTRIBUTED,
                    "reason": REASON_EXCEEDS_EXCHANGE_QUANTITY,
                }
            update = build_closure_update(row, str(plan.reason), closure, run_id, at)
            if not await update_row(spid, update):
                failed.append(spid)
                continue
            manager.strategy_positions.pop(spid, None)
            strategy_position_external_close_total.labels(
                ledger="strategy_positions",
                pnl_source=str(closure.get("pnl_source")),
                reason=str(plan.reason),
            ).inc()
            closed[str(plan.reason)] += 1
        for entry in plan.keep:
            row = entry["row"]
            spid = str(row["strategy_position_id"])
            update = coverage_update(entry, run_id, at)
            if await update_row(spid, update):
                loaded = {**row, **update}
                manager.strategy_positions.setdefault(spid, loaded)
                manager.strategy_positions[spid]["strategy_metadata"] = update[
                    "strategy_metadata"
                ]
    report.update({"closed": dict(closed), "failed": failed})
    logger.warning(
        "STRATEGY_POSITION_REBASE run_id=%s closed=%s failed=%d kept=%d",
        run_id,
        dict(closed),
        len(failed),
        report["rows_kept"],
    )
    return report


async def run_rollback(
    *, rows: list[dict[str, Any]], update_row: Any, run_id: str, dry_run: bool = True
) -> dict[str, Any]:
    """Restore every row closed by ``run_id`` from its own audit record."""
    targets = [(r, build_rollback_update(r, run_id)) for r in rows]
    targets = [(r, u) for r, u in targets if u is not None]
    report: dict[str, Any] = {
        "run_id": run_id,
        "dry_run": dry_run,
        "rows": len(targets),
    }
    if dry_run:
        return report
    failed = [
        str(r["strategy_position_id"])
        for r, u in targets
        if not await update_row(str(r["strategy_position_id"]), u)
    ]
    report["failed"] = failed
    logger.warning(
        "STRATEGY_POSITION_REBASE_ROLLBACK run_id=%s restored=%d failed=%d",
        run_id,
        len(targets) - len(failed),
        len(failed),
    )
    return report


async def run_pnl_repair(
    *,
    rows: list[dict[str, Any]],
    update_row: Any,
    fills_fetcher: FillsFetcher | None,
    run_id: str,
    dry_run: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Re-read the closing fills of a run's attributed closures that kept a null P&L, and write it."""
    at = now or datetime.now(UTC)
    candidates = [
        r
        for r in rows
        if ((r.get("strategy_metadata") or {}).get("reconcile_rebase") or {}).get(
            "run_id"
        )
        == run_id
        and ((r.get("strategy_metadata") or {}).get("external_close") or {}).get(
            "pnl_source"
        )
        == PNL_SOURCE_EXCHANGE_FILLS
        and r.get("realized_pnl") is None
    ]
    report: dict[str, Any] = {
        "run_id": run_id,
        "dry_run": dry_run,
        "candidates": [str(r.get("strategy_position_id")) for r in candidates],
        "repaired": [],
        "not_attributable": [],
        "failed": [],
    }
    trades_cache: dict[str, Any] = {}
    for row in candidates:
        spid = str(row.get("strategy_position_id"))
        reopened = {**row, "status": "open", "exit_quantity": 0}
        closure = await fetch_closure(fills_fetcher, reopened, [reopened], trades_cache)
        update = build_pnl_repair_update(row, closure, run_id, at)
        if update is None:
            report["not_attributable"].append(
                {"strategy_position_id": spid, "closure": closure.get("reason")}
            )
            continue
        report.setdefault("planned", []).append(
            {
                "strategy_position_id": spid,
                "realized_pnl": str(update.get("realized_pnl")),
                "exit_price": update.get("exit_price"),
            }
        )
        if dry_run:
            continue
        if await update_row(spid, update):
            report["repaired"].append(spid)
        else:
            report["failed"].append(spid)
    return report
