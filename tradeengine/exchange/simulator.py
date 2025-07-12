import logging
import random
import uuid
from datetime import UTC, datetime
from typing import Any

from contracts.order import TradeOrder
from shared.constants import (
    SIMULATION_DELAY_MS,
    SIMULATION_SLIPPAGE,
    SIMULATION_SUCCESS_RATE,
)

logger = logging.getLogger(__name__)


class TradeSimulator:
    """Simulates trade execution for testing and development"""

    def __init__(self) -> None:
        self.simulated_slippage = SIMULATION_SLIPPAGE
        self.success_rate = SIMULATION_SUCCESS_RATE
        self.delay_ms = SIMULATION_DELAY_MS

    async def execute(self, order: TradeOrder) -> dict[str, Any]:
        """Execute a simulated trade order"""
        logger.info(
            "Executing simulated %s %s order for %s %s",
            order.side,
            order.type,
            order.amount,
            order.symbol,
        )

        # Simulate execution delay
        import asyncio

        await asyncio.sleep(self.delay_ms / 1000.0)

        # Simulate occasional failures
        if random.random() > self.success_rate:
            return {
                "order_id": str(uuid.uuid4()),
                "status": "failed",
                "error": "Simulated execution failure",
                "timestamp": datetime.now(UTC).isoformat(),
                "simulated": True,
                "original_order": order.model_dump(),
            }

        # Calculate simulated fill price with slippage
        fill_price = self._calculate_fill_price(order)

        # Generate successful execution result
        result = {
            "order_id": str(uuid.uuid4()),
            "status": "filled",
            "side": order.side,
            "type": order.type,
            "amount": order.amount,
            "fill_price": round(fill_price, 2),
            "total_value": round(order.amount * fill_price, 2),
            "fees": round(order.amount * fill_price * 0.001, 4),  # 0.1% fees
            "timestamp": datetime.now(UTC).isoformat(),
            "simulated": True,
            "fills": self._generate_fills(order, fill_price),
            "original_order": order.model_dump(),
        }

        logger.info(
            "Simulated trade executed: %s - %s", result["order_id"], result["status"]
        )
        return result

    def _calculate_fill_price(self, order: TradeOrder) -> float:
        """Calculate fill price based on order type"""
        base_price = order.target_price or 45000.0  # Default BTC price

        # Apply slippage based on order side
        if order.side == "buy":
            fill_price = base_price * (1 + self.simulated_slippage)
        else:
            fill_price = base_price * (1 - self.simulated_slippage)

        # For stop orders, use the stop price as base
        if order.type in ["stop", "stop_limit"] and order.stop_loss:
            fill_price = order.stop_loss

        # For take profit orders, use the take profit price as base
        if order.type in ["take_profit", "take_profit_limit"] and order.take_profit:
            fill_price = order.take_profit

        return fill_price

    def _generate_fills(self, order: TradeOrder, fill_price: float) -> list:
        """Generate simulated fill data"""
        return [
            {
                "price": str(fill_price),
                "qty": str(order.amount),
                "commission": str(order.amount * fill_price * 0.001),
                "commissionAsset": "USDT",
                "tradeId": random.randint(1000000, 9999999),
            }
        ]


# Global simulator instance
simulator = TradeSimulator()
