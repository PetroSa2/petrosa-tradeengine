import uuid
import random
from datetime import datetime
from typing import Dict, Any
from contracts.order import TradeOrder
import logging

logger = logging.getLogger(__name__)


class TradeSimulator:
    """Simulates trade execution for testing and development"""

    def __init__(self):
        self.simulated_slippage = 0.001  # 0.1% slippage
        self.success_rate = 0.95  # 95% success rate

    async def execute(self, order: TradeOrder) -> Dict[str, Any]:
        """Execute a simulated trade order"""
        logger.info(
            "Executing simulated %s %s order for %s",
            order.side,
            order.type,
            order.amount,
        )

        # Simulate execution delay
        import asyncio

        await asyncio.sleep(0.1)

        # Simulate occasional failures
        if random.random() > self.success_rate:
            return {
                "order_id": str(uuid.uuid4()),
                "status": "failed",
                "error": "Simulated execution failure",
                "timestamp": datetime.utcnow().isoformat(),
                "simulated": True,
            }

        # Calculate simulated fill price with slippage
        fill_price = order.target_price if order.target_price else 0.0
        if order.side == "buy":
            fill_price *= 1 + self.simulated_slippage
        else:
            fill_price *= 1 - self.simulated_slippage

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
            "timestamp": datetime.utcnow().isoformat(),
            "simulated": True,
        }

        logger.info(
            "Simulated trade executed: %s - %s", result["order_id"], result["status"]
        )
        return result


# Global simulator instance
simulator = TradeSimulator()
