#!/usr/bin/env python3
"""Reconcile open journal rows with the read-only Binance position snapshot.

The default is a dry-run.  ``--apply`` only closes rows whose symbol and side
are absent from the exchange snapshot, and requires the exact phantom count
from the reviewed dry-run via ``--confirm-count``.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from shared.constants import (
    BINANCE_API_KEY,
    BINANCE_API_SECRET,
    BINANCE_FUTURES_BASE_URL,
    BINANCE_TESTNET,
    UTC,
)
from shared.trading_store_client import TradingStoreClient

logger = logging.getLogger("reconcile_positions_journal")


class ReconciliationError(Exception):
    """A safe-to-report operator error that must not result in writes."""


def _normalise_side(row: dict[str, Any]) -> str:
    """Return LONG or SHORT, deriving one-way mode from the signed quantity."""
    side = str(row.get("positionSide", row.get("position_side", row.get("side", ""))))
    side = side.upper()
    if side in {"LONG", "SHORT"}:
        return side

    quantity = row.get(
        "positionAmt", row.get("quantity", row.get("position_amount", 0))
    )
    try:
        return "SHORT" if float(quantity) < 0 else "LONG"
    except (TypeError, ValueError) as exc:
        raise ReconciliationError(f"invalid position quantity for {row!r}") from exc


def _symbol_side(row: dict[str, Any]) -> tuple[str, str]:
    symbol = str(row.get("symbol", "")).upper()
    if not symbol:
        raise ReconciliationError(f"position row is missing symbol: {row!r}")
    return symbol, _normalise_side(row)


def _quantity(row: dict[str, Any]) -> float:
    value = row.get("positionAmt", row.get("quantity", row.get("position_amount", 0)))
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ReconciliationError(f"invalid position quantity for {row!r}") from exc


def _db_quantity(row: dict[str, Any]) -> float | None:
    for key in ("quantity", "positionAmt", "position_amount"):
        if row.get(key) is not None:
            try:
                return float(row[key])
            except (TypeError, ValueError):
                return None
    return None


def _is_nonzero(row: dict[str, Any]) -> bool:
    return abs(_quantity(row)) > 1e-12


async def fetch_exchange_positions(client: Any) -> list[dict[str, Any]]:
    """Call only Binance's read-only futures position information endpoint."""
    result = client.futures_position_information()
    if inspect.isawaitable(result):
        result = await result
    return list(result or [])


def build_plan(
    exchange_positions: list[dict[str, Any]],
    database_positions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Classify database rows and build the operator-facing report details."""
    exchange_by_key: dict[tuple[str, str], float] = {}
    for row in exchange_positions:
        if not _is_nonzero(row):
            continue
        key = _symbol_side(row)
        exchange_by_key[key] = exchange_by_key.get(key, 0.0) + _quantity(row)

    phantom: list[dict[str, Any]] = []
    live: list[dict[str, Any]] = []
    grouped: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"phantom": 0, "live-candidate": 0}
    )
    live_groups: dict[tuple[str, str], dict[str, Any]] = {}

    for row in database_positions:
        key = _symbol_side(row)
        symbol, side = key
        position_id = row.get("position_id")
        if position_id is None or str(position_id) == "":
            raise ReconciliationError(f"open position is missing position_id: {row!r}")

        if key not in exchange_by_key:
            phantom.append(row)
            grouped[key]["phantom"] += 1
            continue

        live.append(row)
        grouped[key]["live-candidate"] += 1
        group = live_groups.setdefault(
            key,
            {
                "symbol": symbol,
                "position_side": side,
                "exchange_quantity": exchange_by_key[key],
                "exchange_quantity_abs": abs(exchange_by_key[key]),
                "database_quantity": 0.0,
                "database_rows": [],
            },
        )
        quantity = _db_quantity(row)
        if quantity is not None:
            group["database_quantity"] += quantity
        group["database_rows"].append(
            {"position_id": str(position_id), "quantity": quantity}
        )

    by_symbol_side = {
        f"{symbol}:{side}": values for (symbol, side), values in sorted(grouped.items())
    }
    report_details = {
        "counts": {"phantom": len(phantom), "live-candidate": len(live)},
        "by_symbol_side": by_symbol_side,
        "live_candidates": [live_groups[key] for key in sorted(live_groups)],
        "phantom_rows": [
            {
                "position_id": str(row["position_id"]),
                "symbol": _symbol_side(row)[0],
                "position_side": _symbol_side(row)[1],
            }
            for row in phantom
        ],
    }
    return phantom, live, report_details


async def reconcile(
    exchange_client: Any,
    position_client: Any,
    *,
    apply: bool,
    confirm_count: int | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Read both systems, optionally apply the reviewed phantom plan."""
    exchange_positions = await fetch_exchange_positions(exchange_client)
    database_positions = await position_client.get_open_positions()
    phantom, _, report = build_plan(exchange_positions, database_positions)
    phantom_count = len(phantom)

    if apply:
        if confirm_count is None:
            raise ReconciliationError("--apply requires --confirm-count N")
        if confirm_count != phantom_count:
            raise ReconciliationError(
                f"confirmation count {confirm_count} does not match phantom count "
                f"{phantom_count}; no updates were issued"
            )

        exit_time = (now or datetime.now(UTC)).astimezone(UTC).isoformat()
        for row in phantom:
            result = await position_client.update_position(
                str(row["position_id"]),
                {
                    "status": "closed",
                    "close_reason": "reconciled_no_exchange_position",
                    "exit_time": exit_time,
                    "pnl": None,
                },
            )
            if hasattr(result, "ok") and not result.ok:
                raise ReconciliationError(
                    f"update failed for position_id={row['position_id']}: {result.error}"
                )
        report["applied"] = phantom_count
    else:
        report["applied"] = 0

    report["phantom"] = phantom_count
    report["live-candidate"] = report["counts"]["live-candidate"]
    report["dry_run"] = not apply
    return report


def _build_exchange_client() -> Any:
    """Build a client without calling any endpoint besides position information."""
    from binance import Client

    client = Client(
        api_key=BINANCE_API_KEY,
        api_secret=BINANCE_API_SECRET,
        testnet=BINANCE_TESTNET,
    )
    if BINANCE_FUTURES_BASE_URL:
        futures_url = BINANCE_FUTURES_BASE_URL.rstrip("/")
        client.FUTURES_URL = (
            futures_url if futures_url.endswith("/fapi") else f"{futures_url}/fapi"
        )
    return client


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Inspect and report without issuing database updates (the default).",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Close exactly the phantom count confirmed by --confirm-count.",
    )
    parser.add_argument("--confirm-count", type=int)
    parser.add_argument("--report", type=Path, help="Write the JSON report to PATH.")
    return parser


def _write_report(report: dict[str, Any], path: Path | None) -> None:
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if path is None:
        print(rendered)
    else:
        path.write_text(rendered + "\n", encoding="utf-8")
        logger.info("Wrote reconciliation report to %s", path)


async def _main_async(args: argparse.Namespace) -> int:
    if args.confirm_count is not None and args.confirm_count < 0:
        raise ReconciliationError("--confirm-count must be non-negative")
    if not args.apply and args.confirm_count is not None:
        raise ReconciliationError("--confirm-count is only valid with --apply")

    exchange_client = _build_exchange_client()
    position_client = TradingStoreClient()
    await position_client.connect()
    try:
        report = await reconcile(
            exchange_client,
            position_client,
            apply=args.apply,
            confirm_count=args.confirm_count,
        )
    finally:
        await position_client.disconnect()
    _write_report(report, args.report)
    return 0


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _build_parser().parse_args()
    try:
        return asyncio.run(_main_async(args))
    except ReconciliationError as exc:
        logger.error("%s", exc)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
