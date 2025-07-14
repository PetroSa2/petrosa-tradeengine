"""
Position Manager - Tracks positions and enforces risk limits
"""

import logging
from datetime import datetime
from typing import Any

from contracts.order import TradeOrder
from shared.audit import audit_logger
from shared.constants import (
    MAX_DAILY_LOSS_PCT,
    MAX_PORTFOLIO_EXPOSURE_PCT,
    MAX_POSITION_SIZE_PCT,
    RISK_MANAGEMENT_ENABLED,
)

logger = logging.getLogger(__name__)


class PositionManager:
    """Manages trading positions and risk limits"""

    def __init__(self) -> None:
        self.positions: dict[str, dict[str, Any]] = {}
        self.daily_pnl: float = 0.0
        self.max_position_size_pct: float = MAX_POSITION_SIZE_PCT
        self.max_daily_loss_pct: float = MAX_DAILY_LOSS_PCT
        self.max_portfolio_exposure_pct: float = MAX_PORTFOLIO_EXPOSURE_PCT
        self.total_portfolio_value: float = (
            10000.0  # Placeholder, would integrate with account
        )

    async def initialize(self) -> None:
        pass

    async def close(self) -> None:
        pass

    def get_metrics(self) -> dict[str, Any]:
        return {}

    async def update_position(self, order: TradeOrder, result: dict[str, Any]) -> None:
        """Update position after order execution"""
        symbol = order.symbol
        try:
            if symbol not in self.positions:
                self.positions[symbol] = {
                    "quantity": 0.0,
                    "avg_price": 0.0,
                    "unrealized_pnl": 0.0,
                    "realized_pnl": 0.0,
                    "last_update": datetime.utcnow(),
                    "entry_time": datetime.utcnow(),
                    "total_cost": 0.0,
                    "total_value": 0.0,
                }
            position = self.positions[symbol]
            fill_price = result.get("fill_price", order.target_price or 0)
            fill_quantity = result.get("amount", order.amount)

            if order.side == "buy":
                # Add to position
                new_quantity = position["quantity"] + fill_quantity
                if new_quantity > 0:
                    new_avg_price = (
                        position["quantity"] * position["avg_price"]
                        + fill_quantity * fill_price
                    ) / new_quantity
                    position["quantity"] = new_quantity
                    position["avg_price"] = new_avg_price
                    position["total_cost"] += fill_quantity * fill_price
                    position["total_value"] = new_quantity * fill_price

            elif order.side == "sell":
                # Reduce position
                if position["quantity"] > 0:
                    # Calculate realized P&L
                    realized_pnl = (fill_price - position["avg_price"]) * min(
                        fill_quantity, position["quantity"]
                    )
                    position["realized_pnl"] += realized_pnl
                    self.daily_pnl += realized_pnl

                    # Update position
                    position["quantity"] -= fill_quantity
                    position["total_value"] = position["quantity"] * fill_price

                    if position["quantity"] <= 0:
                        # Position closed
                        logger.info(
                            f"Position closed for {symbol}, "
                            f"total realized P&L: {position['realized_pnl']:.2f}"
                        )
                        audit_logger.log_position(position, status="closed")
                        del self.positions[symbol]
                        return

            position["last_update"] = datetime.utcnow()
            position["unrealized_pnl"] = (
                fill_price - position["avg_price"]
            ) * position["quantity"]

            logger.info(
                f"Updated position for {symbol}: "
                f"quantity={position['quantity']:.6f}, "
                f"avg_price={position['avg_price']:.2f}"
            )
            audit_logger.log_position(position, status="updated")
        except Exception as e:
            logger.error(f"Error updating position for {symbol}: {e}")
            audit_logger.log_error(
                {"error": str(e)},
                context={"order": order.model_dump(), "result": result},
            )

    async def check_position_limits(self, order: TradeOrder) -> bool:
        """Check if order meets position size limits"""
        if not RISK_MANAGEMENT_ENABLED:
            return True

        # Check individual position size limit
        if (
            order.position_size_pct
            and order.position_size_pct > self.max_position_size_pct
        ):
            logger.warning(
                f"Position size {order.position_size_pct} exceeds limit "
                f"{self.max_position_size_pct}"
            )
            return False

        # Check portfolio exposure limit
        current_exposure = self._calculate_portfolio_exposure()
        if current_exposure > self.max_portfolio_exposure_pct:
            logger.warning(
                f"Portfolio exposure {current_exposure:.2%} exceeds limit "
                f"{self.max_portfolio_exposure_pct:.2%}"
            )
            return False

        return True

    async def check_daily_loss_limits(self) -> bool:
        """Check daily loss limits"""
        if not RISK_MANAGEMENT_ENABLED:
            return True

        max_daily_loss = self.total_portfolio_value * self.max_daily_loss_pct

        if self.daily_pnl < -max_daily_loss:
            logger.warning(
                f"Daily loss {self.daily_pnl:.2f} exceeds limit {-max_daily_loss:.2f}"
            )
            return False

        return True

    def _calculate_portfolio_exposure(self) -> float:
        """Calculate current portfolio exposure"""
        total_exposure = 0.0

        for position in self.positions.values():
            if position["quantity"] > 0:
                # Calculate position value as percentage of portfolio
                position_value = position["quantity"] * position["avg_price"]
                exposure_pct = position_value / self.total_portfolio_value
                total_exposure += exposure_pct

        return total_exposure

    def get_positions(self) -> dict[str, dict[str, Any]]:
        """Get all current positions"""
        return self.positions.copy()

    def get_position(self, symbol: str) -> dict[str, Any] | None:
        """Get specific position"""
        return self.positions.get(symbol)

    def get_daily_pnl(self) -> float:
        """Get current daily P&L"""
        return self.daily_pnl

    def get_total_unrealized_pnl(self) -> float:
        """Get total unrealized P&L across all positions"""
        total_unrealized = 0.0
        for position in self.positions.values():
            total_unrealized += position.get("unrealized_pnl", 0.0)
        return total_unrealized

    def get_portfolio_summary(self) -> dict[str, Any]:
        """Get portfolio summary"""
        total_positions = len(self.positions)
        total_exposure = self._calculate_portfolio_exposure()
        total_unrealized = self.get_total_unrealized_pnl()

        return {
            "total_positions": total_positions,
            "total_exposure": total_exposure,
            "daily_pnl": self.daily_pnl,
            "total_unrealized_pnl": total_unrealized,
            "portfolio_value": self.total_portfolio_value,
            "max_position_size_pct": self.max_position_size_pct,
            "max_daily_loss_pct": self.max_daily_loss_pct,
            "max_portfolio_exposure_pct": self.max_portfolio_exposure_pct,
        }

    def reset_daily_pnl(self) -> None:
        """Reset daily P&L (call at start of new day)"""
        self.daily_pnl = 0.0
        logger.info("Daily P&L reset")

    def set_portfolio_value(self, value: float) -> None:
        """Set total portfolio value"""
        self.total_portfolio_value = value
        logger.info(f"Portfolio value updated to {value:.2f}")

    def set_risk_limits(
        self,
        max_position_size_pct: float,
        max_daily_loss_pct: float,
        max_portfolio_exposure_pct: float,
    ) -> None:
        """Set risk management limits"""
        self.max_position_size_pct = max_position_size_pct
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_portfolio_exposure_pct = max_portfolio_exposure_pct
        logger.info(
            f"Risk limits updated: position={max_position_size_pct:.1%}, "
            f"daily_loss={max_daily_loss_pct:.1%}, "
            f"exposure={max_portfolio_exposure_pct:.1%}"
        )


# Global position manager instance
position_manager = PositionManager()
