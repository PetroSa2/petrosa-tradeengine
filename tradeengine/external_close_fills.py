"""Attribute exchange closing fills to a strategy position that closed externally (#831).

When the strategy-position reconciler finds a strategy position with no
matching exchange position, the position was closed outside tradeengine's own
close path (a protective leg the stream missed, a residual close, a manual
close, a liquidation). This module turns the exchange's own trade history
(``GET /fapi/v1/userTrades``) into the closure record: exit price, realized P&L
and commission, but only when the closing fills can be attributed to that one
position without guessing.

The result always carries ``pnl_source``:

- ``exchange_fills``: the fills closing this position were identified exactly;
  exit price, realized P&L and commission are their quantity-weighted price and
  sums.
- ``unattributed``: fills exist but cannot be tied to this position alone
  (another strategy position shares the symbol and side, or the closing
  quantity does not equal the remaining quantity). No P&L is recorded; the
  candidate fills are kept for audit.
- ``missing``: the trade history could not be read. No P&L is recorded.

Pure functions only: no I/O, so the attribution is testable on recorded fills.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from shared.constants import UTC

PNL_SOURCE_EXCHANGE_FILLS = "exchange_fills"
PNL_SOURCE_UNATTRIBUTED = "unattributed"
PNL_SOURCE_MISSING = "missing"

_CLOSING_SIDE = {"LONG": "SELL", "SHORT": "BUY"}


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _to_ms(value: Any) -> int | None:
    """Epoch milliseconds for a datetime, ISO string or epoch number."""
    if value is None:
        return None
    if isinstance(value, int | float):
        return int(value)
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return int(value.timestamp() * 1000)
    return None


def position_open_ms(position: dict[str, Any]) -> int | None:
    """The position's entry time in epoch milliseconds, or None if unknown."""
    return _to_ms(position.get("entry_time"))


def remaining_quantity(position: dict[str, Any]) -> Decimal:
    entry = _dec(position.get("entry_quantity")) or Decimal("0")
    exited = _dec(position.get("exit_quantity")) or Decimal("0")
    return max(entry - exited, Decimal("0"))


def _fill_summary(trade: dict[str, Any]) -> dict[str, Any]:
    return {
        "trade_id": trade.get("id"),
        "order_id": trade.get("orderId"),
        "side": trade.get("side"),
        "position_side": trade.get("positionSide"),
        "price": trade.get("price"),
        "qty": trade.get("qty"),
        "realized_pnl": trade.get("realizedPnl"),
        "commission": trade.get("commission"),
        "commission_asset": trade.get("commissionAsset"),
        "time": trade.get("time"),
    }


def closing_fill_candidates(
    position: dict[str, Any], trades: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Fills after the position's entry that reduce a position on its side.

    A LONG is reduced by SELL fills and a SHORT by BUY fills. In hedge mode the
    fill's ``positionSide`` must equal the position's side; one-way fills carry
    ``BOTH``. The position's own entry order is excluded.
    """
    side = str(position.get("side", "")).upper()
    closing_side = _CLOSING_SIDE.get(side)
    if closing_side is None:
        return []
    symbol = position.get("symbol")
    open_ms = position_open_ms(position)
    entry_order_id = str(position.get("entry_order_id") or "")
    candidates = []
    for trade in trades:
        if not isinstance(trade, dict):
            continue
        if symbol and trade.get("symbol") not in (None, symbol):
            continue
        if str(trade.get("side", "")).upper() != closing_side:
            continue
        position_side = str(trade.get("positionSide") or "BOTH").upper()
        if position_side not in ("BOTH", side):
            continue
        trade_ms = _to_ms(trade.get("time"))
        if open_ms is not None and (trade_ms is None or trade_ms < open_ms):
            continue
        if entry_order_id and str(trade.get("orderId")) == entry_order_id:
            continue
        if _dec(trade.get("qty")) is None or _dec(trade.get("price")) is None:
            continue
        candidates.append(trade)
    candidates.sort(key=lambda t: (_to_ms(t.get("time")) or 0, t.get("id") or 0))
    return candidates


def missing_closure(reason: str) -> dict[str, Any]:
    """Closure record for a position whose trade history could not be read."""
    return {"pnl_source": PNL_SOURCE_MISSING, "reason": reason}


def attribute_closing_fills(
    position: dict[str, Any],
    trades: list[dict[str, Any]],
    other_positions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the closure record for ``position`` from the symbol's trade history.

    ``other_positions`` are the other strategy positions the manager holds
    (open, or evicted in the same reconcile pass). Any of them on the same
    symbol and side shares the closing fills, so attribution is refused.
    """
    candidates = closing_fill_candidates(position, trades)
    summaries = [_fill_summary(t) for t in candidates]
    spid = position.get("strategy_position_id")
    symbol = position.get("symbol")
    side = str(position.get("side", "")).upper()

    sharing = sorted(
        str(other.get("strategy_position_id"))
        for other in other_positions
        if other.get("strategy_position_id") != spid
        and other.get("symbol") == symbol
        and str(other.get("side", "")).upper() == side
    )
    if sharing:
        return {
            "pnl_source": PNL_SOURCE_UNATTRIBUTED,
            "reason": "symbol_side_shared",
            "sharing_positions": sharing,
            "candidate_fills": summaries,
        }
    if not candidates:
        return {
            "pnl_source": PNL_SOURCE_UNATTRIBUTED,
            "reason": "no_closing_fills",
            "candidate_fills": [],
        }

    remaining = remaining_quantity(position)
    total_qty = sum((_dec(t["qty"]) or Decimal("0")) for t in candidates)
    if total_qty != remaining:
        return {
            "pnl_source": PNL_SOURCE_UNATTRIBUTED,
            "reason": "quantity_mismatch",
            "remaining_quantity": str(remaining),
            "closing_quantity": str(total_qty),
            "candidate_fills": summaries,
        }

    notional = sum(
        (_dec(t["price"]) or Decimal("0")) * (_dec(t["qty"]) or Decimal("0"))
        for t in candidates
    )
    realized = sum((_dec(t.get("realizedPnl")) or Decimal("0")) for t in candidates)
    commission_by_asset: dict[str, Decimal] = {}
    for trade in candidates:
        amount = _dec(trade.get("commission"))
        if amount is None:
            continue
        asset = str(trade.get("commissionAsset") or "UNKNOWN")
        commission_by_asset[asset] = (
            commission_by_asset.get(asset, Decimal("0")) + amount
        )
    exit_ms = max(_to_ms(t.get("time")) or 0 for t in candidates)
    order_ids: list[str] = []
    for trade in candidates:
        order_id = str(trade.get("orderId"))
        if order_id not in order_ids:
            order_ids.append(order_id)

    return {
        "pnl_source": PNL_SOURCE_EXCHANGE_FILLS,
        "exit_price": notional / total_qty,
        "exit_quantity": total_qty,
        "realized_pnl": realized,
        "commission_by_asset": commission_by_asset,
        "exit_time": datetime.fromtimestamp(exit_ms / 1000, tz=UTC),
        "exit_order_ids": order_ids,
        "fills": summaries,
    }
