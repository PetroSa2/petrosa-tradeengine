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


class SimulatorExchange:
    """Simulator exchange for testing and development"""

    def __init__(self, nats_client: Any = None) -> None:
        self.nc = nats_client
        self.simulator = TradeSimulator()
        self.logger = logging.getLogger(__name__)

    async def initialize(self) -> None:
        """Initialize simulator exchange"""
        self.logger.info("Simulator exchange initialized")

    async def close(self) -> None:
        """Close simulator exchange"""
        self.logger.info("Simulator exchange closed")

    async def health_check(self) -> dict[str, Any]:
        """Check simulator health"""
        return {"status": "healthy", "type": "simulator"}

    async def get_account_info(self) -> dict[str, Any]:
        """Get simulated account information"""
        return {
            "balances": {
                "BTC": {"free": "0.1", "locked": "0.0"},
                "USDT": {"free": "5000.0", "locked": "0.0"},
            },
            "positions": {},
            "pnl": {"total": 0.0, "daily": 0.0},
            "risk_metrics": {"max_position_size": 0.1, "max_daily_loss": 100.0},
        }

    async def get_price(self, symbol: str) -> float:
        """Get simulated price for a symbol"""
        # Simulate realistic prices
        base_prices = {
            "BTCUSDT": 45000.0,
            "ETHUSDT": 3000.0,
            "ADAUSDT": 0.5,
            "DOTUSDT": 7.0,
        }
        base_price = base_prices.get(symbol, 100.0)
        # Add some random variation
        variation = random.uniform(-0.02, 0.02)  # ±2%
        return base_price * (1 + variation)

    async def execute_order(self, order: TradeOrder) -> dict[str, Any]:
        """Execute order through simulator"""
        return await self.simulator.execute(order)

    async def cancel_order(self, symbol: str, order_id: str) -> dict[str, Any]:
        """Cancel order in simulator"""
        return {
            "success": True,
            "order_id": order_id,
            "symbol": symbol,
            "status": "cancelled",
            "simulated": True,
        }

    async def get_order_status(self, symbol: str, order_id: str) -> dict[str, Any]:
        """Get order status from simulator"""
        return {
            "order_id": order_id,
            "symbol": symbol,
            "status": "filled",
            "simulated": True,
        }

    async def get_metrics(self) -> dict[str, Any]:
        """Get simulator metrics"""
        return {
            "orders_executed": 0,
            "total_volume": 0.0,
            "success_rate": 1.0,
            "average_execution_time": 0.1,
        }


class TradeSimulator:
    """Simulates trade execution for testing and development"""

    def __init__(self) -> None:
        self.simulated_slippage = SIMULATION_SLIPPAGE
        self.success_rate = SIMULATION_SUCCESS_RATE
        self.delay_ms = SIMULATION_DELAY_MS

    async def execute(self, order: TradeOrder) -> dict[str, Any]:
        """
        Execute a simulated trade order.

        Args:
            order: The trade order to execute.

        Returns:
            A dictionary containing execution results.
        """
        import asyncio

        # Simulate network latency
        await asyncio.sleep(self.delay_ms / 1000.0)

        # Determine success based on success rate
        if random.random() > self.success_rate:
            return {
                "order_id": order.order_id or str(uuid.uuid4()),
                "status": "failed",
                "error": "Simulated execution failure",
                "simulated": True,
                "timestamp": datetime.now(UTC).isoformat(),
            }

        # Calculate simulated fill price with slippage
        fill_price = self._calculate_fill_price(order)

        return {
            "status": "filled",
            "order_id": order.order_id or str(uuid.uuid4()),
            "exchange_id": f"sim_{order.order_id or uuid.uuid4()}",
            "symbol": order.symbol,
            "side": order.side,
            "type": getattr(order, "type", "market"),
            "amount": order.amount,
            "filled_amount": order.amount,
            "fill_price": round(fill_price, 2),
            "average_price": round(fill_price, 2),
            "total_value": round(order.amount * fill_price, 2),
            "fees": round(order.amount * fill_price * 0.001, 4),  # 0.1% fees
            "timestamp": datetime.now(UTC).isoformat(),
            "simulated": True,
            "fills": self._generate_fills(order, fill_price),
        }

    def _calculate_fill_price(self, order: TradeOrder) -> float:
        """Calculate fill price based on order type"""
        base_price = order.target_price or 45000.0  # Default price

        # Apply slippage based on order side
        if order.side == "buy":
            fill_price = base_price * (1 + self.simulated_slippage)
        else:
            fill_price = base_price * (1 - self.simulated_slippage)

        # For stop orders, use the stop price as base if available
        if getattr(order, "type", "") in ["stop", "stop_limit"] and getattr(
            order, "stop_price", None
        ):
            fill_price = order.stop_price
        elif getattr(order, "type", "") in ["stop", "stop_limit"] and getattr(
            order, "stop_loss", None
        ):
            # Compatibility with stop_loss field
            fill_price = order.stop_loss

        # For take profit orders, use the take profit price as base if available
        if getattr(order, "type", "") in [
            "take_profit",
            "take_profit_limit",
        ] and getattr(order, "take_profit", None):
            fill_price = order.take_profit

        return fill_price

    def _generate_fills(
        self, order: TradeOrder, fill_price: float
    ) -> list[dict[str, Any]]:
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
