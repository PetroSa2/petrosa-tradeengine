import asyncio
import logging
import time
from typing import Any, Dict

from prometheus_client import Counter, Histogram

from contracts.order import OrderSide, OrderStatus, OrderType, TradeOrder
from contracts.signal import Signal, TimeInForce
from shared.audit import audit_logger
from shared.config import Settings
from shared.distributed_lock import distributed_lock_manager
from tradeengine.order_manager import OrderManager
from tradeengine.position_manager import PositionManager
from tradeengine.signal_aggregator import SignalAggregator

# Prometheus metrics for signal flow tracking
signals_received = Counter(
    "tradeengine_signals_received_total",
    "Total signals received by the dispatcher",
    ["strategy", "symbol", "action"],
)

signals_processed = Counter(
    "tradeengine_signals_processed_total",
    "Total signals processed by the dispatcher",
    ["status", "action"],
)

orders_executed = Counter(
    "tradeengine_orders_executed_total",
    "Total orders executed",
    ["symbol", "side", "status"],
)

order_execution_time = Histogram(
    "tradeengine_order_execution_seconds",
    "Time taken to execute orders",
    ["symbol", "side"],
)

binance_api_calls = Counter(
    "tradeengine_binance_api_calls_total",
    "Total Binance API calls",
    ["operation", "status"],
)

signals_duplicate = Counter(
    "tradeengine_signals_duplicate_total",
    "Total duplicate signals detected and rejected",
    ["strategy", "symbol", "action"],
)


class OCOManager:
    """Manages OCO (One-Cancels-the-Other) logic for SL/TP orders"""

    def __init__(self, exchange: Any, logger: logging.Logger):
        self.exchange = exchange
        self.logger = logger
        self.active_oco_pairs: Dict[str, Dict[str, Any]] = {}  # position_id -> oco_info
        self.monitoring_task: asyncio.Task | None = None
        self.monitoring_active = False

    async def place_oco_orders(
        self,
        position_id: str,
        symbol: str,
        position_side: str,
        quantity: float,
        stop_loss_price: float,
        take_profit_price: float,
    ) -> Dict[str, Any]:
        """
        Place SL/TP orders that will cancel each other (OCO behavior)

        Args:
            position_id: Unique identifier for the position
            symbol: Trading symbol (e.g., BTCUSDT)
            position_side: LONG or SHORT
            quantity: Position size
            stop_loss_price: Stop loss trigger price
            take_profit_price: Take profit target price

        Returns:
            Dict with sl_order_id and tp_order_id
        """

        self.logger.info(f"🔄 PLACING OCO ORDERS FOR {symbol} {position_side}")
        self.logger.info(f"Position ID: {position_id}")
        self.logger.info(f"Quantity: {quantity}")
        self.logger.info(f"Stop Loss: {stop_loss_price}")
        self.logger.info(f"Take Profit: {take_profit_price}")

        # Determine order sides based on position side
        if position_side == "LONG":
            sl_side = OrderSide.SELL
            tp_side = OrderSide.SELL
        else:  # SHORT
            sl_side = OrderSide.BUY
            tp_side = OrderSide.BUY

        # Place Stop Loss order
        sl_order = TradeOrder(
            symbol=symbol,
            side=sl_side,
            type=OrderType.STOP,
            amount=quantity,
            target_price=stop_loss_price,
            stop_loss=stop_loss_price,
            take_profit=None,
            conditional_price=None,
            conditional_direction=None,
            conditional_timeout=None,
            iceberg_quantity=None,
            client_order_id=None,
            order_id=f"oco_sl_{position_id}_{int(time.time())}",
            status=OrderStatus.PENDING,
            filled_amount=0.0,
            average_price=None,
            position_id=None,
            position_side=position_side,
            exchange="binance",
            reduce_only=True,
            time_in_force=TimeInForce.GTC,
            position_size_pct=None,
            simulate=False,
            updated_at=None,
        )

        # Place Take Profit order
        tp_order = TradeOrder(
            symbol=symbol,
            side=tp_side,
            type=OrderType.TAKE_PROFIT,
            amount=quantity,
            target_price=take_profit_price,
            stop_loss=None,
            take_profit=take_profit_price,
            conditional_price=None,
            conditional_direction=None,
            conditional_timeout=None,
            iceberg_quantity=None,
            client_order_id=None,
            order_id=f"oco_tp_{position_id}_{int(time.time())}",
            status=OrderStatus.PENDING,
            filled_amount=0.0,
            average_price=None,
            position_id=None,
            position_side=position_side,
            exchange="binance",
            reduce_only=True,
            time_in_force=TimeInForce.GTC,
            position_size_pct=None,
            simulate=False,
            updated_at=None,
        )

        # Execute both orders
        try:
            sl_result = await self.exchange.execute(sl_order)
            tp_result = await self.exchange.execute(tp_order)

            sl_order_id = sl_result.get("order_id")
            tp_order_id = tp_result.get("order_id")

            if sl_order_id and tp_order_id:
                # Store the OCO pair for monitoring
                self.active_oco_pairs[position_id] = {
                    "sl_order_id": sl_order_id,
                    "tp_order_id": tp_order_id,
                    "symbol": symbol,
                    "position_side": position_side,
                    "status": "active",
                    "created_at": time.time(),
                }

                self.logger.info("✅ OCO ORDERS PLACED SUCCESSFULLY")
                self.logger.info(f"  Stop Loss Order ID: {sl_order_id}")
                self.logger.info(f"  Take Profit Order ID: {tp_order_id}")

                # Start monitoring if not already active
                if not self.monitoring_active:
                    await self.start_monitoring()

                return {
                    "sl_order_id": sl_order_id,
                    "tp_order_id": tp_order_id,
                    "status": "success",
                }
            else:
                self.logger.error("❌ FAILED TO PLACE OCO ORDERS")
                self.logger.error(f"  SL Result: {sl_result}")
                self.logger.error(f"  TP Result: {tp_result}")
                return {"status": "failed"}

        except Exception as e:
            self.logger.error(f"❌ ERROR PLACING OCO ORDERS: {e}")
            return {"status": "error", "error": str(e)}

    async def cancel_oco_pair(self, position_id: str) -> bool:
        """
        Cancel both SL and TP orders for a position

        Args:
            position_id: Position identifier

        Returns:
            True if both orders were cancelled successfully
        """

        if position_id not in self.active_oco_pairs:
            self.logger.warning(f"⚠️  No OCO pair found for position {position_id}")
            return False

        oco_info = self.active_oco_pairs[position_id]
        sl_order_id = oco_info["sl_order_id"]
        tp_order_id = oco_info["tp_order_id"]
        symbol = oco_info["symbol"]

        self.logger.info(f"🔄 CANCELLING OCO PAIR FOR {symbol}")
        self.logger.info(f"Position ID: {position_id}")
        self.logger.info(f"SL Order ID: {sl_order_id}")
        self.logger.info(f"TP Order ID: {tp_order_id}")

        try:
            # Cancel both orders using batch cancellation
            cancel_result = self.exchange.client.futures_cancel_batch_orders(
                symbol=symbol, orderIdList=[sl_order_id, tp_order_id]
            )

            if cancel_result and len(cancel_result) >= 2:
                self.logger.info("✅ OCO PAIR CANCELLED SUCCESSFULLY")
                self.active_oco_pairs[position_id]["status"] = "cancelled"
                return True
            else:
                self.logger.error(f"❌ FAILED TO CANCEL OCO PAIR: {cancel_result}")
                return False

        except Exception as e:
            self.logger.error(f"❌ ERROR CANCELLING OCO PAIR: {e}")
            return False

    async def cancel_other_order(self, position_id: str, filled_order_id: str) -> bool:
        """
        Cancel the other order when one SL/TP order is filled (OCO behavior)

        Args:
            position_id: Position identifier
            filled_order_id: ID of the order that was filled

        Returns:
            True if the other order was cancelled successfully
        """

        if position_id not in self.active_oco_pairs:
            self.logger.warning(f"⚠️  No OCO pair found for position {position_id}")
            return False

        oco_info = self.active_oco_pairs[position_id]
        sl_order_id = oco_info["sl_order_id"]
        tp_order_id = oco_info["tp_order_id"]

        # Determine which order to cancel
        if filled_order_id == sl_order_id:
            order_to_cancel = tp_order_id
            filled_type = "Stop Loss"
            cancel_type = "Take Profit"
        elif filled_order_id == tp_order_id:
            order_to_cancel = sl_order_id
            filled_type = "Take Profit"
            cancel_type = "Stop Loss"
        else:
            self.logger.warning(
                f"⚠️  Filled order {filled_order_id} not found in OCO pair"
            )
            return False

        self.logger.info(f"🔄 OCO TRIGGERED: {filled_type} FILLED")
        self.logger.info(f"Position ID: {position_id}")
        self.logger.info(f"Filled Order: {filled_order_id} ({filled_type})")
        self.logger.info(f"Cancelling Order: {order_to_cancel} ({cancel_type})")

        try:
            # Cancel the other order
            cancel_result = self.exchange.client.futures_cancel_order(
                symbol=oco_info["symbol"], orderId=order_to_cancel
            )

            if cancel_result:
                self.logger.info(f"✅ {cancel_type} ORDER CANCELLED SUCCESSFULLY")
                self.active_oco_pairs[position_id]["status"] = "completed"
                return True
            else:
                self.logger.error(f"❌ FAILED TO CANCEL {cancel_type} ORDER")
                return False

        except Exception as e:
            self.logger.error(f"❌ ERROR CANCELLING {cancel_type} ORDER: {e}")
            return False

    async def start_monitoring(self) -> None:
        """Start monitoring active orders for fills and trigger OCO logic"""
        if self.monitoring_active:
            return

        self.monitoring_active = True
        self.monitoring_task = asyncio.create_task(self._monitor_orders())
        self.logger.info("🔍 STARTED ORDER MONITORING")

    async def stop_monitoring(self) -> None:
        """Stop monitoring orders"""
        self.monitoring_active = False
        if self.monitoring_task:
            self.monitoring_task.cancel()
            try:
                await self.monitoring_task
            except asyncio.CancelledError:
                pass
        self.logger.info("🔍 STOPPED ORDER MONITORING")

    async def _monitor_orders(self) -> None:
        """
        Monitor active orders for fills and trigger OCO logic
        This runs in a separate task
        """

        self.logger.info("🔍 STARTING ORDER MONITORING")
        self.logger.info(f"Active OCO pairs: {len(self.active_oco_pairs)}")

        while self.monitoring_active and self.active_oco_pairs:
            try:
                # Check each active OCO pair
                for position_id, oco_info in list(self.active_oco_pairs.items()):
                    if oco_info["status"] != "active":
                        continue

                    # Query order status
                    orders = self.exchange.client.futures_get_open_orders(
                        symbol=oco_info["symbol"]
                    )

                    sl_order_id = oco_info["sl_order_id"]
                    tp_order_id = oco_info["tp_order_id"]

                    # Check if orders still exist
                    sl_exists = any(order["orderId"] == sl_order_id for order in orders)
                    tp_exists = any(order["orderId"] == tp_order_id for order in orders)

                    # If one order is missing, it was likely filled
                    if not sl_exists and tp_exists:
                        await self.cancel_other_order(position_id, sl_order_id)
                    elif sl_exists and not tp_exists:
                        await self.cancel_other_order(position_id, tp_order_id)
                    elif not sl_exists and not tp_exists:
                        # Both orders are gone - OCO completed
                        self.logger.info(f"✅ OCO COMPLETED FOR POSITION {position_id}")
                        self.active_oco_pairs[position_id]["status"] = "completed"

                # Remove completed pairs
                self.active_oco_pairs = {
                    pid: info
                    for pid, info in self.active_oco_pairs.items()
                    if info["status"] == "active"
                }

                # Wait before next check
                await asyncio.sleep(2)  # Check every 2 seconds

            except Exception as e:
                self.logger.error(f"❌ ERROR IN ORDER MONITORING: {e}")
                await asyncio.sleep(5)  # Wait longer on error

        self.logger.info("🔍 ORDER MONITORING STOPPED")


class Dispatcher:
    """Central dispatcher for trading operations with distributed state management"""

    def __init__(self, exchange: Any = None) -> None:
        self.settings = Settings()
        self.order_manager = OrderManager()
        self.position_manager = PositionManager()
        self.signal_aggregator = SignalAggregator()
        self.exchange = exchange
        self.logger = logging.getLogger(__name__)

        # Initialize OCO Manager for SL/TP order management
        self.oco_manager = OCOManager(exchange, self.logger)

        # Duplicate signal detection cache
        # Format: {signal_id: timestamp} - stores signal IDs with their reception time
        self.signal_cache: dict[str, float] = {}
        self.signal_cache_ttl = (
            60  # Cache TTL in seconds (signals older than this are considered unique)
        )
        self.signal_cache_cleanup_interval = 300  # Cleanup every 5 minutes
        self.last_cache_cleanup = time.time()

    async def initialize(self) -> None:
        """Initialize dispatcher components with distributed state management"""
        try:
            # Initialize distributed lock manager first
            await distributed_lock_manager.initialize()

            # Initialize components
            await self.order_manager.initialize()
            await self.position_manager.initialize()

            self.logger.info(
                "Dispatcher initialized successfully with distributed state management"
            )
        except Exception as e:
            self.logger.error(f"Dispatcher initialization error: {e}")
            raise

    async def close(self) -> None:
        """Close dispatcher components"""
        try:
            await self.order_manager.close()
            await self.position_manager.close()
            await distributed_lock_manager.close()
            self.logger.info("Dispatcher closed successfully")
        except Exception as e:
            self.logger.error(f"Dispatcher close error: {e}")

    async def health_check(self) -> dict[str, Any]:
        """Check dispatcher health with distributed state info"""
        try:
            # Check if components have health_check methods
            order_manager_health = {"status": "unknown"}
            position_manager_health = {"status": "unknown"}
            distributed_lock_health = {"status": "unknown"}

            if hasattr(self.order_manager, "health_check"):
                order_manager_health = await self.order_manager.health_check()
            else:
                order_manager_health = {"status": "healthy", "type": "order_manager"}

            if hasattr(self.position_manager, "health_check"):
                position_manager_health = await self.position_manager.health_check()
            else:
                position_manager_health = {
                    "status": "healthy",
                    "type": "position_manager",
                }

            if hasattr(distributed_lock_manager, "health_check"):
                distributed_lock_health = await distributed_lock_manager.health_check()
            else:
                distributed_lock_health = {
                    "status": "healthy",
                    "type": "distributed_lock_manager",
                }

            return {
                "status": "healthy",
                "components": {
                    "order_manager": order_manager_health,
                    "position_manager": position_manager_health,
                    "signal_aggregator": "active",
                    "distributed_lock_manager": distributed_lock_health,
                },
            }
        except Exception as e:
            self.logger.error(f"Health check error: {e}")
            return {"status": "unhealthy", "error": str(e)}

    async def process_signal(
        self,
        signal: Signal,
        conflict_resolution: str = "strongest_wins",
        timeframe_resolution: str = "higher_timeframe_wins",
        risk_management: bool = True,
    ) -> dict[str, Any]:
        """Process a trading signal with distributed state management"""
        try:
            # Log signal
            if audit_logger.enabled and audit_logger.connected:
                audit_logger.log_signal(signal.model_dump())

            # Add signal to aggregator
            self.signal_aggregator.add_signal(signal)

            # Process based on strategy mode
            if signal.strategy_mode.value == "deterministic":
                from tradeengine.signal_aggregator import DeterministicProcessor

                processor = DeterministicProcessor()
                result = await processor.process(
                    signal, self.signal_aggregator.active_signals
                )
            elif signal.strategy_mode.value == "ml_light":
                from tradeengine.signal_aggregator import MLProcessor

                ml_processor = MLProcessor()
                result = await ml_processor.process(
                    signal, self.signal_aggregator.active_signals
                )
            elif signal.strategy_mode.value == "llm_reasoning":
                from tradeengine.signal_aggregator import LLMProcessor

                llm_processor = LLMProcessor()
                result = await llm_processor.process(
                    signal, self.signal_aggregator.active_signals
                )
            else:
                result = {
                    "status": "rejected",
                    "reason": f"Unknown strategy mode: {signal.strategy_mode.value}",
                }

            # Log result
            if audit_logger.enabled and audit_logger.connected:
                audit_logger.log_signal(
                    {
                        "signal": signal.model_dump(),
                        "result": result,
                        "conflict_resolution": conflict_resolution,
                        "timeframe_resolution": timeframe_resolution,
                    }
                )

            return result

        except Exception as e:
            self.logger.error(f"Signal processing error: {e}")
            if audit_logger.enabled and audit_logger.connected:
                audit_logger.log_error(
                    {
                        "error": str(e),
                        "signal": signal.model_dump(),
                        "endpoint": "process_signal",
                    }
                )
            return {"status": "error", "error": str(e)}

    def _generate_signal_id(self, signal: Signal) -> str:
        """Generate a unique ID for a signal for deduplication.

        Uses strategy_id, symbol, action, and timestamp (rounded to second) to identify duplicates.
        This allows detecting signals that are sent via both HTTP and NATS within the same second.
        """
        # Round timestamp to second precision to catch duplicates within 1 second
        timestamp_second = signal.timestamp.isoformat()[:19] if signal.timestamp else ""
        return (
            f"{signal.strategy_id}_{signal.symbol}_{signal.action}_{timestamp_second}"
        )

    def _cleanup_signal_cache(self) -> None:
        """Remove expired entries from the signal cache."""
        current_time = time.time()

        # Only cleanup if interval has passed
        if current_time - self.last_cache_cleanup < self.signal_cache_cleanup_interval:
            return

        # Remove entries older than TTL
        expired_ids = [
            signal_id
            for signal_id, timestamp in self.signal_cache.items()
            if current_time - timestamp > self.signal_cache_ttl
        ]

        for signal_id in expired_ids:
            del self.signal_cache[signal_id]

        if expired_ids:
            self.logger.debug(
                f"Cleaned up {len(expired_ids)} expired signal cache entries"
            )

        self.last_cache_cleanup = current_time

    async def dispatch(self, signal: Signal) -> dict[str, Any]:
        """Dispatch a signal for processing with distributed state management"""
        try:
            # Track signal reception in metrics
            signals_received.labels(
                strategy=signal.strategy_id, symbol=signal.symbol, action=signal.action
            ).inc()

            # Enhanced logging for signal reception
            self.logger.info(
                f"📩 SIGNAL RECEIVED: {signal.strategy_id} | "
                f"{signal.symbol} {signal.action.upper()} @ {signal.current_price} | "
                f"Confidence: {signal.confidence:.2%} | "
                f"Timeframe: {signal.timeframe}"
            )

            # Check for duplicate signals (prevents double processing from HTTP + NATS)
            signal_id = self._generate_signal_id(signal)
            current_time = time.time()

            if signal_id in self.signal_cache:
                age = current_time - self.signal_cache[signal_id]
                if age < self.signal_cache_ttl:
                    # Duplicate detected within TTL window
                    self.logger.warning(
                        f"🚫 DUPLICATE SIGNAL DETECTED AND REJECTED: {signal.strategy_id} | "
                        f"{signal.symbol} {signal.action.upper()} | "
                        f"Age: {age:.2f}s | Original received {age:.2f}s ago"
                    )
                    signals_duplicate.labels(
                        strategy=signal.strategy_id,
                        symbol=signal.symbol,
                        action=signal.action,
                    ).inc()
                    return {
                        "status": "duplicate",
                        "reason": f"Duplicate signal detected (age: {age:.2f}s)",
                        "original_time": self.signal_cache[signal_id],
                        "duplicate_age_seconds": age,
                    }

            # Store signal in cache
            self.signal_cache[signal_id] = current_time

            # Cleanup old cache entries periodically
            self._cleanup_signal_cache()

            # Handle hold signals
            if signal.action == "hold":
                self.logger.info(
                    f"⏸️  HOLD SIGNAL FILTERED: {signal.strategy_id} | "
                    f"{signal.symbol} | No action taken"
                )
                signals_processed.labels(status="hold", action="hold").inc()
                return {"status": "hold", "reason": "Signal indicates hold action"}

            # Log signal processing
            self.logger.info(
                f"⚙️  PROCESSING SIGNAL: {signal.strategy_id} | "
                f"{signal.symbol} {signal.action.upper()}"
            )

            # Process the signal
            result = await self.process_signal(signal)

            # If processing was successful, execute the order with distributed lock
            # Signal processors can return "success" or "executed" - both are valid for order execution
            signal_status = result.get("status")
            if signal_status in ("success", "executed"):
                self.logger.info(
                    f"✅ SIGNAL VALIDATED: {signal.strategy_id} | "
                    f"Converting to order for {signal.symbol} | "
                    f"Processing status: {signal_status}"
                )
                order = self._signal_to_order(signal)

                self.logger.info(
                    f"🔐 ACQUIRING DISTRIBUTED LOCK: order_execution_{signal.symbol}"
                )
                # Execute order with distributed lock to ensure consensus
                execution_result = await distributed_lock_manager.execute_with_lock(
                    f"order_execution_{signal.symbol}",
                    self._execute_order_with_consensus,
                    order,
                )

                result["execution_result"] = execution_result
                result["status"] = (
                    "executed"  # Change status to executed for consistency
                )

                self.logger.info(
                    f"🎯 SIGNAL DISPATCH COMPLETE: {signal.strategy_id} | "
                    f"Execution status: {execution_result.get('status')}"
                )
                signals_processed.labels(status="executed", action=signal.action).inc()
            elif signal_status == "rejected":
                self.logger.info(
                    f"⛔ SIGNAL REJECTED: {signal.strategy_id} | "
                    f"Reason: {result.get('reason', 'Unknown')}"
                )
                signals_processed.labels(status="rejected", action=signal.action).inc()
            else:
                self.logger.warning(
                    f"⚠️  SIGNAL VALIDATION FAILED: {signal.strategy_id} | "
                    f"Status: {signal_status} | Reason: {result.get('reason', 'Unknown')}"
                )
                signals_processed.labels(status="failed", action=signal.action).inc()

            return result

        except Exception as e:
            self.logger.error(
                f"❌ DISPATCH ERROR: {signal.strategy_id if hasattr(signal, 'strategy_id') else 'Unknown'} | "
                f"Error: {e}",
                exc_info=True,
            )
            return {"status": "error", "error": str(e)}

    async def _execute_order_with_consensus(self, order: TradeOrder) -> dict[str, Any]:
        """Execute order with distributed consensus"""
        try:
            # Check risk limits with distributed state
            if not await self.position_manager.check_position_limits(order):
                return {"status": "rejected", "reason": "Risk limits exceeded"}

            if not await self.position_manager.check_daily_loss_limits():
                return {"status": "rejected", "reason": "Daily loss limits exceeded"}

            # Execute order
            result = await self.execute_order(order)

            # Update position with distributed state management and create position record
            # Market orders return "NEW" status immediately, which is valid for risk management
            if result and result.get("status") in ["filled", "partially_filled", "NEW"]:
                await self.position_manager.update_position(order, result)

                # Create position tracking record with dual persistence
                await self.position_manager.create_position_record(order, result)

                # Place stop loss and take profit orders if specified
                await self._place_risk_management_orders(order, result)

            return result

        except Exception as e:
            self.logger.error(f"Order execution with consensus error: {e}")
            return {"status": "error", "error": str(e)}

    def _signal_to_order(self, signal: Signal) -> TradeOrder:
        """Convert a signal to a trade order with dynamic minimum amounts"""
        import uuid
        from datetime import datetime

        # Calculate order amount based on signal quantity or dynamic minimum
        amount = self._calculate_order_amount(signal)

        # Generate unique position ID for tracking
        position_id = str(uuid.uuid4())

        # Determine position side for hedge mode (buy=LONG, sell=SHORT)
        position_side = "LONG" if signal.action == "buy" else "SHORT"

        # Collect all signal parameters for position tracking
        strategy_metadata = {
            "signal_id": signal.signal_id or signal.id,
            "strategy_id": signal.strategy_id,
            "strategy_mode": signal.strategy_mode.value,
            "source": signal.source,
            "strategy": signal.strategy,
            "timeframe": signal.timeframe,
            "confidence": signal.confidence,
            "strength": signal.strength.value,
            "indicators": signal.indicators,
            "rationale": signal.rationale,
            "llm_reasoning": signal.llm_reasoning,
            "metadata": signal.metadata,
            "meta": signal.meta,
        }

        # Create the order
        order = TradeOrder(
            order_id=f"order_{signal.strategy_id}_{datetime.utcnow().timestamp()}",
            symbol=signal.symbol,
            side=signal.action,
            type=signal.order_type.value,
            amount=amount,
            target_price=signal.current_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            conditional_price=signal.conditional_price,
            conditional_direction=signal.conditional_direction,
            conditional_timeout=signal.conditional_timeout,
            iceberg_quantity=signal.iceberg_quantity,
            client_order_id=signal.client_order_id,
            status=OrderStatus.PENDING,
            reduce_only=False,  # Orders from signals are position-opening
            filled_amount=0.0,
            average_price=0.0,
            time_in_force=signal.time_in_force.value,
            position_size_pct=signal.position_size_pct,
            created_at=signal.timestamp,
            updated_at=signal.timestamp,
            simulate=signal.meta.get("simulate", False) if signal.meta else False,
            # Hedge mode position tracking
            position_id=position_id,
            position_side=position_side,
            exchange="binance",
            strategy_metadata=strategy_metadata,
        )

        return order

    def _calculate_order_amount(self, signal: Signal) -> float:
        """Calculate order amount ensuring MIN_NOTIONAL is met"""
        try:
            # Import here to avoid circular imports
            from tradeengine.api import binance_exchange

            # Calculate minimum amount needed to meet MIN_NOTIONAL
            current_price = signal.current_price or 0
            min_amount = binance_exchange.calculate_min_order_amount(
                signal.symbol, current_price
            )

            # If signal provides quantity, validate it meets MIN_NOTIONAL
            if signal.quantity and signal.quantity > 0:
                if signal.quantity < min_amount:
                    self.logger.warning(
                        f"Signal quantity {signal.quantity} is below minimum {min_amount} "
                        f"for {signal.symbol} at ${current_price:.2f}. Using minimum."
                    )
                    amount = min_amount
                else:
                    amount = signal.quantity
            else:
                # Use calculated minimum
                amount = min_amount

            self.logger.info(
                f"Calculated order amount for {signal.symbol}: {amount} "
                f"(signal_qty: {signal.quantity}, min_required: {min_amount}, "
                f"current_price: ${current_price:.2f})"
            )

            return amount

        except Exception as e:
            self.logger.error(
                f"Error calculating order amount for {signal.symbol}: {e}"
            )
            # Fallback to safe default
            return 0.001

    async def execute_order(self, order: TradeOrder) -> dict[str, Any]:
        """Execute a trading order with detailed logging"""
        import time

        start_time = time.time()

        try:
            # Enhanced logging for order execution
            self.logger.info(
                f"🔨 EXECUTING ORDER: {order.symbol} {order.side.upper()} "
                f"{order.amount} @ {order.target_price} | "
                f"Type: {order.type} | ID: {order.order_id}"
            )

            # Log order
            if audit_logger.enabled and audit_logger.connected:
                audit_logger.log_order(order.model_dump())

            # Execute order on Binance exchange
            if order.simulate:
                # Simulated order - just track locally
                self.logger.info(
                    f"🎭 SIMULATION MODE: Order {order.order_id} simulated"
                )
                result = {"status": "pending", "simulated": True}
                await self.order_manager.track_order(order, result)
            else:
                # Real order - execute on Binance
                if self.exchange:
                    try:
                        self.logger.info(
                            f"📤 SENDING TO BINANCE: {order.symbol} {order.side} "
                            f"{order.amount} @ {order.target_price}"
                        )
                        result = await self.exchange.execute(order)
                        await self.order_manager.track_order(order, result)

                        # Log success with details
                        self.logger.info(
                            f"✅ BINANCE ORDER EXECUTED: {order.symbol} {order.side} | "
                            f"Status: {result.get('status')} | "
                            f"Order ID: {result.get('order_id', 'N/A')} | "
                            f"Fill Price: {result.get('fill_price', 'N/A')} | "
                            f"Result: {result}"
                        )
                    except Exception as exchange_error:
                        self.logger.error(
                            f"❌ BINANCE EXCHANGE ERROR: {order.symbol} {order.side} | "
                            f"Error: {exchange_error} | Order ID: {order.order_id}",
                            exc_info=True,
                        )
                        result = {"status": "error", "error": str(exchange_error)}
                        await self.order_manager.track_order(order, result)
                else:
                    # No exchange provided, just track locally
                    self.logger.warning(
                        f"⚠️  NO EXCHANGE CONFIGURED: Order {order.order_id} tracked locally only"
                    )
                    result = {"status": "pending", "no_exchange": True}
                    await self.order_manager.track_order(order, result)

            # Log result
            if audit_logger.enabled and audit_logger.connected:
                audit_logger.log_order(
                    {
                        "order": order.model_dump(),
                        "result": result,
                    }
                )

            self.logger.info(
                f"📊 ORDER EXECUTION COMPLETE: {order.order_id} | Status: {result.get('status')}"
            )

            # Track order execution metrics
            execution_time = time.time() - start_time
            order_execution_time.labels(symbol=order.symbol, side=order.side).observe(
                execution_time
            )
            orders_executed.labels(
                symbol=order.symbol,
                side=order.side,
                status=result.get("status", "unknown"),
            ).inc()

            return result

        except Exception as e:
            self.logger.error(
                f"❌ ORDER EXECUTION FAILED: {order.order_id} | Error: {e}",
                exc_info=True,
            )
            if audit_logger.enabled and audit_logger.connected:
                audit_logger.log_error(
                    {
                        "error": str(e),
                        "order": order.model_dump(),
                        "endpoint": "execute_order",
                    }
                )
            return {"status": "error", "error": str(e)}

    def get_signal_summary(self) -> dict[str, Any]:
        """Get signal processing summary"""
        return self.signal_aggregator.get_signal_summary()

    def set_strategy_weight(self, strategy_id: str, weight: float) -> None:
        """Set weight for a strategy"""
        self.signal_aggregator.set_strategy_weight(strategy_id, weight)

    def get_positions(self) -> dict[str, Any]:
        """Get all positions"""
        return self.position_manager.get_positions()

    def get_position(self, symbol: str) -> dict[str, Any] | None:
        """Get specific position"""
        return self.position_manager.get_position(symbol)

    def get_portfolio_summary(self) -> dict[str, Any]:
        """Get portfolio summary"""
        return self.position_manager.get_portfolio_summary()

    def get_active_orders(self) -> list[dict[str, Any]]:
        """Get all active orders"""
        return self.order_manager.get_active_orders()

    def get_conditional_orders(self) -> list[dict[str, Any]]:
        """Get all conditional orders"""
        return self.order_manager.get_conditional_orders()

    def get_order_history(self) -> list[dict[str, Any]]:
        """Get order history"""
        return self.order_manager.get_order_history()

    def get_order_summary(self) -> dict[str, Any]:
        """Get order summary"""
        return self.order_manager.get_order_summary()

    def get_order(self, order_id: str) -> dict[str, Any] | None:
        """Get specific order"""
        return self.order_manager.get_order(order_id)

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order"""
        return self.order_manager.cancel_order(order_id)

    async def get_account_info(self) -> dict[str, Any]:
        """Get account information"""
        try:
            # This would integrate with the exchange to get real account info
            # For now, return a placeholder
            return {
                "account_type": "simulated",
                "balances": {
                    "BTC": {"free": "0.1", "locked": "0.0"},
                    "USDT": {"free": "5000.0", "locked": "0.0"},
                },
                "total_balance_usdt": 5000.0,
                "positions": self.get_positions(),
                "pnl": {
                    "total": 0.0,
                    "daily": self.position_manager.get_daily_pnl(),
                    "unrealized": self.position_manager.get_total_unrealized_pnl(),
                },
                "risk_metrics": {
                    "max_position_size": 0.1,
                    "max_daily_loss": 100.0,
                    "current_exposure": self.position_manager._calculate_portfolio_exposure(),
                },
            }
        except Exception as e:
            self.logger.error(f"Error getting account info: {e}")
            return {"error": str(e)}

    async def get_price(self, symbol: str) -> float:
        """Get current price for a symbol"""
        try:
            # This would integrate with price feeds
            # For now, return a placeholder
            base_prices = {
                "BTCUSDT": 45000.0,
                "ETHUSDT": 3000.0,
                "ADAUSDT": 0.5,
            }
            return base_prices.get(symbol, 100.0)
        except Exception as e:
            self.logger.error(f"Error getting price for {symbol}: {e}")
            return 0.0

    def get_metrics(self) -> dict[str, Any]:
        """Get system metrics"""
        return {
            "positions_count": len(self.get_positions()),
            "active_orders_count": len(self.get_active_orders()),
            "conditional_orders_count": len(self.get_conditional_orders()),
            "daily_pnl": self.position_manager.get_daily_pnl(),
            "total_unrealized_pnl": self.position_manager.get_total_unrealized_pnl(),
        }

    async def _place_risk_management_orders(
        self, order: TradeOrder, result: dict[str, Any]
    ) -> None:
        """Place stop loss and take profit orders with OCO behavior after successful position execution"""
        try:
            # ENTRY LOG - Always log when this method is called
            self.logger.info(
                f"🔧 ENTERING _place_risk_management_orders | Symbol: {order.symbol} | "
                f"SL: {order.stop_loss} | TP: {order.take_profit} | "
                f"Exchange: {self.exchange is not None} | Reduce_only: {order.reduce_only}"
            )

            if not self.exchange:
                self.logger.warning(
                    "No exchange configured, cannot place risk management orders"
                )
                return

            # Only place risk management orders for position-opening orders (not reduce_only)
            if order.reduce_only:
                self.logger.debug(
                    "Skipping risk management orders for reduce_only order"
                )
                return

            # Check if both SL and TP are specified for OCO behavior
            if (
                order.stop_loss
                and order.stop_loss > 0
                and order.take_profit
                and order.take_profit > 0
            ):
                # Use OCO logic for paired SL/TP orders
                self.logger.info(f"🔄 PLACING OCO ORDERS FOR {order.symbol}")

                # Get the filled quantity with robust extraction
                # Market orders may return amount=0 when status is NEW (not yet filled)
                filled_quantity = result.get("amount", None)

                # Convert to float if it's a string
                if filled_quantity is not None and isinstance(filled_quantity, str):
                    try:
                        filled_quantity = float(filled_quantity)
                    except (ValueError, TypeError):
                        filled_quantity = None

                # Use order.amount if result amount is 0, None, or invalid
                if filled_quantity is None or filled_quantity <= 0:
                    filled_quantity = order.amount
                    self.logger.info(
                        f"Using order.amount ({order.amount}) for OCO - "
                        f"result amount was {result.get('amount')}"
                    )
                else:
                    self.logger.info(f"Using filled amount {filled_quantity} for OCO")

                # Final safety check
                if filled_quantity <= 0:
                    self.logger.error(
                        f"Cannot place OCO orders: invalid quantity {filled_quantity} "
                        f"(order.amount={order.amount}, result.amount={result.get('amount')})"
                    )
                    return

                oco_result = await self.oco_manager.place_oco_orders(
                    position_id=order.position_id or "",
                    symbol=order.symbol,
                    position_side=order.position_side or "",
                    quantity=filled_quantity,
                    stop_loss_price=order.stop_loss or 0.0,
                    take_profit_price=order.take_profit or 0.0,
                )

                if oco_result["status"] == "success":
                    self.logger.info(
                        f"✅ OCO ORDERS PLACED SUCCESSFULLY FOR {order.symbol}"
                    )

                    # Update position record with OCO order IDs
                    if order.position_id:
                        await self.position_manager.update_position_risk_orders(
                            order.position_id,
                            stop_loss_order_id=oco_result.get("sl_order_id"),
                            take_profit_order_id=oco_result.get("tp_order_id"),
                        )
                else:
                    self.logger.error(
                        f"❌ OCO ORDERS FAILED FOR {order.symbol}: {oco_result}"
                    )
                    # Fallback to individual order placement
                    await self._place_individual_risk_orders(order, result)

            elif order.stop_loss and order.stop_loss > 0:
                # Only stop loss specified
                await self._place_stop_loss_order(order, result)
            elif order.take_profit and order.take_profit > 0:
                # Only take profit specified
                await self._place_take_profit_order(order, result)

        except Exception as e:
            self.logger.error(
                f"Failed to place risk management orders: {e}", exc_info=True
            )

    async def _place_individual_risk_orders(
        self, order: TradeOrder, result: dict[str, Any]
    ) -> None:
        """Fallback method to place individual SL/TP orders (non-OCO)"""
        try:
            # Place stop loss order if specified
            if order.stop_loss and order.stop_loss > 0:
                await self._place_stop_loss_order(order, result)

            # Place take profit order if specified
            if order.take_profit and order.take_profit > 0:
                await self._place_take_profit_order(order, result)
        except Exception as e:
            self.logger.error(
                f"Failed to place individual risk orders: {e}", exc_info=True
            )

    async def _place_stop_loss_order(
        self, order: TradeOrder, result: dict[str, Any]
    ) -> None:
        """Place stop loss order"""
        try:
            from datetime import datetime

            from contracts.order import OrderStatus, TradeOrder

            # Get the filled quantity with robust extraction
            filled_quantity = result.get("amount", None)

            # Convert to float if it's a string
            if filled_quantity is not None and isinstance(filled_quantity, str):
                try:
                    filled_quantity = float(filled_quantity)
                except (ValueError, TypeError):
                    filled_quantity = None

            # Use order.amount if result amount is 0, None, or invalid
            if filled_quantity is None or filled_quantity <= 0:
                filled_quantity = order.amount
                self.logger.info(
                    f"Using order.amount ({order.amount}) for SL - "
                    f"result amount was {result.get('amount')}"
                )

            # Safety check
            if filled_quantity <= 0:
                self.logger.error(
                    f"Cannot place stop loss: invalid quantity {filled_quantity}"
                )
                return

            # Create stop loss order
            stop_loss_order = TradeOrder(
                order_id=f"sl_{order.order_id}_{datetime.utcnow().timestamp()}",
                symbol=order.symbol,
                side=(
                    "sell" if order.side == "buy" else "buy"
                ),  # Opposite side to close position
                type="stop",  # Stop market order
                amount=filled_quantity,
                stop_loss=order.stop_loss,
                take_profit=None,  # Not applicable for stop loss order
                target_price=None,  # Market order when triggered
                position_id=order.position_id,
                position_side=order.position_side,
                exchange=order.exchange,
                strategy_metadata=order.strategy_metadata,
                reduce_only=True,  # This is a position-closing order
                status=OrderStatus.PENDING,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
                # Optional fields with defaults
                conditional_price=None,
                conditional_direction=None,
                conditional_timeout=None,
                iceberg_quantity=None,
                client_order_id=None,
                filled_amount=0.0,
                average_price=None,
                simulate=False,
                time_in_force="GTC",
                position_size_pct=None,
            )

            self.logger.info(
                f"📉 PLACING STOP LOSS: {order.symbol} {stop_loss_order.side} "
                f"{stop_loss_order.amount} @ {order.stop_loss}"
            )

            # Execute stop loss order
            if self.exchange:
                sl_result = await self.exchange.execute(stop_loss_order)
            else:
                sl_result = {"status": "error", "error": "No exchange configured"}

            # Check if order was successfully placed (NEW status for SL/TP orders)
            # SL/TP orders return "NEW" status when placed, not "filled"
            if sl_result.get("status") in [
                "filled",
                "partially_filled",
                "pending",
                "NEW",
            ]:
                self.logger.info(
                    f"✅ STOP LOSS PLACED: {order.symbol} | "
                    f"Order ID: {sl_result.get('order_id', 'N/A')} | "
                    f"Stop Price: {order.stop_loss} | "
                    f"Status: {sl_result.get('status')}"
                )

                # Track the stop loss order
                await self.order_manager.track_order(stop_loss_order, sl_result)

                # Update position record with stop loss order ID
                if order.position_id:
                    await self.position_manager.update_position_risk_orders(
                        order.position_id, stop_loss_order_id=sl_result.get("order_id")
                    )
            else:
                self.logger.error(
                    f"❌ STOP LOSS FAILED: {order.symbol} | "
                    f"Status: {sl_result.get('status', 'N/A')} | "
                    f"Error: {sl_result.get('error', 'Unknown error')}"
                )

        except Exception as e:
            self.logger.error(f"Failed to place stop loss order: {e}", exc_info=True)

    async def _place_take_profit_order(
        self, order: TradeOrder, result: dict[str, Any]
    ) -> None:
        """Place take profit order"""
        try:
            from datetime import datetime

            from contracts.order import OrderStatus, TradeOrder

            # Get the filled quantity with robust extraction
            filled_quantity = result.get("amount", None)

            # Convert to float if it's a string
            if filled_quantity is not None and isinstance(filled_quantity, str):
                try:
                    filled_quantity = float(filled_quantity)
                except (ValueError, TypeError):
                    filled_quantity = None

            # Use order.amount if result amount is 0, None, or invalid
            if filled_quantity is None or filled_quantity <= 0:
                filled_quantity = order.amount
                self.logger.info(
                    f"Using order.amount ({order.amount}) for TP - "
                    f"result amount was {result.get('amount')}"
                )

            # Safety check
            if filled_quantity <= 0:
                self.logger.error(
                    f"Cannot place take profit: invalid quantity {filled_quantity}"
                )
                return

            # Create take profit order
            take_profit_order = TradeOrder(
                order_id=f"tp_{order.order_id}_{datetime.utcnow().timestamp()}",
                symbol=order.symbol,
                side=(
                    "sell" if order.side == "buy" else "buy"
                ),  # Opposite side to close position
                type="take_profit",  # Take profit market order
                amount=filled_quantity,
                take_profit=order.take_profit,
                target_price=None,  # Market order when triggered
                position_id=order.position_id,
                position_side=order.position_side,
                exchange=order.exchange,
                strategy_metadata=order.strategy_metadata,
                reduce_only=True,  # This is a position-closing order
                status=OrderStatus.PENDING,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
                # Optional fields with defaults
                stop_loss=None,
                conditional_price=None,
                conditional_direction=None,
                conditional_timeout=None,
                iceberg_quantity=None,
                client_order_id=None,
                filled_amount=0.0,
                average_price=None,
                simulate=False,
                time_in_force="GTC",
                position_size_pct=None,
            )

            self.logger.info(
                f"📈 PLACING TAKE PROFIT: {order.symbol} {take_profit_order.side} "
                f"{take_profit_order.amount} @ {order.take_profit}"
            )

            # Execute take profit order
            if self.exchange:
                tp_result = await self.exchange.execute(take_profit_order)
            else:
                tp_result = {"status": "error", "error": "No exchange configured"}

            # Check if order was successfully placed (NEW status for SL/TP orders)
            # SL/TP orders return "NEW" status when placed, not "filled"
            if tp_result.get("status") in [
                "filled",
                "partially_filled",
                "pending",
                "NEW",
            ]:
                self.logger.info(
                    f"✅ TAKE PROFIT PLACED: {order.symbol} | "
                    f"Order ID: {tp_result.get('order_id', 'N/A')} | "
                    f"Take Profit Price: {order.take_profit} | "
                    f"Status: {tp_result.get('status')}"
                )

                # Track the take profit order
                await self.order_manager.track_order(take_profit_order, tp_result)

                # Update position record with take profit order ID
                if order.position_id:
                    await self.position_manager.update_position_risk_orders(
                        order.position_id,
                        take_profit_order_id=tp_result.get("order_id"),
                    )
            else:
                self.logger.error(
                    f"❌ TAKE PROFIT FAILED: {order.symbol} | "
                    f"Status: {tp_result.get('status', 'N/A')} | "
                    f"Error: {tp_result.get('error', 'Unknown error')}"
                )

        except Exception as e:
            self.logger.error(f"Failed to place take profit order: {e}", exc_info=True)

    async def close_position_with_cleanup(
        self,
        position_id: str,
        symbol: str,
        position_side: str,
        quantity: float,
        reason: str = "manual",
    ) -> dict[str, Any]:
        """
        Close a position and clean up all associated SL/TP orders (OCO cleanup)

        Args:
            position_id: Unique identifier for the position
            symbol: Trading symbol (e.g., BTCUSDT)
            position_side: LONG or SHORT
            quantity: Position size to close
            reason: Reason for closing (manual, sl_triggered, tp_triggered, etc.)

        Returns:
            Dict with closing result and cleanup status
        """
        try:
            self.logger.info("🔄 CLOSING POSITION WITH OCO CLEANUP")
            self.logger.info(f"Position ID: {position_id}")
            self.logger.info(f"Symbol: {symbol}")
            self.logger.info(f"Position Side: {position_side}")
            self.logger.info(f"Quantity: {quantity}")
            self.logger.info(f"Reason: {reason}")

            # Step 1: Cancel associated OCO orders first
            oco_cancelled = False
            if position_id in self.oco_manager.active_oco_pairs:
                self.logger.info(f"🔄 CANCELLING OCO ORDERS FOR POSITION {position_id}")
                oco_cancelled = await self.oco_manager.cancel_oco_pair(position_id)
                if oco_cancelled:
                    self.logger.info("✅ OCO ORDERS CANCELLED SUCCESSFULLY")
                else:
                    self.logger.warning("⚠️  FAILED TO CANCEL OCO ORDERS")

            # Step 2: Close the position
            position_closed = False
            try:
                # Determine order side for closing (opposite of position)
                close_side = "sell" if position_side == "LONG" else "buy"

                # Create closing order
                close_order = TradeOrder(
                    symbol=symbol,
                    side=close_side,
                    type=OrderType.MARKET,
                    amount=quantity,
                    target_price=None,
                    stop_loss=None,
                    take_profit=None,
                    conditional_price=None,
                    conditional_direction=None,
                    conditional_timeout=None,
                    iceberg_quantity=None,
                    client_order_id=None,
                    order_id=f"close_{position_id}_{int(time.time())}",
                    status=OrderStatus.PENDING,
                    filled_amount=0.0,
                    average_price=None,
                    position_id=None,
                    position_side=position_side,
                    exchange="binance",
                    reduce_only=True,  # This is a position-closing order
                    time_in_force=TimeInForce.GTC,
                    position_size_pct=None,
                    simulate=False,
                    updated_at=None,
                )

                self.logger.info(
                    f"📤 CLOSING POSITION: {symbol} {close_side} {quantity}"
                )

                # Execute closing order
                if self.exchange:
                    close_result = await self.exchange.execute(close_order)
                else:
                    raise ValueError("Exchange not configured")

                if close_result.get("status") in ["NEW", "FILLED", "PARTIALLY_FILLED"]:
                    position_closed = True
                    self.logger.info("✅ POSITION CLOSED SUCCESSFULLY")
                    self.logger.info(f"  Order ID: {close_result.get('order_id')}")
                    self.logger.info(f"  Status: {close_result.get('status')}")
                else:
                    self.logger.error(f"❌ FAILED TO CLOSE POSITION: {close_result}")

            except Exception as e:
                self.logger.error(f"❌ ERROR CLOSING POSITION: {e}")

            # Step 3: Clean up position record
            try:
                if position_id:
                    await self.position_manager.close_position_record(
                        position_id, {"reason": reason, "manual_close": True}
                    )
                    self.logger.info("✅ POSITION RECORD UPDATED")
            except Exception as e:
                self.logger.error(f"❌ ERROR UPDATING POSITION RECORD: {e}")

            return {
                "position_closed": position_closed,
                "oco_cancelled": oco_cancelled,
                "close_result": close_result if position_closed else None,
                "status": "success" if position_closed else "failed",
            }

        except Exception as e:
            self.logger.error(
                f"❌ ERROR IN POSITION CLOSE WITH CLEANUP: {e}", exc_info=True
            )
            return {
                "position_closed": False,
                "oco_cancelled": False,
                "close_result": None,
                "status": "error",
                "error": str(e),
            }

    async def shutdown(self) -> None:
        """Shutdown dispatcher and stop OCO monitoring"""
        try:
            self.logger.info("🔄 SHUTTING DOWN DISPATCHER")

            # Stop OCO monitoring
            await self.oco_manager.stop_monitoring()

            self.logger.info("✅ DISPATCHER SHUTDOWN COMPLETE")

        except Exception as e:
            self.logger.error(f"❌ ERROR DURING SHUTDOWN: {e}")
