import logging
import random
import uuid
from datetime import datetime, timezone

try:
    from datetime import UTC
except ImportError:
    UTC = timezone.utc

from typing import Any

from contracts.order import TradeOrder
from shared.constants import (
    SIMULATION_DELAY_MS,
    SIMULATION_SLIPPAGE,
    SIMULATION_SUCCESS_RATE,
)

logger = logging.getLogger(__name__)


class SimulatorExchange:
    """Simulator exchange for testing and development"""

    def __init__(self, nats_client: Any = None):
        self.nc = nats_client
        self.orders = {}
        self.trades = []
        logger.info("Initialized SimulatorExchange")

    async def initialize(self) -> None:
        """Initialize simulator exchange"""
        logger.info("SimulatorExchange initialized")

    async def get_price(self, symbol: str) -> float:
        """Get current market price for a symbol"""
        # Return a random price for simulation
        return random.uniform(20000.0, 60000.0) if "BTC" in symbol else random.uniform(1500.0, 4000.0)

    async def create_order(self, order: TradeOrder) -> dict[str, Any]:
        """Create a new order on the exchange"""
        # Simulate network delay
        import asyncio
        await asyncio.sleep(SIMULATION_DELAY_MS / 1000.0)

        # Simulate success/failure
        if random.random() > SIMULATION_SUCCESS_RATE:
            logger.error(f"Simulator: Failed to place order {order.order_id}")
            return {
                "status": "error",
                "error": "Simulator: Random exchange failure",
                "order_id": order.order_id
            }

        # Apply slippage
        price = order.target_price or await self.get_price(order.symbol)
        slippage = price * SIMULATION_SLIPPAGE
        if order.side == "buy":
            execution_price = price + slippage
        else:
            execution_price = price - slippage

        # Create exchange order ID
        exchange_id = f"sim_{uuid.uuid4()}"
        
        # Store order
        result = {
            "status": "success",
            "order_id": exchange_id,
            "client_order_id": order.order_id,
            "symbol": order.symbol,
            "side": order.side,
            "price": execution_price,
            "amount": order.amount,
            "filled_amount": order.amount,
            "average_price": execution_price,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        
        self.orders[exchange_id] = result
        logger.info(f"Simulator: Order {order.order_id} placed successfully as {exchange_id}")
        
        return result

    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        """Cancel an existing order"""
        if order_id in self.orders:
            order = self.orders.pop(order_id)
            logger.info(f"Simulator: Order {order_id} cancelled")
            return {"status": "success", "order_id": order_id}
        else:
            logger.error(f"Simulator: Order {order_id} not found")
            return {"status": "error", "error": "Order not found", "order_id": order_id}

    async def get_order_status(self, order_id: str) -> dict[str, Any]:
        """Get the status of an order"""
        if order_id in self.orders:
            return self.orders[order_id]
        else:
            return {"status": "error", "error": "Order not found", "order_id": order_id}

    async def get_account_balance(self) -> dict[str, Any]:
        """Get account balance"""
        return {
            "USDT": 100000.0,
            "BTC": 1.5,
            "ETH": 20.0
        }
