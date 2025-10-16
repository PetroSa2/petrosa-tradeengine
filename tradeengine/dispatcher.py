import logging
import time
from typing import Any

from prometheus_client import Counter, Histogram

from contracts.order import OrderStatus, TradeOrder
from contracts.signal import Signal
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


class Dispatcher:
    """Central dispatcher for trading operations with distributed state management"""

    def __init__(self, exchange: Any = None) -> None:
        self.settings = Settings()
        self.order_manager = OrderManager()
        self.position_manager = PositionManager()
        self.signal_aggregator = SignalAggregator()
        self.exchange = exchange
        self.logger = logging.getLogger(__name__)

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
        timestamp_second = signal.timestamp[:19] if signal.timestamp else ""
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
                result[
                    "status"
                ] = "executed"  # Change status to executed for consistency

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

            # Update position with distributed state management
            if result and result.get("status") in ["filled", "partially_filled"]:
                await self.position_manager.update_position(order, result)

            return result

        except Exception as e:
            self.logger.error(f"Order execution with consensus error: {e}")
            return {"status": "error", "error": str(e)}

    def _signal_to_order(self, signal: Signal) -> TradeOrder:
        """Convert a signal to a trade order with dynamic minimum amounts"""
        from datetime import datetime

        # Calculate order amount based on signal quantity or dynamic minimum
        amount = self._calculate_order_amount(signal)

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
                self.logger.info(f"🎭 SIMULATION MODE: Order {order.order_id} simulated")
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
