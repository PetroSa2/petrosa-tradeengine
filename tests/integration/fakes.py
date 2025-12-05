"""
Fake implementations for integration testing.

These fakes provide simple, predictable in-memory behavior for testing
without requiring external dependencies (MongoDB, Data Manager, Exchange APIs).
"""

from typing import Any

from contracts.order import TradeOrder


class FakeExchange:
    """Fake exchange for integration testing.

    Tracks executed orders in memory without making actual API calls.
    """

    def __init__(self) -> None:
        self.executed_orders: list[dict[str, Any]] = []
        self._order_counter = 0
        self._open_orders: dict[str, list[dict[str, Any]]] = {}
        self._cancelled_orders: set[str] = set()

    async def initialize(self) -> None:
        """Initialize fake exchange (no-op)."""
        pass

    async def close(self) -> None:
        """Close fake exchange (no-op)."""
        pass

    async def execute(self, order: TradeOrder) -> dict[str, Any]:
        """Execute an order and track it in memory.

        Args:
            order: The trade order to execute

        Returns:
            Dict with order_id, status, fill_price, amount, symbol
        """
        self._order_counter += 1
        order_id = f"fake_order_{self._order_counter}"

        # Track executed order
        result = {
            "order_id": order_id,
            "status": "filled",
            "fill_price": order.target_price or 50000.0,
            "amount": order.amount,
            "symbol": order.symbol,
        }
        self.executed_orders.append(result)

        # Add to open orders if it's a limit/stop order
        if order.type in [
            "limit",
            "stop",
            "stop_limit",
            "take_profit",
            "take_profit_limit",
        ]:
            symbol = order.symbol
            if symbol not in self._open_orders:
                self._open_orders[symbol] = []
            self._open_orders[symbol].append({"orderId": order_id, "status": "NEW"})

        return result

    async def execute_order(self, order: TradeOrder) -> dict[str, Any]:
        """Alias for execute() for compatibility."""
        return await self.execute(order)

    def get_executed_orders(self) -> list[dict[str, Any]]:
        """Get all executed orders."""
        return self.executed_orders.copy()

    def clear_executed_orders(self) -> None:
        """Clear all executed orders."""
        self.executed_orders.clear()

    def fill_order(
        self, symbol: str, order_id: str, fill_price: float | None = None
    ) -> None:
        """Simulate an order being filled (removes from open orders).

        Args:
            symbol: Trading symbol
            order_id: Order ID to fill
            fill_price: Optional fill price (defaults to order target_price)
        """
        if symbol in self._open_orders:
            self._open_orders[symbol] = [
                o for o in self._open_orders[symbol] if o["orderId"] != order_id
            ]

    def was_cancelled(self, order_id: str) -> bool:
        """Check if an order was cancelled.

        Args:
            order_id: Order ID to check

        Returns:
            True if order was cancelled, False otherwise
        """
        return order_id in self._cancelled_orders

    async def cancel_order(self, symbol: str, order_id: str) -> dict[str, Any]:
        """Cancel an order.

        Args:
            symbol: Trading symbol
            order_id: Order ID to cancel

        Returns:
            Dict with orderId and status
        """
        self._cancelled_orders.add(order_id)
        if symbol in self._open_orders:
            self._open_orders[symbol] = [
                o for o in self._open_orders[symbol] if o["orderId"] != order_id
            ]
        return {"orderId": order_id, "status": "CANCELED"}

    async def get_open_orders(self, symbol: str) -> list[dict[str, Any]]:
        """Get open orders for a symbol.

        Args:
            symbol: Trading symbol

        Returns:
            List of open order dicts
        """
        return self._open_orders.get(symbol, []).copy()


class FakePositionManager:
    """Fake position manager for integration testing.

    Provides configurable risk limits and in-memory position tracking
    without requiring MongoDB or Data Manager.
    """

    def __init__(
        self,
        max_position_size_pct: float = 0.1,
        max_daily_loss_pct: float = 0.05,
        max_portfolio_exposure_pct: float = 0.8,
        total_portfolio_value: float = 10000.0,
    ) -> None:
        """Initialize fake position manager with configurable risk limits.

        Args:
            max_position_size_pct: Maximum position size as % of portfolio (default: 10%)
            max_daily_loss_pct: Maximum daily loss as % of portfolio (default: 5%)
            max_portfolio_exposure_pct: Maximum portfolio exposure % (default: 80%)
            total_portfolio_value: Total portfolio value in USD (default: 10000.0)
        """
        self.positions: dict[tuple[str, str], dict[str, Any]] = {}
        self.daily_pnl: float = 0.0
        self.max_position_size_pct: float = max_position_size_pct
        self.max_daily_loss_pct: float = max_daily_loss_pct
        self.max_portfolio_exposure_pct: float = max_portfolio_exposure_pct
        self.total_portfolio_value: float = total_portfolio_value

    async def initialize(self) -> None:
        """Initialize fake position manager (no-op)."""
        pass

    async def close(self) -> None:
        """Close fake position manager (no-op)."""
        pass

    async def check_position_limits(self, order: TradeOrder) -> bool:
        """Check if order meets position size limits.

        Args:
            order: Trade order to check

        Returns:
            True if order passes limits, False otherwise
        """
        # Check individual position size limit (if position_size_pct is set)
        if order.position_size_pct:
            try:
                position_size_pct = float(order.position_size_pct)
                if position_size_pct > self.max_position_size_pct:
                    return False
            except (ValueError, TypeError):
                pass  # If position_size_pct is not a valid number, check by value instead

        # Check absolute position size (for both new and existing positions)
        position_side = "LONG" if order.side == "buy" else "SHORT"
        position_key = (order.symbol, position_side)

        # Calculate position value for this order
        order_price = order.target_price or 50000.0
        order_value = order.amount * order_price

        # Check portfolio exposure limit (including the new order)
        current_exposure = self._calculate_portfolio_exposure()
        new_order_exposure_pct = (
            order_value / self.total_portfolio_value
            if self.total_portfolio_value > 0
            else 0
        )
        total_exposure_after_order = current_exposure + new_order_exposure_pct
        if total_exposure_after_order > self.max_portfolio_exposure_pct:
            return False

        if position_key in self.positions:
            # Existing position - check total after adding this order
            current_quantity = self.positions[position_key]["quantity"]
            current_value = current_quantity * self.positions[position_key].get(
                "avg_price", order_price
            )
            new_total_value = current_value + order_value
        else:
            # New position - check this order's value
            new_total_value = order_value

        # Check against max position size limit
        max_position_value = self.total_portfolio_value * self.max_position_size_pct
        if new_total_value > max_position_value:
            return False

        return True

    async def check_daily_loss_limits(self) -> bool:
        """Check daily loss limits.

        Returns:
            True if daily loss is within limits, False otherwise
        """
        max_daily_loss = self.total_portfolio_value * self.max_daily_loss_pct
        if self.daily_pnl < -max_daily_loss:
            return False
        return True

    def _calculate_portfolio_exposure(self) -> float:
        """Calculate current portfolio exposure as percentage.

        Returns:
            Portfolio exposure as decimal (e.g., 0.8 for 80%)
        """
        total_exposure = 0.0
        for position in self.positions.values():
            if position["quantity"] > 0:
                # Calculate position value as percentage of portfolio
                position_value = position["quantity"] * position.get("avg_price", 0.0)
                if position_value > 0:
                    exposure_pct = position_value / self.total_portfolio_value
                    total_exposure += exposure_pct
        return total_exposure

    async def update_position(self, order: TradeOrder, result: dict[str, Any]) -> None:
        """Update position after order execution.

        Args:
            order: Trade order
            result: Order execution result
        """
        symbol = order.symbol
        position_side = order.position_side or (
            "LONG" if order.side == "buy" else "SHORT"
        )
        position_key = (symbol, position_side)

        fill_price = result.get("fill_price", order.target_price or 0.0)
        fill_quantity = result.get("amount", order.amount)

        if position_key not in self.positions:
            self.positions[position_key] = {
                "symbol": symbol,
                "position_side": position_side,
                "quantity": 0.0,
                "avg_price": 0.0,
                "total_value": 0.0,
            }

        position = self.positions[position_key]

        # Add to position
        is_adding = (position_side == "LONG" and order.side == "buy") or (
            position_side == "SHORT" and order.side == "sell"
        )

        if is_adding:
            new_quantity = position["quantity"] + fill_quantity
            if new_quantity > 0:
                new_avg_price = (
                    position["quantity"] * position["avg_price"]
                    + fill_quantity * fill_price
                ) / new_quantity
                position["quantity"] = new_quantity
                position["avg_price"] = new_avg_price
                position["total_value"] = new_quantity * fill_price
        else:
            # Reduce position
            position["quantity"] = max(0.0, position["quantity"] - fill_quantity)
            if position["quantity"] > 0:
                position["total_value"] = position["quantity"] * fill_price
            else:
                # Position closed
                del self.positions[position_key]

    async def create_position_record(
        self, order: TradeOrder, result: dict[str, Any]
    ) -> None:
        """Create position record (no-op for fake)."""
        pass

    async def update_position_risk_orders(
        self,
        position_id: str,
        stop_loss_order_id: str | None = None,
        take_profit_order_id: str | None = None,
    ) -> None:
        """Update position risk orders (no-op for fake)."""
        pass

    def get_positions(self) -> dict[tuple[str, str], dict[str, Any]]:
        """Get all current positions.

        Returns:
            Dict mapping (symbol, position_side) to position data
        """
        return self.positions.copy()

    def set_portfolio_value(self, value: float) -> None:
        """Set total portfolio value.

        Args:
            value: Portfolio value in USD
        """
        self.total_portfolio_value = value

    def set_risk_limits(
        self,
        max_position_size_pct: float,
        max_daily_loss_pct: float,
        max_portfolio_exposure_pct: float,
    ) -> None:
        """Set risk management limits.

        Args:
            max_position_size_pct: Maximum position size as % of portfolio
            max_daily_loss_pct: Maximum daily loss as % of portfolio
            max_portfolio_exposure_pct: Maximum portfolio exposure %
        """
        self.max_position_size_pct = max_position_size_pct
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_portfolio_exposure_pct = max_portfolio_exposure_pct
