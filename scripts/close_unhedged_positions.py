#!/usr/bin/env python3
"""One-shot operator tool: close Binance positions that lack reduceOnly SL+TP.

AC6 of #424 (OCO orphan-legs incident, 2026-05-30). Reads the live Binance
positionRisk + open-algo-order state, surfaces any position that lacks BOTH
a reduceOnly STOP-shaped order AND a reduceOnly TAKE_PROFIT-shaped order on
the matching positionSide, and (with ``--commit``) issues MARKET reduceOnly
orders to close them.

Dry-run is the default — the script is safe to run repeatedly to inspect
state. ``--commit`` is what makes it actually close anything.

Authentication: reads ``BINANCE_API_KEY`` and ``BINANCE_API_SECRET`` from
the environment. Add ``BINANCE_TESTNET=1`` to point at testnet.

Output: one JSON line per unhedged position to stdout. Exit code 0 if any
were listed/closed, 1 if none — safe to chain into operator tooling.

Usage:
    # Dry-run (default — always safe)
    python scripts/close_unhedged_positions.py

    # Live close all unhedged
    python scripts/close_unhedged_positions.py --commit

    # Only ETHUSDT
    python scripts/close_unhedged_positions.py --commit --symbol ETHUSDT

    # Only positions older than 1 hour
    python scripts/close_unhedged_positions.py --commit --min-age-seconds 3600
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from typing import Any

# Logging goes to stderr so stdout is reserved for the JSON-line operator output.
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    stream=sys.stderr,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("close_unhedged_positions")


def _is_reduce_only(order: dict[str, Any]) -> bool:
    return bool(order.get("reduceOnly")) or bool(order.get("closePosition"))


def _normalise_side(pos: dict[str, Any]) -> str:
    side = str(pos.get("positionSide", "BOTH")).upper()
    if side in ("LONG", "SHORT"):
        return side
    return "LONG" if float(pos.get("positionAmt", 0)) >= 0 else "SHORT"


def _position_is_unhedged(
    pos: dict[str, Any], symbol_orders: list[dict[str, Any]]
) -> tuple[bool, bool, bool]:
    """Return (unhedged, sl_present, tp_present) for one positionRisk row."""
    side = _normalise_side(pos)
    sl_present = False
    tp_present = False
    for o in symbol_orders:
        o_side = str(o.get("positionSide", "BOTH")).upper()
        if o_side not in ("BOTH", side):
            continue
        if not _is_reduce_only(o):
            continue
        o_type = str(o.get("type") or o.get("origType") or "").upper()
        if "STOP" in o_type:
            sl_present = True
        elif "TAKE_PROFIT" in o_type:
            tp_present = True
    unhedged = not (sl_present and tp_present)
    return unhedged, sl_present, tp_present


async def _fetch_state(
    client: Any,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Return (positions, open_algo_orders_by_symbol)."""
    positions_raw = await client.get_position_info()
    nonzero = [p for p in positions_raw if abs(float(p.get("positionAmt", 0))) > 1e-9]

    orders_by_symbol: dict[str, list[dict[str, Any]]] = {}
    for symbol in {p["symbol"] for p in nonzero}:
        try:
            orders = await client.get_open_algo_orders(symbol=symbol)
        except Exception:
            logger.exception(
                "get_open_algo_orders(%s) failed — treating as no-orders (conservative)",
                symbol,
            )
            orders = []
        orders_by_symbol[symbol] = orders or []

    return nonzero, orders_by_symbol


async def _close_one(client: Any, pos: dict[str, Any]) -> dict[str, Any]:
    """Issue a MARKET reduceOnly close for one position. Returns the
    Binance API result (or a synthetic error dict if it raised)."""
    symbol = pos["symbol"]
    side = _normalise_side(pos)
    qty = abs(float(pos["positionAmt"]))
    # Closing direction is opposite of position side.
    order_side = "SELL" if side == "LONG" else "BUY"
    params = {
        "symbol": symbol,
        "side": order_side,
        "type": "MARKET",
        "quantity": qty,
        "reduceOnly": True,
        # If hedge mode, supply positionSide; one-way mode (BOTH) ignores it.
        "positionSide": pos.get("positionSide", "BOTH"),
    }
    try:
        result = await client.create_order(**params)
    except Exception as exc:
        logger.exception("close failed for %s %s qty=%s", symbol, side, qty)
        return {"error": str(exc), "params": params}
    return result


def _filter_age(
    positions: list[dict[str, Any]], min_age_seconds: int
) -> list[dict[str, Any]]:
    """Drop positions younger than min_age_seconds. ``updateTime`` is in
    milliseconds; rows missing it are kept (better to consider them
    eligible than skip silently)."""
    if min_age_seconds <= 0:
        return positions
    cutoff_ms = (time.time() - min_age_seconds) * 1000.0
    keep = []
    for p in positions:
        ts = p.get("updateTime")
        if ts is None:
            keep.append(p)
        elif float(ts) <= cutoff_ms:
            keep.append(p)
    return keep


async def main_async(args: argparse.Namespace) -> int:
    # Local import keeps the script runnable without spinning up the full
    # tradeengine wiring (e.g. NATS, MongoDB) — only the exchange wrapper
    # is needed here.
    from tradeengine.exchange.binance import BinanceFuturesExchange

    client = BinanceFuturesExchange()
    await client.initialize()

    positions, orders_by_symbol = await _fetch_state(client)

    if args.symbol:
        positions = [p for p in positions if p["symbol"] == args.symbol]

    positions = _filter_age(positions, args.min_age_seconds)

    closed_or_listed = 0
    for pos in positions:
        symbol = pos["symbol"]
        unhedged, sl_present, tp_present = _position_is_unhedged(
            pos, orders_by_symbol.get(symbol, [])
        )
        if not unhedged:
            continue

        record: dict[str, Any] = {
            "symbol": symbol,
            "positionSide": _normalise_side(pos),
            "positionAmt": float(pos["positionAmt"]),
            "sl_present": sl_present,
            "tp_present": tp_present,
            "would_close": True,
        }

        if args.commit:
            result = await _close_one(client, pos)
            record["close_result"] = result
            record["would_close"] = False
            record["closed"] = "error" not in result
        else:
            record["dry_run"] = True

        print(json.dumps(record), flush=True)
        closed_or_listed += 1

    return 0 if closed_or_listed > 0 else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "List or close unhedged Binance Futures positions (positions with "
            "no matching reduceOnly SL+TP). AC6 of #424."
        )
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help=(
            "Issue MARKET reduceOnly close orders for each unhedged position. "
            "Default is dry-run."
        ),
    )
    parser.add_argument(
        "--symbol",
        default=None,
        help="Limit to a single symbol (e.g. ETHUSDT).",
    )
    parser.add_argument(
        "--min-age-seconds",
        type=int,
        default=0,
        help=(
            "Only consider positions whose updateTime is older than this many "
            "seconds. 0 (default) means consider all."
        ),
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    if args.commit and not (
        os.environ.get("BINANCE_API_KEY") and os.environ.get("BINANCE_API_SECRET")
    ):
        print(
            "BINANCE_API_KEY/BINANCE_API_SECRET must be set when --commit is passed",
            file=sys.stderr,
        )
        return 2
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
