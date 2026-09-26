"""
Position Manager - Tracks positions and enforces risk limits with distributed state
management using Data Manager API and MongoDB for coordination only.
"""

import asyncio
import logging
from datetime import date, datetime
from types import SimpleNamespace
from typing import Any

from contracts.order import TradeOrder
from shared.audit import audit_logger
from shared.config import Settings
from shared.constants import (
    MAX_DAILY_LOSS_PCT,
    MAX_PORTFOLIO_EXPOSURE_PCT,
    MAX_POSITION_SIZE_PCT,
    RISK_MANAGEMENT_ENABLED,
    TE_EXCHANGE_TRUTH_STORE_ENABLED,
    UTC,
    get_mongodb_connection_string,
    redact_uri,
)
from shared.trading_store_client import trading_store
from tradeengine.exchange_truth_store import ExchangeTruthStore
from tradeengine.metrics import (
    algo_orders_open,
    current_position_size,
    daily_pnl_persist_failures_consecutive,
    exchange_truth_shadow_delta_total,
    otel_algo_orders_open,
    position_close_persist_failures_total,
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
from tradeengine.services.persist_retry_queue import PendingWrite, persist_retry_queue

logger = logging.getLogger(__name__)
position_client = trading_store


class PositionManager:
    """Manages trading positions and risk limits with distributed state management
    using Data Manager API for persistence and MongoDB for coordination only."""

    def __init__(self, exchange: Any = None) -> None:
        self.positions: dict[tuple[str, str], dict[str, Any]] = {}
        self.position_records: dict[str, dict[str, Any]] = {}
        self.daily_pnl: float = 0.0
        self._daily_pnl_date: date = datetime.now(UTC).date()
        self._daily_pnl_rollover_lock = asyncio.Lock()
        self._daily_pnl_refresh_stale: bool = True
        self.max_position_size_pct: float = MAX_POSITION_SIZE_PCT
        self.max_daily_loss_pct: float = MAX_DAILY_LOSS_PCT
        self.max_portfolio_exposure_pct: float = MAX_PORTFOLIO_EXPOSURE_PCT
        self.total_portfolio_value: float = 0.0  # Initialized from exchange
        self.last_sync_time: datetime | None = None
        self.sync_lock = asyncio.Lock()
        self.settings = Settings()
        self.mongodb_client: Any = None
        self.mongodb_db: Any = None
        self.exchange = exchange
        self.portfolio_value_last_update: datetime | None = None
        self.portfolio_value_lock = asyncio.Lock()
        self.rejection_reason: str | None = (
            None  # Set by check_position_limits on rejection
        )
        self._portfolio_exposure_refresh_failed = False
        self._recorded_exit_order_ids: set[str] = set()
        # AC2 (#459 — 446-C): injected by Dispatcher.initialize() after
        # UserDataStreamConsumer starts; None until then.
        self.exchange_truth_store: ExchangeTruthStore | None = None

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
                await self._load_positions_from_store()

                # Load daily P&L from Data Manager
                await self._load_daily_pnl_from_store()

                # Fetch initial portfolio value from exchange
                await self._refresh_portfolio_value()

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

    async def _refresh_portfolio_value(self) -> bool:
        """Fetch real-time availableBalance from Binance exchange.

        Primary: availableBalance (Wallet Balance - Initial Margin - Open Order Margin).
        Fallback: totalWalletBalance when availableBalance is absent (e.g. all margin
        committed to open positions — the account is funded, just fully allocated).
        Implements a 5s cache duration.
        Returns True if update succeeded, False otherwise.
        """
        if not self.exchange:
            logger.warning("No exchange client configured for portfolio value refresh")
            return False

        async with self.portfolio_value_lock:
            now = datetime.now(UTC)
            # Check cache (5s duration as per ticket AC)
            if (
                self.portfolio_value_last_update
                and (now - self.portfolio_value_last_update).total_seconds() < 5
            ):
                return True

            try:
                account_info = await self.exchange.get_account_info()
                available_balance = account_info.get("available_balance")

                if available_balance is not None:
                    self.total_portfolio_value = float(available_balance)
                    self.portfolio_value_last_update = now
                    logger.info(
                        f"Dynamic portfolio value updated: ${self.total_portfolio_value:,.2f} (available balance)"
                    )
                    return True

                # availableBalance absent from Binance response — fall back to
                # totalWalletBalance so a fully-margined account isn't treated as
                # empty and every order rejected. (#404)
                total_wallet = account_info.get("total_wallet_balance")
                if total_wallet is not None:
                    self.total_portfolio_value = float(total_wallet)
                    self.portfolio_value_last_update = now
                    logger.warning(
                        "available_balance absent from Binance account info — "
                        f"using total_wallet_balance=${self.total_portfolio_value:,.2f} as fallback. "
                        f"Keys present: {sorted(account_info.keys())}"
                    )
                    return True

                logger.error(
                    "Failed to extract available_balance or total_wallet_balance from "
                    f"Binance account info. Keys present: {sorted(account_info.keys())}"
                )
                return False
            except Exception as e:
                logger.error(f"Error fetching portfolio value from Binance: {e}")
                return False

    async def close(self) -> None:
        """Close position manager and sync final state"""
        try:
            await self._sync_positions_to_store()
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

    async def _load_positions_from_store(self) -> None:
        """Load positions from Data Manager with hedge mode support"""
        try:
            positions_data = await trading_store.get_open_positions()
            positions = {}
            position_records = {}

            for doc in positions_data:
                symbol = doc.get("symbol")
                if not symbol:
                    continue
                position_side = doc.get("position_side", "LONG")
                position_key = (symbol, position_side)

                position = {
                    "symbol": symbol,
                    "position_side": position_side,
                    "quantity": float(doc.get("quantity", 0.0)),
                    "avg_price": float(
                        doc.get("avg_price", doc.get("entry_price", 0.0))
                    ),
                    "unrealized_pnl": float(doc.get("unrealized_pnl", 0.0)),
                    "realized_pnl": float(doc.get("realized_pnl", 0.0)),
                    "total_cost": float(doc.get("total_cost", 0.0)),
                    "total_value": float(doc.get("total_value", 0.0)),
                    "entry_time": doc.get("entry_time", datetime.now(UTC)),
                    "last_update": doc.get("last_update", datetime.now(UTC)),
                    "status": doc.get("status", "open"),
                }
                if doc.get("position_id"):
                    record = dict(doc)
                    record.setdefault("avg_price", record.get("entry_price", 0.0))
                    record.setdefault("last_update", record.get("entry_time"))
                    record.setdefault("status", "open")
                    position_records[str(doc["position_id"])] = record
                positions[position_key] = position

            self.position_records = position_records
            if TE_EXCHANGE_TRUTH_STORE_ENABLED == "on":
                self.positions = {}
                self._refresh_positions_from_exchange_truth_store()
            else:
                self.positions = positions
            self.last_sync_time = datetime.now(UTC)
            logger.info(
                "Loaded %s position records from Data Manager; risk view has %s positions",
                len(position_records),
                len(self.positions),
            )

        except Exception as e:
            logger.error(f"Failed to load positions from trading store: {e}")
            raise

    async def _load_positions_from_data_manager(self) -> None:
        """Backward-compatible name for callers outside the manager."""
        await self._load_positions_from_store()

    async def _load_daily_pnl_from_store(self) -> None:
        """Load daily P&L and require a persisted value before trading."""
        try:
            today = datetime.now(UTC).date()
            self._daily_pnl_date = today
            daily_pnl = await trading_store.get_daily_pnl(today.isoformat())
            if daily_pnl is not None:
                self.daily_pnl = float(daily_pnl)
                self._daily_pnl_refresh_stale = False
                logger.info(
                    "Loaded daily P&L from MongoDB trading store: %s", self.daily_pnl
                )
            else:
                self._daily_pnl_refresh_stale = True
                logger.critical(
                    "No persisted daily P&L exists for today; trading remains "
                    "blocked until the daily-loss baseline is persisted."
                )
        except Exception as e:
            self._daily_pnl_refresh_stale = True
            logger.warning(f"Failed to load daily P&L from Data Manager: {e}")

    async def _roll_daily_pnl_if_new_day(self, *, force: bool = False) -> bool:
        """Close the previous UTC day and establish a zero baseline for today.

        The dedicated lock serializes rollover decisions without taking
        ``sync_lock``. The periodic sync can therefore call this while the
        position update path is active without creating a lock cycle.
        """
        today = datetime.now(UTC).date()
        async with self._daily_pnl_rollover_lock:
            if self._daily_pnl_date == today and not force:
                return False

            previous_date = self._daily_pnl_date
            previous_pnl = self.daily_pnl
            previous_write_ok = True
            current_write_ok = True

            if previous_date != today and previous_date is not None:
                try:
                    result = await trading_store.update_daily_pnl(
                        previous_date.isoformat(), previous_pnl
                    )
                    previous_write_ok = getattr(result, "ok", True) is not False
                except Exception as exc:
                    previous_write_ok = False
                    logger.error(
                        "Failed to persist closing daily P&L for %s: %s",
                        previous_date,
                        exc,
                    )

            self.daily_pnl = 0.0
            self._daily_pnl_date = today

            try:
                result = await trading_store.update_daily_pnl(today.isoformat(), 0.0)
                current_write_ok = getattr(result, "ok", True) is not False
            except Exception as exc:
                current_write_ok = False
                logger.error(
                    "Failed to persist opening daily P&L baseline for %s: %s",
                    today,
                    exc,
                )

            self._daily_pnl_refresh_stale = not (previous_write_ok and current_write_ok)
            logger.info(
                "Daily P&L rolled from %s to %s; previous=%s, baseline=0.0",
                previous_date,
                today,
                previous_pnl,
            )
            return True

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

    async def _sync_positions_to_store(self) -> None:
        """Sync mutable position fields to the MongoDB-backed trading store."""
        async with self.sync_lock:
            try:
                rolled_over = await self._roll_daily_pnl_if_new_day()
                records = list(getattr(self, "position_records", {}).values())
                if not records:
                    records = [
                        position
                        for position in self.positions.values()
                        if position.get("position_id")
                    ]

                for record in records:
                    position_id = record.get("position_id")
                    if not position_id:
                        logger.warning("Skipping position sync without position_id")
                        continue
                    position_data = dict(record)
                    position_data.setdefault(
                        "avg_price", position_data.get("entry_price", 0.0)
                    )
                    position_data.setdefault(
                        "last_update",
                        position_data.get("entry_time", datetime.now(UTC)),
                    )
                    position_data["updated_at"] = datetime.now(UTC)
                    current = await trading_store.get_position(str(position_id))
                    if not current or current.get("status") != "open":
                        self.position_records.pop(str(position_id), None)
                        continue
                    mutable = {
                        key: position_data[key]
                        for key in (
                            "quantity",
                            "avg_price",
                            "unrealized_pnl",
                            "last_update",
                            "updated_at",
                        )
                        if key in position_data
                    }
                    await trading_store.update_position(str(position_id), mutable)

                # The rollover helper already persisted the new-day zero.
                if not rolled_over:
                    today = datetime.now(UTC).date().isoformat()
                    persist_result = await trading_store.update_daily_pnl(
                        today, self.daily_pnl
                    )
                    if persist_result.ok:
                        daily_pnl_persist_failures_consecutive.set(0)
                    else:
                        daily_pnl_persist_failures_consecutive.inc()
                        logger.error(
                            "Daily P&L persistence failed for %s (store=mongodb): %s",
                            today,
                            persist_result.error,
                        )

                self.last_sync_time = datetime.now(UTC)
                logger.debug("Positions synced to trading store")

            except Exception as e:
                logger.error(f"Failed to sync positions to trading store: {e}")

    async def _sync_positions_to_data_manager(self) -> None:
        """Backward-compatible name for the store sync loop."""
        await self._sync_positions_to_store()

    def _refresh_positions_from_exchange_truth_store(self) -> bool:
        """Refresh the risk view from the exchange-authoritative position store."""
        store = self.exchange_truth_store
        if store is None or not getattr(store, "is_ready", False):
            return False

        snapshots = store.get_positions()
        self.positions = {
            key: {
                "symbol": snapshot.symbol,
                "position_side": snapshot.side,
                "quantity": snapshot.quantity,
                "avg_price": snapshot.entry_price,
                "unrealized_pnl": snapshot.unrealized_pnl,
                "realized_pnl": 0.0,
                "total_cost": 0.0,
                "total_value": snapshot.quantity * snapshot.entry_price,
                "entry_time": snapshot.updated_at,
                "last_update": snapshot.updated_at,
                "status": "open",
            }
            for key, snapshot in snapshots.items()
        }
        return True

    async def _periodic_sync(self) -> None:
        """Periodically sync positions to Data Manager"""
        while True:
            try:
                await asyncio.sleep(30)  # Sync every 30 seconds
                await self._sync_positions_to_store()
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
                    "last_update": datetime.now(UTC),
                    "entry_time": datetime.now(UTC),
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

            # Get fill price from result and ensure it's a float.
            # #548: a MARKET order can return status=NEW with fill_price
            # explicitly None (Binance hasn't reported the fill yet) — the
            # key IS present, so dict.get()'s default is never applied.
            # Treat None the same as "missing" before the string/float coercion.
            fill_price = result.get("fill_price")
            if fill_price is None:
                fill_price = order.target_price or 0
            elif isinstance(fill_price, str):
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
            durable_close_recorded = False

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
                    if order.position_id and order.position_id in self.position_records:
                        durable_close_recorded = (
                            await self.record_position_close(
                                position_id=order.position_id,
                                exit_price=fill_price,
                                exit_qty=fill_quantity,
                                exit_order_id=str(result.get("order_id", "")) or None,
                                exit_time=datetime.now(UTC),
                                close_reason="reduce_only"
                                if order.reduce_only
                                else "signal_reduce",
                                commission=float(result.get("commission", 0.0) or 0.0),
                            )
                            is not None
                        )
                    if not durable_close_recorded:
                        await self._roll_daily_pnl_if_new_day()
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

                        if not durable_close_recorded:
                            await self._close_position_in_data_manager(
                                position_key, position
                            )
                        del self.positions[position_key]
                        return

            position["last_update"] = datetime.now(UTC)

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
                await asyncio.wait_for(self._sync_positions_to_store(), timeout=2.0)
            except TimeoutError:
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
            await trading_store.close_position(
                symbol,
                position_side,
                {
                    "status": "closed",
                    "exit_price": position.get(
                        "last_price", position.get("avg_price", 0.0)
                    ),
                    "exit_time": datetime.now(UTC),
                    "pnl": position["realized_pnl"],
                    "pnl_pct": 0.0,
                    "pnl_after_fees": position["realized_pnl"],
                    "duration_seconds": 0,
                    "close_reason": "signal_reduce",
                    "final_commission": 0.0,
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
            # Ensure fill_price is a float, not a string.
            # #548: same None-vs-missing gap as update_position() — a NEW
            # (unfilled) MARKET order returns fill_price=None explicitly, so
            # dict.get()'s default does not apply; guard for None first.
            fill_price = result.get("fill_price")
            if fill_price is None:
                fill_price = order.target_price or 0
            elif isinstance(fill_price, str):
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
                "entry_time": datetime.now(UTC),
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
            if not hasattr(self, "position_records"):
                self.position_records = {}
            self.position_records[order.position_id] = dict(position_data)

            # AC-1 (#352): durable write with retry — a silent timeout drop here causes
            # MySQL positions table to be empty, which triggers every subsequent signal to
            # open a new entry + OCO pair, accumulating orders until Binance limit.
            _create_ok = False
            for _attempt in range(1, 4):
                try:
                    result = await asyncio.wait_for(
                        trading_store.create_position(position_data), timeout=5.0
                    )
                    if result.ok:
                        logger.info(
                            "Position %s created via trading store for %s %s (attempt %s)",
                            order.position_id,
                            order.symbol,
                            order.position_side,
                            _attempt,
                        )
                        _create_ok = True
                        break
                    logger.warning(
                        "Trading-store position insert failed for %s (attempt %s/3): %s",
                        order.position_id,
                        _attempt,
                        result.error,
                    )
                except TimeoutError:
                    logger.warning(
                        f"⚠️  Data Manager position insert timed out for {order.position_id} "
                        f"(attempt {_attempt}/3)"
                    )
                except Exception as data_manager_error:
                    logger.warning(
                        f"⚠️  Data Manager position insert failed for {order.position_id} "
                        f"(attempt {_attempt}/3): {data_manager_error}"
                    )
            if not _create_ok:
                persist_retry_queue.enqueue(
                    PendingWrite(
                        operation="create_position",
                        data=dict(position_data),
                        symbol=order.symbol,
                        position_id=order.position_id,
                        last_error="trading-store create failed",
                    )
                )
                logger.critical(
                    f"❌ CRITICAL: Position {order.position_id} ({order.symbol} {order.position_side}) "
                    f"could NOT be persisted after 3 attempts. "
                    f"Manual reconciliation required — check Data Manager health."
                )

            # Export metrics (with timeout to prevent blocking)
            try:
                await asyncio.wait_for(
                    self._export_position_opened_metrics(position_data), timeout=1.0
                )
            except TimeoutError:
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
                await trading_store.update_position_risk_orders(
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
                position = await trading_store.get_position(position_id)
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
        """Compatibility wrapper for the fill-aware close routine."""
        if position_id not in self.position_records:
            # Older callers provide the complete position snapshot inline. Keep
            # those callers working while routing all persistence through the
            # position-id keyed implementation.
            self.position_records[position_id] = {
                "position_id": position_id,
                "strategy_id": exit_result.get("strategy_id", "unknown"),
                "exchange": exit_result.get("exchange", "binance"),
                "symbol": exit_result.get("symbol", ""),
                "position_side": exit_result.get("position_side", "LONG"),
                "entry_price": exit_result.get("entry_price", 0.0),
                "quantity": exit_result.get("quantity", 0.0),
                "entry_time": exit_result.get("entry_time", datetime.now(UTC)),
                "commission_total": exit_result.get("entry_commission", 0.0),
                "status": "open",
            }
        await self.record_position_close(
            position_id=position_id,
            exit_price=exit_result.get("exit_price", 0.0),
            exit_qty=exit_result.get("quantity", 0.0),
            exit_order_id=exit_result.get("order_id")
            or exit_result.get("exit_order_id"),
            exit_time=exit_result.get("exit_time", datetime.now(UTC)),
            close_reason=exit_result.get(
                "close_reason", exit_result.get("reason", "manual")
            ),
            commission=exit_result.get(
                "exit_commission", exit_result.get("commission", 0.0)
            ),
        )

    async def record_position_close(
        self,
        position_id: str,
        exit_price: float,
        exit_qty: float,
        exit_order_id: str | None,
        exit_time: datetime | None,
        close_reason: str,
        commission: float = 0.0,
    ) -> dict[str, Any] | None:
        """Record a full or partial exchange fill against a position row.

        The operation is keyed by ``position_id`` and is idempotent on the
        exchange fill order id. Persistence failures remain best-effort: the
        durable update is placed on the existing retry queue and never blocks
        the order path.
        """
        try:
            if exit_order_id and str(exit_order_id) in self._recorded_exit_order_ids:
                return None
            record = self.position_records.get(position_id)
            if record is None:
                record = await trading_store.get_position(position_id)
            if not record:
                logger.error("Position %s not found for close fill", position_id)
                return None

            entry_price = float(
                record.get("entry_price", record.get("avg_price", 0.0)) or 0.0
            )
            position_side = str(record.get("position_side", "LONG"))
            current_qty = float(
                record.get("quantity", record.get("entry_quantity", 0.0)) or 0.0
            )
            close_qty = min(max(float(exit_qty or 0.0), 0.0), current_qty)
            if close_qty <= 0.0:
                return None
            exit_price = float(exit_price or 0.0)
            commission = float(commission or 0.0)
            gross_pnl = (
                (exit_price - entry_price) * close_qty
                if position_side == "LONG"
                else (entry_price - exit_price) * close_qty
            )
            previous_pnl = float(
                record.get("pnl", record.get("realized_pnl", 0.0)) or 0.0
            )
            previous_commission = float(record.get("final_commission", 0.0) or 0.0)
            cumulative_pnl = previous_pnl + gross_pnl
            cumulative_commission = previous_commission + commission
            remaining_qty = max(current_qty - close_qty, 0.0)
            status = "closed" if remaining_qty <= 1e-12 else "open"
            raw_entry_time = record.get("entry_time")
            entry_time = (
                raw_entry_time
                if isinstance(raw_entry_time, datetime)
                else (exit_time or datetime.now(UTC))
            )
            effective_exit_time = exit_time or datetime.now(UTC)
            if isinstance(entry_time, str):
                entry_time = datetime.fromisoformat(
                    str(entry_time).replace("Z", "+00:00")
                )
            if isinstance(effective_exit_time, str):
                effective_exit_time = datetime.fromisoformat(
                    str(effective_exit_time).replace("Z", "+00:00")
                )
            duration_seconds = max(
                int((effective_exit_time - entry_time).total_seconds()), 0
            )
            original_qty = float(
                record.get(
                    "entry_quantity", record.get("original_quantity", current_qty)
                )
                or current_qty
            )
            pnl_pct = (
                cumulative_pnl / (entry_price * original_qty) * 100
                if entry_price > 0 and original_qty > 0
                else 0.0
            )
            update_data = {
                "status": status,
                "quantity": remaining_qty,
                "exit_price": exit_price,
                "exit_time": effective_exit_time,
                "exit_order_id": exit_order_id,
                "pnl": cumulative_pnl,
                "pnl_pct": pnl_pct,
                "pnl_after_fees": cumulative_pnl
                - float(record.get("commission_total", 0.0) or 0.0)
                - cumulative_commission,
                "duration_seconds": duration_seconds,
                "close_reason": close_reason,
                "final_commission": cumulative_commission,
            }

            record.update(update_data)
            self.position_records[position_id] = record
            if exit_order_id:
                self._recorded_exit_order_ids.add(str(exit_order_id))

            try:
                result = await trading_store.update_position(position_id, update_data)
            except Exception as persist_error:
                result = SimpleNamespace(ok=False, error=str(persist_error))
            if getattr(result, "ok", True) is False:
                self._queue_close_retry(position_id, record, update_data, result)

            await self._roll_daily_pnl_if_new_day()
            self.daily_pnl += gross_pnl
            await trading_store.update_daily_pnl(
                datetime.now(UTC).date().isoformat(), self.daily_pnl
            )
            total_daily_pnl_usd.labels(exchange=record.get("exchange", "binance")).set(
                self.daily_pnl
            )

            position_data = {**record, **update_data, "gross_pnl": gross_pnl}
            await self._export_position_closed_metrics(position_data)
            if status == "closed":
                positions_closed_total.labels(
                    strategy_id=record.get("strategy_id", "unknown"),
                    symbol=record.get("symbol", "unknown"),
                    position_side=position_side,
                    close_reason=close_reason,
                    exchange=record.get("exchange", "binance"),
                ).inc()
            return position_data
        except Exception as e:
            logger.error("Error recording position close %s: %s", position_id, e)
            return None

    def _queue_close_retry(
        self,
        position_id: str,
        record: dict[str, Any],
        update_data: dict[str, Any],
        result: Any,
    ) -> None:
        """Queue a failed close update without raising into order flow."""
        symbol = str(record.get("symbol", "unknown"))
        side = str(record.get("position_side", "unknown"))
        position_close_persist_failures_total.labels(
            symbol=symbol, position_side=side
        ).inc()
        logger.error(
            "Position close persistence failed for %s: %s",
            position_id,
            getattr(result, "error", result),
        )
        try:
            data = dict(update_data)
            data["_retry_position_id"] = position_id
            persist_retry_queue.enqueue(
                PendingWrite(
                    operation="update_position",
                    data=data,
                    symbol=symbol,
                    position_id=position_id,
                    last_error=str(getattr(result, "error", "") or ""),
                )
            )
        except Exception as exc:
            logger.error(
                "Failed to enqueue close persistence retry for %s: %s", position_id, exc
            )

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

    async def _get_allowed_symbols(self) -> list[str]:
        """Return allowed_symbols from live config, falling back to DEFAULT_TRADING_PARAMETERS.

        Empty list means all symbols are permitted (safe default).
        """
        try:
            from tradeengine.api_filter_routes import get_config_manager

            mgr = get_config_manager()
            global_config = await mgr.get_config()
            if global_config and "allowed_symbols" in global_config:
                value = global_config["allowed_symbols"]
                if isinstance(value, list):
                    return value
        except Exception as e:
            logger.warning("Failed to get allowed_symbols from config: %s", e)

        from tradeengine.defaults import DEFAULT_TRADING_PARAMETERS

        return DEFAULT_TRADING_PARAMETERS.get("allowed_symbols", [])

    async def get_position_size_limit(self, symbol: str) -> float:
        """Get position size limit for symbol (checks config, then default)"""
        try:
            # Use the live config manager (started with MongoDB in api.py lifespan).
            # Importing the cold singleton from config_manager.py would always return
            # hardcoded defaults because it has no MongoDB client.
            from tradeengine.api_filter_routes import get_config_manager

            mgr = get_config_manager()
            symbol_config = await mgr.get_config(symbol=symbol)
            if symbol_config and symbol_config.get("max_position_size"):
                return float(symbol_config["max_position_size"])

            # Check global config
            global_config = await mgr.get_config()
            if global_config and global_config.get("max_position_size"):
                return float(global_config["max_position_size"])

        except Exception as e:
            logger.warning(f"Failed to get config limit for {symbol}: {e}")

        # Fall back to environment variable default
        from shared.constants import MAX_POSITION_SIZE

        return MAX_POSITION_SIZE

    async def check_position_limits(self, order: TradeOrder) -> bool:
        """Check if order meets position size limits with distributed state"""
        if not RISK_MANAGEMENT_ENABLED:
            self.rejection_reason = None
            return True

        # P6.3: Symbol whitelist check — runs before any expensive portfolio refresh.
        # Empty / unset list means all symbols are allowed (safe default).
        allowed_symbols = await self._get_allowed_symbols()
        if allowed_symbols and order.symbol not in allowed_symbols:
            self.rejection_reason = "symbol_not_allowed"
            logger.warning(
                "⛔ RISK REJECTION: Symbol %s not in allowed_symbols whitelist",
                order.symbol,
            )
            return False

        # Refresh portfolio value before checks (mandatory as per AC)
        if not await self._refresh_portfolio_value():
            self.rejection_reason = "refresh_failure"
            logger.error(
                f"⛔ RISK REJECTION: Failed to refresh portfolio value for {order.symbol} - aborting entry"
            )
            return False

        # AC-1 (#352): Explicit zero-capital guard — return a distinct reason before
        # _calculate_portfolio_exposure() hits the "safest for risk" 1.0 fallback.
        if self.total_portfolio_value <= 0:
            self.rejection_reason = "insufficient_margin"
            logger.error(
                f"⛔ RISK REJECTION: Insufficient margin — available capital is "
                f"${self.total_portfolio_value:.2f} (total_portfolio_value <= 0). "
                f"Order {order.symbol} rejected; check exchange account funding."
            )
            return False

        if await self._refresh_positions_from_data_manager() is False:
            self.rejection_reason = "refresh_failure"
            logger.error(
                "⛔ RISK REJECTION: Failed to refresh live positions for %s",
                order.symbol,
            )
            return False

        # NEW: Check absolute position size limit (from config or default)
        position_side = "LONG" if order.side == "buy" else "SHORT"
        position_key = (order.symbol, position_side)

        # AC2 (#459): when flag=on, source existing qty from ExchangeTruthStore.
        # Local self.positions used as fallback and for off/shadow modes.
        _use_exchange_qty = (
            TE_EXCHANGE_TRUTH_STORE_ENABLED == "on"
            and self.exchange_truth_store is not None
        )
        if _use_exchange_qty:
            _snap = self.exchange_truth_store.get_positions().get(position_key)  # type: ignore[union-attr]
            current_quantity = _snap.quantity if _snap is not None else 0.0
            _has_position = _snap is not None
        else:
            _has_position = position_key in self.positions
            current_quantity = (
                self.positions[position_key]["quantity"] if _has_position else 0.0
            )

        if _has_position or _use_exchange_qty:
            new_quantity = current_quantity + order.amount

            # Get limit from config (symbol-specific or global)
            max_position_size = await self.get_position_size_limit(order.symbol)

            if new_quantity > max_position_size:
                logger.warning(
                    f"Position size would exceed limit: {order.symbol} {position_side} "
                    f"current={current_quantity}, new={new_quantity}, max={max_position_size} "
                    f"(source={'exchange' if _use_exchange_qty else 'local'})"
                )
                self.rejection_reason = "absolute_position_size"
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
            self.rejection_reason = "position_size_pct"
            return False

        # Check portfolio exposure limit
        current_exposure = self._calculate_portfolio_exposure()
        if self._portfolio_exposure_refresh_failed:
            self.rejection_reason = "refresh_failure"
            logger.error(
                "⛔ RISK REJECTION: Could not determine exchange position notional "
                "for %s — failing closed",
                order.symbol,
            )
            return False
        if current_exposure > self.max_portfolio_exposure_pct:
            logger.warning(
                f"Portfolio exposure {current_exposure:.2%} exceeds limit "
                f"{self.max_portfolio_exposure_pct:.2%}"
            )
            self.rejection_reason = "portfolio_exposure"
            return False

        # Check algo order limits (prevent -4045 error)
        if not await self.check_algo_order_limits(order):
            self.rejection_reason = "algo_order_limits"
            return False

        self.rejection_reason = None
        return True

    async def check_algo_order_limits(self, order: TradeOrder) -> bool:
        """Check if we have enough room for SL/TP orders on Binance"""
        # If exchange is not initialized or doesn't support the method, skip check
        if not self.exchange or not hasattr(self.exchange, "get_open_algo_orders"):
            return True

        try:
            # 1. Check Symbol-specific limit (max 10 OPEN orders only)
            # get_open_algo_orders queries Binance API for currently-open orders;
            # filled or cancelled orders are never included in this count.
            algo_orders = await self.exchange.get_open_algo_orders(symbol=order.symbol)
            open_count = len(algo_orders)
            # #569: refresh the open-algo-orders gauge on every check (i.e. on
            # every order attempt) so it reflects current exchange state.
            # symbol-labelled only — bounded to actively traded symbols.
            algo_orders_open.labels(symbol=order.symbol).set(open_count)
            otel_algo_orders_open.set(open_count, {"symbol": order.symbol})
            if open_count >= 9:  # Need 2 free slots for a new OCO pair (SL + TP)
                logger.warning(
                    f"⛔ RISK REJECTION: Algo order limit reached for {order.symbol} "
                    f"({open_count}/10 open orders). Cannot place OCO."
                )
                logger.debug(
                    f"Algo limit detail for {order.symbol}: only open (active) orders "
                    f"are counted; filled/cancelled orders do not contribute."
                )
                return False

            # 2. Check Account-wide limit (max 100 OPEN orders only)
            all_algo_orders = await self.exchange.get_open_algo_orders()
            global_open_count = len(all_algo_orders)
            if global_open_count >= 98:  # Leave room for simultaneous orders
                logger.warning(
                    f"⛔ RISK REJECTION: Global account algo order limit reached "
                    f"({global_open_count}/100 open orders). Cannot place OCO."
                )
                return False

            logger.debug(
                f"✅ Algo order limits OK for {order.symbol}: "
                f"symbol={open_count}/10, account={global_open_count}/100 open orders"
            )
            return True
        except Exception as e:
            # #600: was "return True  # Fail-safe to allow trades if API
            # check fails" — that comment lied: it was fail-OPEN, not
            # fail-safe. An algo-order-limit API failure now blocks the
            # entry order until the exchange query succeeds again, rather
            # than admitting an order whose SL/TP OCO may be rejected with
            # -4045 (max algo orders), leaving the position naked.
            logger.error(
                f"⛔ RISK REJECTION: Error checking algo order limits for "
                f"{order.symbol} — failing CLOSED (order rejected) rather "
                f"than risk placing an entry with no room for its SL/TP "
                f"OCO: {e}"
            )
            return False

    async def _refresh_positions_from_data_manager(self) -> bool:
        """Refresh the risk view from its configured authoritative source."""
        if TE_EXCHANGE_TRUTH_STORE_ENABLED == "on":
            return self._refresh_positions_from_exchange_truth_store()

        try:
            positions_data = await trading_store.get_open_positions()
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
                    "entry_time": doc.get("entry_time", datetime.now(UTC)),
                    "last_update": doc.get("last_update", datetime.now(UTC)),
                    "status": doc.get("status", "open"),
                }

            # Only update if positions have changed
            if refreshed_positions != self.positions:
                logger.info("Refreshing positions from Data Manager for consistency")
                self.positions = refreshed_positions
            return True

        except Exception as e:
            logger.error(f"Failed to refresh positions from Data Manager: {e}")
            return False

    async def check_daily_loss_limits(self) -> bool:
        """Check daily loss limits with distributed state"""
        await self._roll_daily_pnl_if_new_day()
        if not RISK_MANAGEMENT_ENABLED:
            return True

        # Refresh portfolio value before checks
        if not await self._refresh_portfolio_value():
            logger.error(
                "⛔ RISK REJECTION: Failed to refresh portfolio value - aborting check"
            )
            return False

        # Refresh daily P&L from Data Manager
        await self._refresh_daily_pnl_from_store()

        # #600: a failed refresh must not leave the kill-switch evaluating
        # against a stale/zero daily_pnl — fail CLOSED (reject new entries)
        # rather than trade blind through a Data Manager outage.
        if self._daily_pnl_refresh_stale:
            logger.warning(
                "⛔ RISK REJECTION: Daily P&L refresh failed (Data Manager "
                "unreachable) — failing CLOSED rather than evaluating the "
                "daily-loss kill-switch against a stale/zero value."
            )
            return False

        max_daily_loss = self.total_portfolio_value * self.max_daily_loss_pct

        if self.daily_pnl < -max_daily_loss:
            logger.warning(
                f"Daily loss {self.daily_pnl:.2f} exceeds limit {-max_daily_loss:.2f}"
            )
            return False

        return True

    async def _refresh_daily_pnl_from_store(self) -> None:
        """Refresh daily P&L from the MongoDB-backed trading store.

        A missing row is treated as an untrusted baseline and fails closed.
        """
        try:
            if await self._roll_daily_pnl_if_new_day():
                return
            today = datetime.now(UTC).date().isoformat()
            daily_pnl = await trading_store.get_daily_pnl(today)
            if daily_pnl is not None:
                self.daily_pnl = float(daily_pnl)
                self._daily_pnl_refresh_stale = False
            else:
                self._daily_pnl_refresh_stale = True
                logger.critical(
                    "No persisted daily P&L exists for today; daily-loss "
                    "risk check fails closed until a baseline is persisted."
                )
        except Exception as e:
            logger.error(
                f"⛔ Failed to refresh daily P&L from MongoDB trading store — the "
                f"daily-loss kill-switch will fail CLOSED until refresh "
                f"succeeds again: {e}"
            )
            self._daily_pnl_refresh_stale = True

    def _calculate_portfolio_exposure(self) -> float:
        """Calculate current portfolio exposure

        Returns 1.0 (100%) when portfolio value is zero/negative as a safety
        guard. The zero-capital guard in check_position_limits() (AC-1 #352)
        intercepts this case first with a distinct ``insufficient_margin``
        rejection reason; callers should inspect rejection_reason rather than
        relying on this float value alone for diagnostics.
        """
        if self.total_portfolio_value <= 0:
            return 1.0

        self._portfolio_exposure_refresh_failed = False

        if TE_EXCHANGE_TRUTH_STORE_ENABLED == "on":
            store = self.exchange_truth_store
            if store is None or not store.is_ready:
                self._portfolio_exposure_refresh_failed = True
                return 1.0

            total_notional = 0.0
            for snapshot in store.get_positions().values():
                if abs(snapshot.quantity) < 1e-9:
                    continue

                notional = abs(snapshot.notional)
                if notional <= 0 and snapshot.mark_price > 0:
                    notional = abs(snapshot.quantity) * snapshot.mark_price
                if notional <= 0:
                    self._portfolio_exposure_refresh_failed = True
                    return 1.0
                total_notional += notional

            return total_notional / self.total_portfolio_value

        total_exposure = 0.0

        for position in self.positions.values():
            if position["quantity"] > 0:
                # Calculate position value as percentage of portfolio
                position_value = position["quantity"] * position["avg_price"]
                exposure_pct = position_value / self.total_portfolio_value
                total_exposure += exposure_pct

        return total_exposure

    def get_cio_portfolio_summary(self, symbol: str) -> dict[str, Any]:
        """
        Calculates real-time portfolio metrics for the CIO reasoning loop.

        #587: sources positions via ``get_positions()`` rather than the raw
        ``self.positions`` journal dict, so this mirrors exactly what
        ``/positions`` returns. When TE_EXCHANGE_TRUTH_STORE_ENABLED=on,
        ``get_positions()`` returns exchange-authoritative snapshots —
        before this fix, ``/state``'s ``open_positions_count`` read the
        local audit journal directly and could diverge from exchange truth
        (e.g. stale entries never pruned on close), while ``/positions``
        (via ``get_positions()``) already reported the exchange-
        authoritative count. Using the same accessor here makes both
        endpoints agree by construction instead of patching the count
        independently.
        """
        total_value = self.total_portfolio_value
        if total_value <= 0:
            return {
                "gross_exposure": 0.0,
                "same_asset_pct": 0.0,
                "open_positions_count": 0,
            }

        total_exposure = 0.0
        same_asset_value = 0.0
        open_count = 0

        for key, pos in self.get_positions().items():
            qty = pos.get("quantity", 0.0)
            if qty != 0:
                open_count += 1
                pos_value = abs(qty * pos.get("avg_price", 0.0))
                total_exposure += pos_value
                # Check if this position is for the requested symbol
                pos_symbol = key[0] if isinstance(key, tuple) else str(key)
                if pos_symbol == symbol:
                    same_asset_value += pos_value

        return {
            "gross_exposure": total_exposure / total_value,
            "same_asset_pct": same_asset_value / total_value,
            "open_positions_count": open_count,
        }

    def get_positions(self) -> dict[tuple[str, str], dict[str, Any]]:
        """Get all current positions.

        When TE_EXCHANGE_TRUTH_STORE_ENABLED=on and the store is seeded, returns
        exchange-authoritative snapshots (AC2/AC3 — #459).  Local self.positions
        continues to be populated as an audit journal regardless of the flag.

        When TE_EXCHANGE_TRUTH_STORE_ENABLED=shadow, reads BOTH sources, emits
        tradeengine_exchange_truth_shadow_delta_total on any divergence, and
        returns local (authority unchanged — AC1 of #461).
        """
        if (
            TE_EXCHANGE_TRUTH_STORE_ENABLED == "on"
            and self.exchange_truth_store is not None
        ):
            snapshots = self.exchange_truth_store.get_positions()
            return {
                k: {
                    "symbol": v.symbol,
                    "position_side": v.side,
                    "quantity": v.quantity,
                    "avg_price": v.entry_price,
                    "mark_price": v.mark_price,
                    "notional": v.notional,
                    "unrealized_pnl": v.unrealized_pnl,
                    "realized_pnl": 0.0,
                    "total_cost": 0.0,
                    "total_value": v.quantity * v.entry_price,
                    "entry_time": v.updated_at,
                    "last_update": v.updated_at,
                    "status": "open",
                    "source": "exchange",
                }
                for k, v in snapshots.items()
            }

        # AC1 (#461): shadow mode — compare local vs exchange, emit deltas, return local.
        if (
            TE_EXCHANGE_TRUTH_STORE_ENABLED == "shadow"
            and self.exchange_truth_store is not None
        ):
            exchange_snaps = self.exchange_truth_store.get_positions()
            local_copy = self.positions.copy()

            for key, local_pos in local_copy.items():
                symbol, side = key
                exchange_snap = exchange_snaps.get(key)
                if exchange_snap is None:
                    exchange_truth_shadow_delta_total.labels(
                        symbol=symbol, side=side, field="missing_in_exchange"
                    ).inc()
                    logger.warning(
                        "shadow_delta: %s/%s present locally but absent from exchange",
                        symbol,
                        side,
                    )
                    continue
                local_qty = float(local_pos.get("quantity", 0))
                if abs(local_qty - exchange_snap.quantity) > 1e-8:
                    exchange_truth_shadow_delta_total.labels(
                        symbol=symbol, side=side, field="quantity"
                    ).inc()
                    logger.warning(
                        "shadow_delta: %s/%s quantity local=%.6f exchange=%.6f",
                        symbol,
                        side,
                        local_qty,
                        exchange_snap.quantity,
                    )

            for key in exchange_snaps:
                if key not in local_copy:
                    symbol, side = key
                    exchange_truth_shadow_delta_total.labels(
                        symbol=symbol, side=side, field="missing_in_local"
                    ).inc()
                    logger.warning(
                        "shadow_delta: %s/%s present on exchange but absent locally",
                        symbol,
                        side,
                    )

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
            for key, position in self.positions.items():
                if isinstance(key, tuple):
                    pos_symbol, _ = key
                else:
                    pos_symbol = str(key)

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
        positions = []
        for key, position in self.positions.items():
            if isinstance(key, tuple):
                pos_symbol, _ = key
            else:
                pos_symbol = str(key)

            if pos_symbol == symbol:
                positions.append(position)
        return positions

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
        """Compatibility wrapper for the rollover helper's forced reset."""
        await self._roll_daily_pnl_if_new_day(force=True)

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
            "mongodb_uri": redact_uri(
                self.settings.mongodb_uri or get_mongodb_connection_string()
            ),
        }


# Global position manager instance
position_manager = PositionManager()
