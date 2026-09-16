#!/usr/bin/env python3
"""Operator tool: flatten Binance Futures positions with an inverted sign.

#586 — a "malformed" position (Binance ``positionSide=LONG`` with negative
``positionAmt``, or ``positionSide=SHORT`` with positive ``positionAmt``) is
un-armable: any ``reduceOnly`` protective SL/TP derived from the declared
``positionSide`` would be direction-invalid. ``NakedPositionRemediator`` in
``arm_only`` mode (#547) detects this and alerts once, but by design never
flattens it — the position stays stuck pending operator action or a
promotion to ``arm_or_flatten`` (see
``docs/runbooks/naked-position-remediation.md``).

This script is that operator action, scoped narrowly to the malformed-sign
case (as opposed to ``close_unhedged_positions.py``'s broader missing-SL/TP
scope):

1. Reads the live Binance positionRisk snapshot (read-only by default).
2. Flags any row where ``positionSide`` and the sign of ``positionAmt``
   disagree.
3. With ``--apply``, issues exactly ONE MARKET close per flagged row, sized
   to the live ``abs(positionAmt)`` re-read immediately before the order
   (never a cached/stale quantity — the #586 root cause was exactly a close
   order overshooting a live position because Binance drops the wire
   ``reduceOnly`` flag whenever ``positionSide`` is set, so nothing on the
   exchange side caps an oversized closing order).

Safe by construction:
- Default is ``--dry-run`` (the explicit flag; omitting both is equivalent).
  No `--dry-run` is required to be typed — the script refuses to write
  unless ``--apply`` is passed explicitly.
- Requires an interactive `--yes-i-am-sure` confirmation OR a non-interactive
  environment variable acknowledgement (``TE_REMEDIATE_MALFORMED_ACK=1``) in
  addition to ``--apply``, so a bare `--apply` from a copy-pasted command
  never fires unattended.
- Re-derives the closing quantity from a fresh positionRisk read at the
  moment of the close (not the earlier scan), so a partial exchange-side
  fill/cancel between scan and close cannot itself cause an overshoot.
- One close order per flagged (symbol, positionSide); never loops/retries
  automatically — a failure is reported and left for the operator to
  re-invoke.

Authentication: reads ``BINANCE_API_KEY`` / ``BINANCE_API_SECRET`` from the
environment (testnet by default via ``BINANCE_TESTNET``; mirrors the rest of
this scripts/ directory — this tool never touches mainnet unless the
environment is explicitly pointed at it, which is an operator decision
outside this script's scope).

Usage:
    # Read-only scan (default, always safe)
    python scripts/remediate-malformed-position.py

    # Same, explicit
    python scripts/remediate-malformed-position.py --dry-run

    # Flatten all malformed positions found (requires double opt-in)
    python scripts/remediate-malformed-position.py --apply --yes-i-am-sure

    # Scope to one symbol
    python scripts/remediate-malformed-position.py --apply --yes-i-am-sure \\
        --symbol LTCUSDT
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Any

# Logging goes to stderr so stdout is reserved for the JSON-line operator output.
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    stream=sys.stderr,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("remediate_malformed_position")

_FLOAT_TOLERANCE = 1e-9


def is_malformed(pos: dict[str, Any]) -> bool:
    """A hedge-mode LONG row must carry positionAmt >= 0; SHORT <= 0.

    Mirrors ``tradeengine.position_reconciler``'s ``_is_malformed_sign``
    contract (#547) so this script and the production reconciler agree on
    what counts as malformed.
    """
    side = str(pos.get("positionSide", "BOTH")).upper()
    if side not in ("LONG", "SHORT"):
        return False  # one-way mode (BOTH) has no declared side to conflict with
    try:
        amt = float(pos.get("positionAmt", 0) or 0.0)
    except (TypeError, ValueError):
        return False
    if abs(amt) < _FLOAT_TOLERANCE:
        return False
    if side == "LONG" and amt < 0:
        return True
    if side == "SHORT" and amt > 0:
        return True
    return False


async def _fetch_malformed(client: Any, symbol: str | None) -> list[dict[str, Any]]:
    raw = await client.get_position_info()
    rows = [p for p in (raw or []) if is_malformed(p)]
    if symbol:
        rows = [p for p in rows if p.get("symbol") == symbol]
    return rows


async def _reread_live_qty(
    client: Any, symbol: str, position_side: str
) -> float | None:
    """Fresh positionRisk read immediately before closing.

    Returns ``None`` if the position is no longer present/malformed (already
    resolved — e.g. an operator or the exchange cleared it between the scan
    and this call), in which case the caller MUST skip rather than close.
    """
    raw = await client.get_position_info()
    for p in raw or []:
        if p.get("symbol") != symbol:
            continue
        if str(p.get("positionSide", "BOTH")).upper() != position_side:
            continue
        if not is_malformed(p):
            return None
        return abs(float(p.get("positionAmt", 0) or 0.0))
    return None


async def _close_one(
    client: Any, symbol: str, position_side: str, qty: float
) -> dict[str, Any]:
    """Issue exactly one MARKET close for the malformed (symbol, positionSide).

    Closing direction is opposite the *declared* positionSide (LONG->SELL,
    SHORT->BUY) regardless of the sign inversion — this is what actually
    flattens the malformed row back toward zero.
    """
    order_side = "SELL" if position_side == "LONG" else "BUY"
    params = {
        "symbol": symbol,
        "side": order_side,
        "type": "MARKET",
        "quantity": qty,
        "positionSide": position_side,
        # Deliberately NOT sending reduceOnly: Binance rejects reduceOnly
        # when positionSide is set in hedge mode (#586 investigation). The
        # #586 dispatcher fix's protection (live-qty clamp) is what this
        # script replicates manually via _reread_live_qty just above.
    }
    try:
        result = await client.create_order(**params)
    except Exception as exc:
        logger.exception(
            "close failed for malformed %s/%s qty=%s", symbol, position_side, qty
        )
        return {"error": str(exc), "params": params}
    return result


async def main_async(args: argparse.Namespace) -> int:
    # Local import keeps the script runnable without spinning up the full
    # tradeengine wiring (e.g. NATS, MongoDB) — only the exchange wrapper is
    # needed here (same pattern as close_unhedged_positions.py).
    from tradeengine.exchange.binance import BinanceFuturesExchange

    client = BinanceFuturesExchange()
    await client.initialize()

    malformed = await _fetch_malformed(client, args.symbol)

    if not malformed:
        print(
            json.dumps({"malformed_positions_found": 0}),
            flush=True,
        )
        return 1

    exit_code = 0
    for pos in malformed:
        symbol = pos["symbol"]
        position_side = str(pos.get("positionSide", "BOTH")).upper()
        raw_amt = float(pos.get("positionAmt", 0) or 0.0)

        record: dict[str, Any] = {
            "symbol": symbol,
            "positionSide": position_side,
            "raw_positionAmt": raw_amt,
            "malformed": True,
        }

        if not args.apply:
            record["dry_run"] = True
            record["would_flatten"] = True
            print(json.dumps(record), flush=True)
            continue

        # Fresh re-read immediately before the close — never trust the
        # earlier scan's quantity for the actual order (#586).
        live_qty = await _reread_live_qty(client, symbol, position_side)
        if live_qty is None:
            record["skipped"] = True
            record["reason"] = "already_resolved_since_scan"
            print(json.dumps(record), flush=True)
            continue

        result = await _close_one(client, symbol, position_side, live_qty)
        record["close_result"] = result
        record["closed_qty"] = live_qty
        record["closed"] = "error" not in result
        if "error" in result:
            exit_code = 1
        print(json.dumps(record), flush=True)

    return exit_code


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Scan for (and, with --apply, flatten) Binance Futures positions "
            "with an inverted sign vs their declared positionSide (#586 / "
            "#547 malformed-position terminal state)."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Explicit no-op flag; this is the default behavior regardless.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Issue a MARKET close for each malformed position found. Default "
            "is dry-run (scan/report only). Requires --yes-i-am-sure or "
            "TE_REMEDIATE_MALFORMED_ACK=1 as a second, explicit opt-in."
        ),
    )
    parser.add_argument(
        "--yes-i-am-sure",
        action="store_true",
        help="Second explicit opt-in required alongside --apply.",
    )
    parser.add_argument(
        "--symbol",
        default=None,
        help="Limit to a single symbol (e.g. LTCUSDT).",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()

    if args.apply:
        if not (
            os.environ.get("BINANCE_API_KEY") and os.environ.get("BINANCE_API_SECRET")
        ):
            print(
                "BINANCE_API_KEY/BINANCE_API_SECRET must be set when --apply is passed",
                file=sys.stderr,
            )
            return 2
        if not (
            args.yes_i_am_sure or os.environ.get("TE_REMEDIATE_MALFORMED_ACK") == "1"
        ):
            print(
                "--apply requires a second explicit opt-in: pass "
                "--yes-i-am-sure or set TE_REMEDIATE_MALFORMED_ACK=1. "
                "Refusing to place a live exchange order without it.",
                file=sys.stderr,
            )
            return 2

    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
