from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from typing import Any

from contracts.order import TradeOrder


class MakerEntryExecutor:
    def __init__(
        self,
        exchange: Any,
        timeout_seconds: float,
        fallback: str,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self.exchange = exchange
        self.timeout_seconds = max(0.0, float(timeout_seconds))
        self.fallback = fallback
        self.clock = clock
        self.sleeper = sleeper

    async def execute(self, order: TradeOrder) -> dict[str, Any]:
        price = await self.exchange.get_best_entry_price(order.symbol, order.side)
        maker = order.model_copy(
            update={
                "type": "limit",
                "target_price": price,
                "time_in_force": "GTX",
                "client_order_id": order.client_order_id
                or f"maker_{uuid.uuid4().hex[:20]}",
            }
        )
        started = self.clock()
        result = await self.exchange.execute(maker)
        order_id = result.get("order_id")
        filled = self._quantity(result)
        final = result
        while self.clock() - started < self.timeout_seconds:
            if self._status(final) in {
                "filled",
                "cancelled",
                "canceled",
                "rejected",
                "failed",
            }:
                break
            await self.sleeper(min(0.1, max(0.0, self.timeout_seconds)))
            if order_id is not None:
                final = await self.exchange.get_order_status(order.symbol, order_id)
            filled = max(filled, self._quantity(final))
            if self._status(final) == "filled":
                break

        status = self._status(final)
        if (
            status not in {"filled", "rejected", "failed", "cancelled", "canceled"}
            and order_id is not None
        ):
            final = await self.exchange.cancel_order(order.symbol, order_id)
            if order_id is not None:
                try:
                    final = await self.exchange.get_order_status(order.symbol, order_id)
                except Exception:
                    pass
            filled = max(filled, self._quantity(final))

        remaining = max(0.0, float(order.amount) - filled)
        maker_result = dict(final)
        maker_result["post_only_rejected"] = self._status(result) in {
            "rejected",
            "failed",
            "error",
        }
        maker_result["maker_filled_amount"] = filled
        maker_result["maker_order_id"] = order_id
        maker_result["intended_price"] = price
        maker_result["fill_latency_ms"] = max(0.0, self.clock() - started) * 1000
        maker_result["maker"] = filled > 0
        maker_result["liquidity"] = "maker"

        if remaining > 0 and self.fallback == "market":
            fallback_order = order.model_copy(
                update={
                    "type": "market",
                    "amount": remaining,
                    "client_order_id": f"{maker.client_order_id}_fallback",
                }
            )
            fallback_result = await self.exchange.execute(fallback_order)
            total = filled + self._quantity(fallback_result)
            return self._combine(
                maker_result,
                fallback_result,
                total,
                float(order.amount),
                "maker_fallback_market",
            )
        if filled == 0:
            maker_result["status"] = "cancelled"
            maker_result["entry_mode"] = "maker_unfilled"
            maker_result["maker_unfilled"] = True
        elif remaining > 0:
            maker_result["status"] = "partially_filled"
            maker_result["entry_mode"] = "maker_partial"
        else:
            maker_result["status"] = "filled"
            maker_result["entry_mode"] = "maker"
        maker_result["amount"] = filled
        return maker_result

    @staticmethod
    def _status(result: dict[str, Any]) -> str:
        return str(result.get("status", "")).lower()

    @staticmethod
    def _quantity(result: dict[str, Any]) -> float:
        value = result.get("amount", result.get("filled", result.get("executedQty", 0)))
        try:
            return max(0.0, float(value or 0.0))
        except (TypeError, ValueError):
            return 0.0

    def _combine(
        self,
        maker: dict[str, Any],
        fallback: dict[str, Any],
        total: float,
        requested: float,
        entry_mode: str,
    ) -> dict[str, Any]:
        maker_qty = self._quantity(maker)
        fallback_qty = self._quantity(fallback)
        maker_price = float(
            maker.get("fill_price")
            or maker.get("average_price")
            or maker.get("intended_price")
            or 0
        )
        fallback_price = float(
            fallback.get("fill_price") or fallback.get("average_price") or 0
        )
        maker_fee = float(maker.get("fees", maker.get("fee", 0)) or 0)
        fallback_fee = float(fallback.get("fees", fallback.get("fee", 0)) or 0)
        weighted = (
            (maker_qty * maker_price + fallback_qty * fallback_price) / total
            if total
            else None
        )
        result = {**maker, **fallback}
        result.update(
            {
                "status": "filled" if total >= requested else "partially_filled",
                "amount": total,
                "fill_price": weighted,
                "entry_mode": entry_mode,
                "maker": False,
                "liquidity": "mixed",
                "maker_filled_amount": maker_qty,
                "fallback_amount": fallback_qty,
                "fees": maker_fee + fallback_fee,
                "fee": maker_fee + fallback_fee,
                "maker_fee": maker_fee,
                "taker_fee": fallback_fee,
                "post_only_rejected": maker.get("post_only_rejected", False),
            }
        )
        return result
