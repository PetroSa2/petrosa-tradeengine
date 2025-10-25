"""
Position Manager - Tracks positions and enforces risk limits with distributed state
management using Data Manager API and MongoDB for coordination only.
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

# Import Data Manager position client
from shared.mysql_client import position_client
from tradeengine.metrics import (
    current_position_size,
    position_commission_usd,
    position_duration_seconds,
    position_entry_price,
    position_exit_price,
    position_pnl_percentage,
    position_pnl_usd,
    position_roi,
    positions_closed_total,
    positions_losing_total,
    positions_opened_total,
    positions_winning_total,
    total_daily_pnl_usd,
    total_position_value_usd,
    total_realized_pnl_usd,
    total_unrealized_pnl_usd,
)

logger = logging.getLogger(__name__)


class PositionManager:
    """Manages trading positions and risk limits with distributed state management
    using Data Manager API for persistence and MongoDB for coordination only."""

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
        """Initialize position manager with Data Manager API for persistence and MongoDB for coordination"""
        try:
            # Initialize MongoDB connection for distributed coordination only
            await self._initialize_mongodb()

            # Initialize Data Manager connection for position persistence
            try:
                await position_client.connect()
                logger.info("Data Manager client connected for position tracking")

                # Load positions from Data Manager (primary source)
                await self._load_positions_from_data_manager()

                # Load daily P&L from Data Manager
                await self._load_daily_pnl_from_data_manager()

            except Exception as data_manager_error:
                logger.error(
                    f"Data Manager connection failed: {data_manager_error}. "
                    "Position tracking disabled."
                )
                raise

            # Start periodic sync to Data Manager
            asyncio.create_task(self._periodic_sync())

            logger.info(
                f"Position manager initialized with {len(self.positions)} positions via Data Manager"
            )
        except Exception as e:
            logger.error(f"Failed to initialize position manager: {e}")
            # Fallback: load from exchange
            await self._load_positions_from_exchange()

    async def close(self) -> None:
        """Close position manager and sync final state"""
        try:
            await self._sync_positions_to_data_manager()
            if self.mongodb_client:
                self.mongodb_client.close()
            await position_client.disconnect()
            logger.info("Position manager closed successfully")
        except Exception as e:
            logger.error(f"Error closing position manager: {e}")

    async def _initialize_mongodb(self) -> None:
        """Initialize MongoDB connection"""
        try:
            import motor.motor_asyncio

            # Get MongoDB connection string from constants with validation
            from shared.constants import MONGODB_DATABASE, get_mongodb_connection_string

            mongodb_url = self.settings.mongodb_uri or get_mongodb_connection_string()
            database_name = self.settings.mongodb_database or MONGODB_DATABASE

            # Ensure database_name is a string
            if database_name is None:
                raise ValueError("MongoDB database name is required")

            self.mongodb_client = motor.motor_asyncio.AsyncIOMotorClient(mongodb_url)
            self.mongodb_db = self.mongodb_client[str(database_name)]

            # Test connection
            await self.mongodb_client.admin.command("ping")
            logger.info(f"MongoDB connected for position manager: {mongodb_url}")

        except Exception as e:
            logger.error(f"Failed to initialize MongoDB for position manager: {e}")
            self.mongodb_client = None
            self.mongodb_db = None
            raise

    async def _load_positions_from_data_manager(self) -> None:
        """Load positions from Data Manager with hedge mode support"""
        try:
            # Query all open positions from Data Manager
            positions_data = await position_client.get_open_positions()
            positions = {}

            for doc in positions_data:
                symbol = doc["symbol"]
                # Get position_side, default to LONG for backward compatibility
                position_side = doc.get("position_side", "LONG")
                position_key = (symbol, position_side)

                positions[position_key] = {
                    "symbol": symbol,
                    "position_side": position_side,
                    "quantity": float(doc.get("quantity", 0.0)),
                    "avg_price": float(doc.get("avg_price", 0.0)),
                    "unrealized_pnl": float(doc.get("unrealized_pnl", 0.0)),
                    "realized_pnl": float(doc.get("realized_pnl", 0.0)),
                    "total_cost": float(doc.get("total_cost", 0.0)),
                    "total_value": float(doc.get("total_value", 0.0)),
                    "entry_time": doc.get("entry_time", datetime.utcnow()),
                    "last_update": doc.get("last_update", datetime.utcnow()),
                    "status": doc.get("status", "open"),
                }

            self.positions = positions
            self.last_sync_time = datetime.utcnow()
            logger.info(f"Loaded {len(positions)} positions from Data Manager")

        except Exception as e:
            logger.error(f"Failed to load positions from Data Manager: {e}")
            raise

    async def _load_daily_pnl_from_data_manager(self) -> None:
        """Load daily P&L from Data Manager"""
        try:
            today = datetime.utcnow().date()
            daily_pnl = await position_client.get_daily_pnl(today.isoformat())
            if daily_pnl is not None:
                self.daily_pnl = float(daily_pnl)
                logger.info(f"Loaded daily P&L from Data Manager: {self.daily_pnl}")
        except Exception as e:
            logger.warning(f"Failed to load daily P&L from Data Manager: {e}")

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

    async def _sync_positions_to_data_manager(self) -> None:
        """Sync current positions to Data Manager with hedge mode support"""
        async with self.sync_lock:
            try:
                # Sync current positions to Data Manager
                for position_key, position in self.positions.items():
                    symbol, position_side = position_key
                    position_data = {
                        "symbol": symbol,
                        "position_side": position_side,
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
                    await position_client.upsert_position(position_data)

                # Update daily P&L in Data Manager
                today = datetime.utcnow().date().isoformat()
                await position_client.update_daily_pnl(today, self.daily_pnl)

                self.last_sync_time = datetime.utcnow()
                logger.debug("Positions synced to Data Manager")

            except Exception as e:
                logger.error(f"Failed to sync positions to Data Manager: {e}")

    async def _periodic_sync(self) -> None:
        """Periodically sync positions to Data Manager"""
        while True:
            try:
                await asyncio.sleep(30)  # Sync every 30 seconds
                await self._sync_positions_to_data_manager()
            except Exception as e:
                logger.error(f"Error in periodic sync: {e}")

    async def update_position(self, order: TradeOrder, result: dict[str, Any]) -> None:
        """Update position after order execution with distributed state management

        CRITICAL: Positions are tracked by (symbol, position_side) tuple to support hedge mode.
        This allows tracking separate LONG and SHORT positions on the same symbol.
        """
        # CRITICAL FIX: Removed sync_lock to prevent blocking
        # MongoDB sync happens asynchronously without blocking position updates
        symbol = order.symbol

        # Determine position side for hedge mode tracking
        # buy = LONG, sell = SHORT
        position_side = order.position_side or (
            "LONG" if order.side == "buy" else "SHORT"
        )
        position_key = (symbol, position_side)

        try:
            if position_key not in self.positions:
                self.positions[position_key] = {
                    "symbol": symbol,
                    "position_side": position_side,
                    "quantity": 0.0,
                    "avg_price": 0.0,
                    "unrealized_pnl": 0.0,
                    "realized_pnl": 0.0,
                    "last_update": datetime.utcnow(),
                    "entry_time": datetime.utcnow(),
                    "total_cost": 0.0,
                    "total_value": 0.0,
                    "accumulation_count": 0,  # NEW: Track accumulations
                }
            position = self.positions[position_key]

            # Ensure all position numeric fields are floats (in case they were loaded as strings)
            position["quantity"] = float(position.get("quantity", 0.0))
            position["avg_price"] = float(position.get("avg_price", 0.0))
            position["unrealized_pnl"] = float(position.get("unrealized_pnl", 0.0))
            position["realized_pnl"] = float(position.get("realized_pnl", 0.0))
            position["total_cost"] = float(position.get("total_cost", 0.0))
            position["total_value"] = float(position.get("total_value", 0.0))

            # Get fill price from result and ensure it's a float
            fill_price = result.get("fill_price", order.target_price or 0)
            if isinstance(fill_price, str):
                try:
                    fill_price = (
                        float(fill_price)
                        if fill_price and fill_price not in ("0", "0.0", "0.00", "")
                        else (order.target_price or 0)
                    )
                except (ValueError, TypeError):
                    fill_price = order.target_price or 0
            fill_price = float(fill_price)

            # Get fill quantity and ensure it's a float
            fill_quantity = result.get("amount", order.amount)
            if isinstance(fill_quantity, str):
                try:
                    fill_quantity = (
                        float(fill_quantity)
                        if fill_quantity
                        and fill_quantity not in ("0", "0.0", "0.00", "")
                        else order.amount
                    )
                except (ValueError, TypeError):
                    fill_quantity = order.amount
            fill_quantity = float(fill_quantity) if fill_quantity else order.amount

            # Hedge mode aware position updates
            # For LONG positions: buy adds, sell reduces
            # For SHORT positions: sell adds, buy reduces
            is_adding_to_position = (
                position_side == "LONG" and order.side == "buy"
            ) or (position_side == "SHORT" and order.side == "sell")

            if is_adding_to_position:
                # Add to position (opening or increasing)
                new_quantity = position["quantity"] + fill_quantity
                if new_quantity > 0:
                    # NEW: Increment accumulation count if adding to existing position
                    if position["quantity"] > 0:  # Was an existing position
                        position["accumulation_count"] = (
                            position.get("accumulation_count", 0) + 1
                        )
                        logger.info(
                            f"Position accumulation #{position['accumulation_count']} for {symbol} {position_side}"
                        )

                    new_avg_price = (
                        position["quantity"] * position["avg_price"]
                        + fill_quantity * fill_price
                    ) / new_quantity
                    position["quantity"] = new_quantity
                    position["avg_price"] = new_avg_price
                    position["total_cost"] += fill_quantity * fill_price
                    position["total_value"] = new_quantity * fill_price

            else:
                # Reduce position (closing or decreasing)
                if position["quantity"] > 0:
                    # Calculate realized P&L
                    # For LONG: profit when price goes up (sell_price > avg_price)
                    # For SHORT: profit when price goes down (avg_price > buy_price)
                    if position_side == "LONG":
                        realized_pnl = (fill_price - position["avg_price"]) * min(
                            fill_quantity, position["quantity"]
                        )
                    else:  # SHORT
                        realized_pnl = (position["avg_price"] - fill_price) * min(
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
                            f"Position closed for {symbol} {position_side}, "
                            f"total realized P&L: {position['realized_pnl']:.2f}"
                        )
                        audit_logger.log_position(position, status="closed")

                        # Emit realized PnL metric
                        total_realized_pnl_usd.labels(
                            exchange=order.exchange,
                        ).set(position["realized_pnl"])

                        # Update daily PnL
                        total_daily_pnl_usd.labels(
                            exchange=order.exchange,
                        ).set(self.daily_pnl)

                        # Reset position size gauge
                        current_position_size.labels(
                            symbol=symbol,
                            position_side=position_side,
                            exchange=order.exchange,
                        ).set(0)

                        logger.info(
                            f"📊 Position metrics updated on close: "
                            f"realized_pnl=${position['realized_pnl']:.2f}, "
                            f"daily_pnl=${self.daily_pnl:.2f}"
                        )

                        await self._close_position_in_data_manager(
                            position_key, position
                        )
                        del self.positions[position_key]
                        return

            position["last_update"] = datetime.utcnow()

            # Calculate unrealized PnL (hedge mode aware)
            # For LONG: profit when current price > avg price
            # For SHORT: profit when current price < avg price
            if position_side == "LONG":
                position["unrealized_pnl"] = (
                    fill_price - position["avg_price"]
                ) * position["quantity"]
            else:  # SHORT
                position["unrealized_pnl"] = (
                    position["avg_price"] - fill_price
                ) * position["quantity"]

            logger.info(
                f"Updated position for {symbol} {position_side}: "
                f"quantity={position['quantity']:.6f}, "
                f"avg_price={position['avg_price']:.2f}"
            )
            audit_logger.log_position(position, status="updated")

            # Emit business metrics for position size and PnL monitoring
            current_position_size.labels(
                symbol=symbol,
                position_side=position_side,
                exchange=order.exchange,
            ).set(position["quantity"])

            total_unrealized_pnl_usd.labels(
                exchange=order.exchange,
            ).set(position["unrealized_pnl"])

            # Update total position value
            total_value = sum(
                pos["total_value"]
                for pos in self.positions.values()
                if pos.get("total_value", 0) > 0
            )
            total_position_value_usd.labels(
                exchange=order.exchange,
            ).set(total_value)

            # Update daily PnL gauge
            total_daily_pnl_usd.labels(
                exchange=order.exchange,
            ).set(self.daily_pnl)

            logger.debug(
                f"📊 Business metrics updated: "
                f"position_size={position['quantity']:.6f}, "
                f"unrealized_pnl=${position['unrealized_pnl']:.2f}, "
                f"total_value=${total_value:.2f}"
            )

            # CRITICAL FIX: Data Manager sync must NOT block risk management orders
            # Use short timeout to prevent hanging - position already updated in memory
            try:
                await asyncio.wait_for(
                    self._sync_positions_to_data_manager(), timeout=2.0
                )
            except asyncio.TimeoutError:
                logger.warning(
                    f"⚠️  Data Manager sync timed out for {symbol} {position_side} (non-critical, continuing)"
                )
            except Exception as data_manager_error:
                logger.warning(
                    f"⚠️  Data Manager sync failed for {symbol} {position_side} (non-critical): {data_manager_error}"
                )

        except Exception as e:
            logger.error(f"Error updating position for {symbol} {position_side}: {e}")
            audit_logger.log_error(
                {"error": str(e)},
                context={"order": order.model_dump(), "result": result},
            )

    async def _close_position_in_data_manager(
        self, position_key: tuple[str, str], position: dict[str, Any]
    ) -> None:
        """Mark position as closed in Data Manager"""
        symbol, position_side = position_key
        try:
            await position_client.close_position(
                symbol,
                position_side,
                {
                    "status": "closed",
                    "last_update": datetime.utcnow(),
                    "closed_at": datetime.utcnow(),
                    "final_realized_pnl": position["realized_pnl"],
                },
            )
            logger.info(
                f"Position {symbol} {position_side} marked as closed in Data Manager"
            )
        except Exception as e:
            logger.error(
                f"Failed to close position {symbol} {position_side} in Data Manager: {e}"
            )

    async def create_position_record(
        self, order: TradeOrder, result: dict[str, Any]
    ) -> None:
        """Create position record on order execution with dual persistence and metrics"""
        try:
            if not order.position_id:
                logger.warning("Order missing position_id, cannot track position")
                return

            # Extract data from order and result
            # Ensure fill_price is a float, not a string
            fill_price = result.get("fill_price", order.target_price or 0)
            if isinstance(fill_price, str):
                try:
                    fill_price = (
                        float(fill_price)
                        if fill_price and fill_price not in ("0", "0.0", "0.00", "")
                        else (order.target_price or 0)
                    )
                except (ValueError, TypeError):
                    fill_price = order.target_price or 0
            fill_price = float(fill_price)

            # Ensure amount is a float
            amount = result.get("amount", order.amount)
            if isinstance(amount, str):
                try:
                    fill_amount = (
                        float(amount)
                        if amount and amount not in ("0", "0.0", "0.00", "")
                        else order.amount
                    )
                except (ValueError, TypeError):
                    fill_amount = order.amount
            else:
                fill_amount = amount if amount and amount > 0 else order.amount
            fill_amount = float(fill_amount)

            # Ensure stop_loss and take_profit are floats if provided
            stop_loss = None
            if order.stop_loss:
                stop_loss = (
                    float(order.stop_loss)
                    if not isinstance(order.stop_loss, str)
                    else float(order.stop_loss)
                )

            take_profit = None
            if order.take_profit:
                take_profit = (
                    float(order.take_profit)
                    if not isinstance(order.take_profit, str)
                    else float(order.take_profit)
                )

            # Ensure commission is a float
            commission = result.get("commission", 0.0)
            if isinstance(commission, str):
                try:
                    commission = float(commission) if commission else 0.0
                except (ValueError, TypeError):
                    commission = 0.0
            commission = float(commission)

            position_data = {
                "position_id": order.position_id,
                "strategy_id": order.strategy_metadata.get("strategy_id", "unknown"),
                "exchange": order.exchange,
                "symbol": order.symbol,
                "position_side": order.position_side or "LONG",
                "entry_price": fill_price,
                "quantity": fill_amount,
                "entry_time": datetime.utcnow(),
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "status": "open",
                "metadata": order.strategy_metadata,
                # Exchange-specific data
                "exchange_position_id": result.get("position_id"),
                "entry_order_id": result.get("order_id", order.order_id),
                "entry_trade_ids": result.get("trade_ids", []),
                "commission_asset": result.get("commission_asset", "USDT"),
                "commission_total": commission,
            }

            # Persist to Data Manager (with timeout to prevent blocking risk management)
            try:
                await asyncio.wait_for(
                    position_client.create_position(position_data), timeout=2.0
                )
                logger.info(
                    f"Position {order.position_id} created via Data Manager for "
                    f"{order.symbol} {order.position_side}"
                )
            except asyncio.TimeoutError:
                logger.warning(
                    f"⚠️  Data Manager position insert timed out for {order.position_id} (non-critical, continuing)"
                )
            except Exception as data_manager_error:
                logger.error(
                    f"Failed to create position via Data Manager: {data_manager_error}"
                )

            # Export metrics (with timeout to prevent blocking)
            try:
                await asyncio.wait_for(
                    self._export_position_opened_metrics(position_data), timeout=1.0
                )
            except asyncio.TimeoutError:
                logger.warning(
                    f"⚠️  Metrics export timed out for {order.position_id} (non-critical)"
                )

        except Exception as e:
            logger.error(f"Error creating position record: {e}")

    async def update_position_risk_orders(
        self,
        position_id: str,
        stop_loss_order_id: str | None = None,
        take_profit_order_id: str | None = None,
    ) -> None:
        """Update position record with stop loss and take profit order IDs"""
        try:
            update_data = {}
            if stop_loss_order_id:
                update_data["stop_loss_order_id"] = stop_loss_order_id
            if take_profit_order_id:
                update_data["take_profit_order_id"] = take_profit_order_id

            if not update_data:
                return

            # Update Data Manager
            try:
                await position_client.update_position_risk_orders(
                    position_id, update_data
                )
                logger.info(
                    f"Updated position {position_id} risk orders via Data Manager: {update_data}"
                )
            except Exception as data_manager_error:
                logger.error(
                    f"Failed to update position risk orders via Data Manager: {data_manager_error}"
                )

        except Exception as e:
            logger.error(f"Error updating position risk orders: {e}")

    async def get_position_data(self, position_id: str) -> dict[str, Any] | None:
        """Get position data by position_id

        Args:
            position_id: The position ID to lookup

        Returns:
            Position data dict or None if not found
        """
        try:
            # Try Data Manager first
            try:
                position = await position_client.get_position(position_id)
                if position:
                    logger.debug(f"Found position {position_id} in Data Manager")
                    return position
            except Exception as data_manager_error:
                logger.warning(
                    f"Failed to get position from Data Manager: {data_manager_error}"
                )

            # Fallback to in-memory positions (search by position_id in metadata)
            for position_key, position in self.positions.items():
                if position.get("position_id") == position_id:
                    logger.debug(f"Found position {position_id} in memory")
                    return position

            logger.warning(f"Position {position_id} not found")
            return None

        except Exception as e:
            logger.error(f"Error getting position data: {e}")
            return None

    async def close_position_record(
        self, position_id: str, exit_result: dict[str, Any]
    ) -> None:
        """Update position record on closure with dual persistence and metrics

        Supports hedge mode with proper PNL calculation for LONG and SHORT positions.
        """
        try:
            # Calculate closure data
            exit_price = exit_result.get("exit_price", 0.0)
            exit_time = exit_result.get("exit_time", datetime.utcnow())
            entry_price = exit_result.get("entry_price", 0.0)
            quantity = exit_result.get("quantity", 0.0)
            entry_time = exit_result.get("entry_time", exit_time)
            position_side = exit_result.get("position_side", "LONG")

            # Calculate PnL (use provided values if already calculated, else calculate)
            if "pnl" in exit_result and "pnl_pct" in exit_result:
                # Already calculated (e.g., from OCO completion)
                pnl = exit_result["pnl"]
                pnl_pct = exit_result["pnl_pct"]
                pnl_after_fees = exit_result.get("pnl_after_fees")
            else:
                # Calculate based on position side (hedge-mode aware)
                if position_side == "LONG":
                    pnl = (exit_price - entry_price) * quantity
                else:  # SHORT
                    pnl = (entry_price - exit_price) * quantity

                pnl_pct = (
                    (pnl / (entry_price * quantity) * 100)
                    if entry_price > 0 and quantity > 0
                    else 0.0
                )

                # Get commissions
                entry_commission = exit_result.get("entry_commission", 0.0)
                exit_commission = exit_result.get("exit_commission", 0.0)
                total_commission = entry_commission + exit_commission
                pnl_after_fees = pnl - total_commission

            # Calculate duration
            duration_seconds = int((exit_time - entry_time).total_seconds())

            # Get commissions (handle both cases)
            entry_commission = exit_result.get("entry_commission", 0.0)
            exit_commission = exit_result.get("exit_commission", 0.0)

            update_data = {
                "status": "closed",
                "exit_price": exit_price,
                "exit_time": exit_time,
                "exit_order_id": exit_result.get("order_id"),
                "exit_trade_ids": exit_result.get("trade_ids", []),
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "pnl_after_fees": pnl_after_fees,
                "duration_seconds": duration_seconds,
                "close_reason": exit_result.get("close_reason", "manual"),
                "final_commission": exit_commission,
            }

            # Update Data Manager
            try:
                await position_client.update_position(position_id, update_data)
                logger.info(f"Position {position_id} closed via Data Manager")
            except Exception as data_manager_error:
                logger.error(
                    f"Failed to close position via Data Manager: {data_manager_error}"
                )

            # Export metrics
            position_data = {**exit_result, **update_data}
            await self._export_position_closed_metrics(position_data)

        except Exception as e:
            logger.error(f"Error closing position record: {e}")

    async def _export_position_opened_metrics(
        self, position_data: dict[str, Any]
    ) -> None:
        """Export metrics when position is opened"""
        try:
            strategy_id = position_data.get("strategy_id", "unknown")
            symbol = position_data.get("symbol", "unknown")
            position_side = position_data.get("position_side", "LONG")
            exchange = position_data.get("exchange", "binance")
            entry_price = position_data.get("entry_price", 0.0)

            # Increment position opened counter
            positions_opened_total.labels(
                strategy_id=strategy_id,
                symbol=symbol,
                position_side=position_side,
                exchange=exchange,
            ).inc()

            # Record entry price
            position_entry_price.labels(
                symbol=symbol, position_side=position_side, exchange=exchange
            ).observe(entry_price)

            logger.debug(
                f"Position opened metrics exported for {position_data.get('position_id')}"
            )

        except Exception as e:
            logger.error(f"Error exporting position opened metrics: {e}")

    async def _export_position_closed_metrics(
        self, position_data: dict[str, Any]
    ) -> None:
        """Export metrics when position is closed"""
        try:
            strategy_id = position_data.get("strategy_id", "unknown")
            symbol = position_data.get("symbol", "unknown")
            position_side = position_data.get("position_side", "LONG")
            exchange = position_data.get("exchange", "binance")
            close_reason = position_data.get("close_reason", "manual")
            pnl_after_fees = position_data.get("pnl_after_fees", 0.0)
            pnl_pct = position_data.get("pnl_pct", 0.0)
            duration_seconds = position_data.get("duration_seconds", 0)
            exit_price = position_data.get("exit_price", 0.0)
            entry_commission = position_data.get("commission_total", 0.0)
            final_commission = position_data.get("final_commission", 0.0)
            total_commission = entry_commission + final_commission

            # Increment position closed counter
            positions_closed_total.labels(
                strategy_id=strategy_id,
                symbol=symbol,
                position_side=position_side,
                close_reason=close_reason,
                exchange=exchange,
            ).inc()

            # Record PnL in USD
            position_pnl_usd.labels(
                strategy_id=strategy_id,
                symbol=symbol,
                position_side=position_side,
                exchange=exchange,
            ).observe(pnl_after_fees)

            # Record PnL percentage
            position_pnl_percentage.labels(
                strategy_id=strategy_id,
                symbol=symbol,
                position_side=position_side,
                exchange=exchange,
            ).observe(pnl_pct)

            # Record duration
            position_duration_seconds.labels(
                strategy_id=strategy_id,
                symbol=symbol,
                position_side=position_side,
                close_reason=close_reason,
                exchange=exchange,
            ).observe(duration_seconds)

            # Record exit price
            position_exit_price.labels(
                symbol=symbol, position_side=position_side, exchange=exchange
            ).observe(exit_price)

            # Record commission
            position_commission_usd.labels(
                strategy_id=strategy_id, symbol=symbol, exchange=exchange
            ).observe(total_commission)

            # Track win/loss
            if pnl_after_fees > 0:
                positions_winning_total.labels(
                    strategy_id=strategy_id,
                    symbol=symbol,
                    position_side=position_side,
                    exchange=exchange,
                ).inc()
            else:
                positions_losing_total.labels(
                    strategy_id=strategy_id,
                    symbol=symbol,
                    position_side=position_side,
                    exchange=exchange,
                ).inc()

            # Calculate and record ROI
            entry_price = position_data.get("entry_price", 0.0)
            if entry_price > 0:
                roi = (exit_price - entry_price) / entry_price
                position_roi.labels(
                    strategy_id=strategy_id,
                    symbol=symbol,
                    position_side=position_side,
                    exchange=exchange,
                ).observe(roi)

            logger.debug(
                f"Position closed metrics exported for {position_data.get('position_id')}: "
                f"PnL=${pnl_after_fees:.2f}, Duration={duration_seconds}s"
            )

        except Exception as e:
            logger.error(f"Error exporting position closed metrics: {e}")

    async def get_position_size_limit(self, symbol: str) -> float:
        """Get position size limit for symbol (checks config, then default)"""
        try:
            # Check if there's a symbol-specific config
            from tradeengine.config_manager import config_manager

            symbol_config = await config_manager.get_config(symbol=symbol)
            if symbol_config and symbol_config.parameters.get("max_position_size"):
                return float(symbol_config.parameters["max_position_size"])

            # Check global config
            global_config = await config_manager.get_config()
            if global_config and global_config.parameters.get("max_position_size"):
                return float(global_config.parameters["max_position_size"])

        except Exception as e:
            logger.warning(f"Failed to get config limit for {symbol}: {e}")

        # Fall back to environment variable default
        from shared.constants import MAX_POSITION_SIZE

        return MAX_POSITION_SIZE

    async def check_position_limits(self, order: TradeOrder) -> bool:
        """Check if order meets position size limits with distributed state"""
        if not RISK_MANAGEMENT_ENABLED:
            return True

        # Refresh positions from Data Manager to ensure consistency
        await self._refresh_positions_from_data_manager()

        # NEW: Check absolute position size limit (from config or default)
        position_side = "LONG" if order.side == "buy" else "SHORT"
        position_key = (order.symbol, position_side)

        if position_key in self.positions:
            current_quantity = self.positions[position_key]["quantity"]
            new_quantity = current_quantity + order.amount

            # Get limit from config (symbol-specific or global)
            max_position_size = await self.get_position_size_limit(order.symbol)

            if new_quantity > max_position_size:
                logger.warning(
                    f"Position size would exceed limit: {order.symbol} {position_side} "
                    f"current={current_quantity}, new={new_quantity}, max={max_position_size}"
                )
                return False

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

    async def _refresh_positions_from_data_manager(self) -> None:
        """Refresh positions from Data Manager to ensure consistency across pods"""
        try:
            # Get all open positions from Data Manager
            positions_data = await position_client.get_open_positions()
            refreshed_positions = {}

            for doc in positions_data:
                symbol = doc.get("symbol")
                if not symbol:
                    logger.warning("Skipping Data Manager document without symbol")
                    continue

                # Get position_side, default to LONG for backward compatibility
                position_side = doc.get("position_side", "LONG")
                position_key = (symbol, position_side)

                refreshed_positions[position_key] = {
                    "symbol": symbol,
                    "position_side": position_side,
                    "quantity": float(doc.get("quantity", 0.0)),
                    "avg_price": float(doc.get("avg_price", 0.0)),
                    "unrealized_pnl": float(doc.get("unrealized_pnl", 0.0)),
                    "realized_pnl": float(doc.get("realized_pnl", 0.0)),
                    "total_cost": float(doc.get("total_cost", 0.0)),
                    "total_value": float(doc.get("total_value", 0.0)),
                    "entry_time": doc.get("entry_time", datetime.utcnow()),
                    "last_update": doc.get("last_update", datetime.utcnow()),
                    "status": doc.get("status", "open"),
                }

            # Only update if positions have changed
            if refreshed_positions != self.positions:
                logger.info("Refreshing positions from Data Manager for consistency")
                self.positions = refreshed_positions

        except Exception as e:
            logger.error(f"Failed to refresh positions from Data Manager: {e}")

    async def check_daily_loss_limits(self) -> bool:
        """Check daily loss limits with distributed state"""
        if not RISK_MANAGEMENT_ENABLED:
            return True

        # Refresh daily P&L from Data Manager
        await self._refresh_daily_pnl_from_data_manager()

        max_daily_loss = self.total_portfolio_value * self.max_daily_loss_pct

        if self.daily_pnl < -max_daily_loss:
            logger.warning(
                f"Daily loss {self.daily_pnl:.2f} exceeds limit {-max_daily_loss:.2f}"
            )
            return False

        return True

    async def _refresh_daily_pnl_from_data_manager(self) -> None:
        """Refresh daily P&L from Data Manager"""
        try:
            today = datetime.utcnow().date().isoformat()
            daily_pnl = await position_client.get_daily_pnl(today)
            if daily_pnl is not None:
                self.daily_pnl = float(daily_pnl)
        except Exception as e:
            logger.warning(f"Failed to refresh daily P&L from Data Manager: {e}")

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

    def get_positions(self) -> dict[tuple[str, str], dict[str, Any]]:
        """Get all current positions

        Returns:
            Dict mapping (symbol, position_side) tuples to position data
        """
        return self.positions.copy()

    def get_position(
        self, symbol: str, position_side: str | None = None
    ) -> dict[str, Any] | None:
        """Get specific position

        Args:
            symbol: Trading symbol (e.g., BTCUSDT)
            position_side: Position side (LONG or SHORT). If None, returns first found position.

        Returns:
            Position data dict or None if not found
        """
        if position_side:
            # Get specific position by symbol and side
            position_key = (symbol, position_side)
            return self.positions.get(position_key)
        else:
            # Get first position for symbol (backward compatibility)
            for (pos_symbol, pos_side), position in self.positions.items():
                if pos_symbol == symbol:
                    return position
            return None

    def get_positions_by_symbol(self, symbol: str) -> list[dict[str, Any]]:
        """Get all positions for a symbol (useful in hedge mode)

        Args:
            symbol: Trading symbol (e.g., BTCUSDT)

        Returns:
            List of position dicts for the symbol (may include both LONG and SHORT)
        """
        return [
            position
            for (pos_symbol, pos_side), position in self.positions.items()
            if pos_symbol == symbol
        ]

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
            "last_sync_time": (
                self.last_sync_time.isoformat() if self.last_sync_time else None
            ),
            "mongodb_connected": self.mongodb_db is not None,
        }

    async def reset_daily_pnl(self) -> None:
        """Reset daily P&L (call at start of new day)"""
        self.daily_pnl = 0.0
        logger.info("Daily P&L reset")

        # Sync to Data Manager
        try:
            today = datetime.utcnow().date().isoformat()
            await position_client.update_daily_pnl(today, 0.0)
        except Exception as e:
            logger.error(f"Failed to reset daily P&L in Data Manager: {e}")

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
            "last_sync": (
                self.last_sync_time.isoformat() if self.last_sync_time else None
            ),
            "mongodb_connected": self.mongodb_db is not None,
            "mongodb_uri": (
                self.settings.mongodb_uri
                if self.settings.mongodb_uri
                else get_mongodb_connection_string()
            ),
        }


# Global position manager instance
position_manager = PositionManager()
