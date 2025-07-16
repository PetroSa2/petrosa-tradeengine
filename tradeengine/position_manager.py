"""
Position Manager - Tracks positions and enforces risk limits with distributed state management using MongoDB
"""

import asyncio
import logging
from datetime import datetime
from typing import Any

from contracts.order import TradeOrder
from shared.audit import audit_logger
from shared.config import Settings
from shared.constants import (
    MAX_DAILY_LOSS_PCT,
    MAX_PORTFOLIO_EXPOSURE_PCT,
    MAX_POSITION_SIZE_PCT,
    RISK_MANAGEMENT_ENABLED,
    get_mongodb_connection_string,
)

logger = logging.getLogger(__name__)


class PositionManager:
    """Manages trading positions and risk limits with distributed state management using MongoDB"""

    def __init__(self) -> None:
        self.positions: dict[str, dict[str, Any]] = {}
        self.daily_pnl: float = 0.0
        self.max_position_size_pct: float = MAX_POSITION_SIZE_PCT
        self.max_daily_loss_pct: float = MAX_DAILY_LOSS_PCT
        self.max_portfolio_exposure_pct: float = MAX_PORTFOLIO_EXPOSURE_PCT
        self.total_portfolio_value: float = (
            10000.0  # Placeholder, would integrate with account
        )
        self.last_sync_time: datetime | None = None
        self.sync_lock = asyncio.Lock()
        self.settings = Settings()
        self.mongodb_client: Any = None
        self.mongodb_db: Any = None

    async def initialize(self) -> None:
        """Initialize position manager with MongoDB persistence"""
        try:
            # Initialize MongoDB connection
            await self._initialize_mongodb()

            # Load positions from MongoDB
            await self._load_positions_from_mongodb()

            # Load daily P&L
            await self._load_daily_pnl_from_mongodb()

            # Start periodic sync
            asyncio.create_task(self._periodic_sync())

            logger.info(
                f"Position manager initialized with {len(self.positions)} positions"
            )
        except Exception as e:
            logger.error(f"Failed to initialize position manager: {e}")
            # Fallback: load from exchange
            await self._load_positions_from_exchange()

    async def close(self) -> None:
        """Close position manager and sync final state"""
        try:
            await self._sync_positions_to_mongodb()
            if self.mongodb_client:
                self.mongodb_client.close()
            logger.info("Position manager closed successfully")
        except Exception as e:
            logger.error(f"Error closing position manager: {e}")

    async def _initialize_mongodb(self) -> None:
        """Initialize MongoDB connection"""
        try:
            import motor.motor_asyncio

            # Get MongoDB connection string from constants with validation
            from shared.constants import get_mongodb_connection_string, MONGODB_DATABASE
            mongodb_url = self.settings.mongodb_uri or get_mongodb_connection_string()
            database_name = self.settings.mongodb_database or MONGODB_DATABASE

            self.mongodb_client = motor.motor_asyncio.AsyncIOMotorClient(mongodb_url)
            self.mongodb_db = self.mongodb_client[database_name]

            # Test connection
            await self.mongodb_client.admin.command("ping")
            logger.info(f"MongoDB connected: {mongodb_url}")

        except Exception as e:
            logger.error(f"Failed to initialize MongoDB: {e}")
            self.mongodb_client = None
            self.mongodb_db = None
            raise

    async def _load_positions_from_mongodb(self) -> None:
        """Load positions from MongoDB"""
        if self.mongodb_db is None:
            logger.warning("MongoDB not available, skipping position load")
            return

        try:
            positions_collection = self.mongodb_db.positions

            # Find all open positions
            cursor = positions_collection.find({"status": "open"})
            positions = {}

            async for doc in cursor:
                symbol = doc["symbol"]
                positions[symbol] = {
                    "quantity": float(doc["quantity"]),
                    "avg_price": float(doc["avg_price"]),
                    "unrealized_pnl": float(doc["unrealized_pnl"]),
                    "realized_pnl": float(doc["realized_pnl"]),
                    "total_cost": float(doc["total_cost"]),
                    "total_value": float(doc["total_value"]),
                    "entry_time": doc["entry_time"],
                    "last_update": doc["last_update"],
                    "status": doc["status"],
                }

            self.positions = positions
            self.last_sync_time = datetime.utcnow()
            logger.info(f"Loaded {len(positions)} positions from MongoDB")

        except Exception as e:
            logger.error(f"Failed to load positions from MongoDB: {e}")
            raise

    async def _load_daily_pnl_from_mongodb(self) -> None:
        """Load daily P&L from MongoDB"""
        if self.mongodb_db is None:
            return

        try:
            daily_pnl_collection = self.mongodb_db.daily_pnl
            today = datetime.utcnow().date()

            doc = await daily_pnl_collection.find_one({"date": today.isoformat()})
            if doc:
                self.daily_pnl = float(doc["daily_pnl"])
                logger.info(f"Loaded daily P&L: {self.daily_pnl}")
        except Exception as e:
            logger.warning(f"Failed to load daily P&L: {e}")

    async def _load_positions_from_exchange(self) -> None:
        """Load positions from Binance API as fallback"""
        try:
            # This would integrate with the exchange to get real positions
            # For now, we'll simulate this
            logger.info("Loading positions from exchange (simulated)")
            # In real implementation, this would call Binance API
            # account_info = await exchange.get_account_info()
            # positions = account_info.get('positions', {})

        except Exception as e:
            logger.error(f"Failed to load positions from exchange: {e}")

    async def _sync_positions_to_mongodb(self) -> None:
        """Sync current positions to MongoDB"""
        if self.mongodb_db is None:
            logger.warning("MongoDB not available, skipping position sync")
            return

        async with self.sync_lock:
            try:
                positions_collection = self.mongodb_db.positions
                daily_pnl_collection = self.mongodb_db.daily_pnl

                # Clear existing open positions
                await positions_collection.delete_many({"status": "open"})

                # Insert current positions
                for symbol, position in self.positions.items():
                    await positions_collection.insert_one(
                        {
                            "symbol": symbol,
                            "quantity": position["quantity"],
                            "avg_price": position["avg_price"],
                            "unrealized_pnl": position["unrealized_pnl"],
                            "realized_pnl": position["realized_pnl"],
                            "total_cost": position["total_cost"],
                            "total_value": position["total_value"],
                            "entry_time": position["entry_time"],
                            "last_update": position["last_update"],
                            "status": "open",
                            "updated_at": datetime.utcnow(),
                        }
                    )

                # Update daily P&L
                today = datetime.utcnow().date().isoformat()
                await daily_pnl_collection.update_one(
                    {"date": today},
                    {
                        "$set": {
                            "daily_pnl": self.daily_pnl,
                            "updated_at": datetime.utcnow(),
                        }
                    },
                    upsert=True,
                )

                self.last_sync_time = datetime.utcnow()
                logger.debug("Positions synced to MongoDB")

            except Exception as e:
                logger.error(f"Failed to sync positions to MongoDB: {e}")

    async def _periodic_sync(self) -> None:
        """Periodically sync positions to MongoDB"""
        while True:
            try:
                await asyncio.sleep(30)  # Sync every 30 seconds
                await self._sync_positions_to_mongodb()
            except Exception as e:
                logger.error(f"Error in periodic sync: {e}")

    async def update_position(self, order: TradeOrder, result: dict[str, Any]) -> None:
        """Update position after order execution with distributed state management"""
        async with self.sync_lock:
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
                            await self._close_position_in_mongodb(symbol, position)
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

                # Sync to MongoDB immediately for critical updates
                await self._sync_positions_to_mongodb()

            except Exception as e:
                logger.error(f"Error updating position for {symbol}: {e}")
                audit_logger.log_error(
                    {"error": str(e)},
                    context={"order": order.model_dump(), "result": result},
                )

    async def _close_position_in_mongodb(
        self, symbol: str, position: dict[str, Any]
    ) -> None:
        """Mark position as closed in MongoDB"""
        if self.mongodb_db is None:
            return

        try:
            positions_collection = self.mongodb_db.positions
            await positions_collection.update_one(
                {"symbol": symbol},
                {
                    "$set": {
                        "status": "closed",
                        "last_update": datetime.utcnow(),
                        "closed_at": datetime.utcnow(),
                        "final_realized_pnl": position["realized_pnl"],
                    }
                },
            )
            logger.info(f"Position {symbol} marked as closed in MongoDB")
        except Exception as e:
            logger.error(f"Failed to close position {symbol} in MongoDB: {e}")

    async def check_position_limits(self, order: TradeOrder) -> bool:
        """Check if order meets position size limits with distributed state"""
        if not RISK_MANAGEMENT_ENABLED:
            return True

        # Refresh positions from MongoDB to ensure consistency
        await self._refresh_positions_from_mongodb()

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

    async def _refresh_positions_from_mongodb(self) -> None:
        """Refresh positions from MongoDB to ensure consistency across pods"""
        if self.mongodb_db is None:
            return

        try:
            positions_collection = self.mongodb_db.positions

            # Find all open positions
            cursor = positions_collection.find({"status": "open"})
            refreshed_positions = {}

            async for doc in cursor:
                symbol = doc["symbol"]
                refreshed_positions[symbol] = {
                    "quantity": float(doc["quantity"]),
                    "avg_price": float(doc["avg_price"]),
                    "unrealized_pnl": float(doc["unrealized_pnl"]),
                    "realized_pnl": float(doc["realized_pnl"]),
                    "total_cost": float(doc["total_cost"]),
                    "total_value": float(doc["total_value"]),
                    "entry_time": doc["entry_time"],
                    "last_update": doc["last_update"],
                    "status": doc["status"],
                }

            # Only update if positions have changed
            if refreshed_positions != self.positions:
                logger.info("Refreshing positions from MongoDB for consistency")
                self.positions = refreshed_positions

        except Exception as e:
            logger.error(f"Failed to refresh positions from MongoDB: {e}")

    async def check_daily_loss_limits(self) -> bool:
        """Check daily loss limits with distributed state"""
        if not RISK_MANAGEMENT_ENABLED:
            return True

        # Refresh daily P&L from MongoDB
        await self._refresh_daily_pnl_from_mongodb()

        max_daily_loss = self.total_portfolio_value * self.max_daily_loss_pct

        if self.daily_pnl < -max_daily_loss:
            logger.warning(
                f"Daily loss {self.daily_pnl:.2f} exceeds limit {-max_daily_loss:.2f}"
            )
            return False

        return True

    async def _refresh_daily_pnl_from_mongodb(self) -> None:
        """Refresh daily P&L from MongoDB"""
        if self.mongodb_db is None:
            return

        try:
            daily_pnl_collection = self.mongodb_db.daily_pnl
            today = datetime.utcnow().date().isoformat()

            doc = await daily_pnl_collection.find_one({"date": today})
            if doc:
                self.daily_pnl = float(doc["daily_pnl"])
        except Exception as e:
            logger.warning(f"Failed to refresh daily P&L: {e}")

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
            "last_sync_time": self.last_sync_time.isoformat()
            if self.last_sync_time
            else None,
            "mongodb_connected": self.mongodb_db is not None,
        }

    async def reset_daily_pnl(self) -> None:
        """Reset daily P&L (call at start of new day)"""
        self.daily_pnl = 0.0
        logger.info("Daily P&L reset")

        # Sync to MongoDB
        if self.mongodb_db is not None:
            try:
                daily_pnl_collection = self.mongodb_db.daily_pnl
                today = datetime.utcnow().date().isoformat()
                await daily_pnl_collection.update_one(
                    {"date": today},
                    {"$set": {"daily_pnl": 0.0, "updated_at": datetime.utcnow()}},
                    upsert=True,
                )
            except Exception as e:
                logger.error(f"Failed to reset daily P&L in MongoDB: {e}")

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

    async def health_check(self) -> dict[str, Any]:
        """Health check for position manager"""
        return {
            "status": "healthy",
            "positions_count": len(self.positions),
            "last_sync": self.last_sync_time.isoformat()
            if self.last_sync_time
            else None,
            "mongodb_connected": self.mongodb_db is not None,
            "mongodb_uri": self.settings.mongodb_uri
            if self.settings.mongodb_uri
            else get_mongodb_connection_string(),
        }


# Global position manager instance
position_manager = PositionManager()
