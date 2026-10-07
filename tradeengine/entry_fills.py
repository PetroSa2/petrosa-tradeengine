"""The fills that open a position, aggregated per entry order.

An entry position must carry the economics of its fills, not of the signal: the volume-weighted fill price, the
summed commission and the trade ids. A market order can fill in several trades, and the fills can arrive before or
after the position row is written, so both paths (the user-data fill handler and the position record) read the
same per-order aggregate. Recording is idempotent per ``(order, trade)``.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any


class EntryFillAggregator:
    """VWAP price, summed commission and trade ids of each entry order's fills."""

    def __init__(self, max_orders: int = 500) -> None:
        self._max_orders = max_orders
        self._orders: OrderedDict[str, dict[str, dict[str, Any]]] = OrderedDict()

    def record(
        self,
        order_id: Any,
        trade_id: Any,
        price: Any,
        quantity: Any,
        fee: Any = None,
        fee_asset: Any = None,
    ) -> dict[str, Any] | None:
        """Add one trade (a repeat of the same trade changes nothing) and return the order's aggregate."""
        try:
            price_f = float(price)
            quantity_f = float(quantity)
        except (TypeError, ValueError):
            return self.aggregate(order_id)
        if price_f <= 0 or quantity_f <= 0:
            return self.aggregate(order_id)
        key = str(order_id)
        trades = self._orders.setdefault(key, {})
        self._orders.move_to_end(key)
        fee_f = None
        if fee is not None:
            try:
                fee_f = float(fee)
            except (TypeError, ValueError):
                fee_f = None
        # Without a trade id the fill itself identifies the trade.
        trade_key = str(trade_id) if trade_id else f"{price_f}:{quantity_f}:{fee_f}"
        trades[trade_key] = {
            "trade_id": str(trade_id) if trade_id else None,
            "price": price_f,
            "quantity": quantity_f,
            "fee": fee_f,
            "fee_asset": str(fee_asset) if fee_asset else None,
        }
        while len(self._orders) > self._max_orders:
            self._orders.popitem(last=False)
        return self.aggregate(order_id)

    def aggregate(self, order_id: Any) -> dict[str, Any] | None:
        trades = self._orders.get(str(order_id))
        if not trades:
            return None
        quantity = sum(t["quantity"] for t in trades.values())
        notional = sum(t["price"] * t["quantity"] for t in trades.values())
        fees = [t for t in trades.values() if t["fee"] is not None]
        asset = next((t["fee_asset"] for t in fees if t["fee_asset"]), None)
        return {
            "entry_price": notional / quantity,
            "quantity": quantity,
            # Fees in another asset than the first one are left out of the sum (they are not comparable).
            "commission_total": sum(
                t["fee"] for t in fees if not asset or t["fee_asset"] in (None, asset)
            )
            if fees
            else None,
            "commission_asset": asset,
            "trade_ids": [t["trade_id"] for t in trades.values() if t["trade_id"]],
        }
