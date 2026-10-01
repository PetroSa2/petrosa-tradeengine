#!/usr/bin/env python3
"""Read-only ledger evidence collection.

The adapters accepted by this module expose GET-like methods only.  This is
deliberate: the tool is suitable for production evidence collection but never
for changing ledger state or placing an order.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


def _number(value: Any) -> float:
    return float(value or 0)


def _time(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value / 1000, UTC)
    text = str(value).replace("Z", "+00:00")
    return datetime.fromisoformat(text).astimezone(UTC)


def entry_legs(
    trades: Iterable[dict[str, Any]], exit_qty: float, *, lookback_days: int
) -> tuple[list[dict[str, Any]], bool]:
    """Walk newest-to-oldest trades until the requested quantity is covered."""
    ordered = sorted(trades, key=lambda row: _time(row.get("time", 0)), reverse=True)
    if ordered:
        cutoff = _time(ordered[0].get("time", 0)) - timedelta(days=lookback_days)
        ordered = [row for row in ordered if _time(row.get("time", 0)) >= cutoff]
    remaining = abs(exit_qty)
    legs: list[dict[str, Any]] = []
    for trade in ordered:
        quantity = min(remaining, abs(_number(trade.get("qty", trade.get("quantity")))))
        if quantity <= 0:
            continue
        legs.append(
            {
                "time": _time(trade.get("time", 0)).isoformat(),
                "side": trade.get("side"),
                "positionSide": trade.get("positionSide", trade.get("position_side")),
                "qty": quantity,
                "price": _number(trade.get("price")),
                "orderId": trade.get("orderId", trade.get("order_id")),
            }
        )
        remaining -= quantity
        if remaining <= 1e-12:
            break
    return legs, remaining > 1e-12


def classify_fill(
    legs: list[dict[str, Any]], candles: dict[str, dict[str, float]]
) -> str:
    """Classify legs against their minute candle ranges."""
    if not legs or any(
        leg_key not in candles for leg_key in [str(leg["time"])[:16] for leg in legs]
    ):
        return "INCONCLUSIVE"
    for leg in legs:
        candle = candles[str(leg["time"])[:16]]
        if not candle["low"] <= leg["price"] <= candle["high"]:
            return "TESTNET_ARTIFACT"
    return "GENUINE"


def fill_report(
    *,
    trades: list[dict[str, Any]],
    income: dict[str, Any],
    candles: dict[str, dict[str, float]],
    exit_qty: float,
    exit_price: float,
    lookback_days: int,
) -> dict[str, Any]:
    legs, bounded = entry_legs(trades, exit_qty, lookback_days=lookback_days)
    leg_sum = sum((exit_price - leg["price"]) * leg["qty"] for leg in legs)
    realized = _number(income.get("income", income.get("realizedPnl")))
    return {
        "legs": legs,
        "lookback_bound_reached": bounded,
        "income_trade_id": income.get("tradeId"),
        "realized_pnl": round(realized, 2),
        "leg_sum": round(leg_sum, 2),
        "residual": round(realized - leg_sum, 2),
        "classification": classify_fill(legs, candles),
    }


def group_persist_events(
    events: list[dict[str, Any]], positions: list[dict[str, Any]]
) -> dict[str, Any]:
    position_keys = {
        str(row.get("position_id", row.get("id", ""))) for row in positions
    }
    by_day: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"events": 0, "without_position": 0, "absolute": 0, "incremental": 0}
    )
    for event in events:
        day = str(event.get("created_at", event.get("time", "")))[:10]
        bucket = by_day[day]
        bucket["events"] += 1
        if str(event.get("position_id", "")) not in position_keys:
            bucket["without_position"] += 1
        mode = str(event.get("write_mode", event.get("persist_mode", ""))).lower()
        if mode == "absolute":
            bucket["absolute"] += 1
        elif mode == "incremental":
            bucket["incremental"] += 1
    return {"days": dict(sorted(by_day.items()))}


def count_rejections(
    text: str, events: Iterable[dict[str, Any]] = ()
) -> dict[str, int]:
    counts = Counter({"-4164": 0, "-1013": 0})
    for code in counts:
        counts[code] += len(re.findall(rf"(?<!\d){re.escape(code)}(?!\d)", text))
    for event in events:
        rendered = json.dumps(event)
        for code in counts:
            counts[code] += len(
                re.findall(rf"(?<!\d){re.escape(code)}(?!\d)", rendered)
            )
    return dict(counts)


def commission_report(
    income: Iterable[dict[str, Any]], fills: Iterable[dict[str, Any]]
) -> dict[str, Any]:
    totals: dict[str, dict[str, float]] = defaultdict(
        lambda: {"income": 0.0, "fills": 0.0}
    )
    for row in income:
        if str(row.get("incomeType", row.get("type", ""))).upper() == "COMMISSION":
            totals[str(row.get("symbol", ""))]["income"] += abs(
                _number(row.get("income"))
            )
    for row in fills:
        totals[str(row.get("symbol", ""))]["fills"] += abs(
            _number(row.get("commission", row.get("fee")))
        )
    return {
        symbol: {**values, "difference": round(values["income"] - values["fills"], 8)}
        for symbol, values in sorted(totals.items())
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    fill = sub.add_parser("fill")
    fill.add_argument("--order-id", required=True)
    fill.add_argument("--symbol", required=True)
    fill.add_argument("--max-lookback-days", type=int, default=7)
    trace = sub.add_parser("persist-trace")
    trace.add_argument("--since", required=True)
    trace.add_argument("--events-file", type=Path)
    reject = sub.add_parser("rejections")
    reject.add_argument("--log-file", type=Path, required=True)
    commission = sub.add_parser("commission")
    commission.add_argument("--from", dest="start", required=True)
    commission.add_argument("--to", dest="end", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "rejections":
        print(
            json.dumps(
                count_rejections(args.log_file.read_text(encoding="utf-8")),
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "persist-trace" and args.events_file:
        payload = json.loads(args.events_file.read_text(encoding="utf-8"))
        print(
            json.dumps(
                group_persist_events(
                    payload.get("execution_events", []), payload.get("positions", [])
                ),
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    raise SystemExit(
        "live collection requires an explicitly supplied read-only adapter"
    )


if __name__ == "__main__":
    raise SystemExit(main())
