import logging
import time
from typing import Any, Dict

from prometheus_client import Counter, Histogram

from contracts.order import TradeOrder
from contracts.signal import Signal
from shared.constants import (
    DEFAULT_BASE_AMOUNT,
    DEFAULT_ORDER_TYPE,
    SIMULATION_ENABLED,
    STOP_LOSS_DEFAULT,
    TAKE_PROFIT_DEFAULT,
)
from shared.logger import audit_logger
from tradeengine.exchange.binance import binance_exchange
from tradeengine.exchange.simulator import simulator
from tradeengine.signal_aggregator import signal_aggregator
from tradeengine.position_manager import position_manager
from tradeengine.order_manager import order_manager

logger = logging.getLogger(__name__)

# Prometheus metrics
trades_total = Counter(
    "tradeengine_trades_total", "Total number of trades", ["status", "type"]
)
errors_total = Counter("tradeengine_errors_total", "Total number of errors", ["type"])
latency_seconds = Histogram("tradeengine_latency_seconds", "Trade execution latency")


class TradeDispatcher:
    """Enhanced dispatcher with signal aggregation and advanced order management"""
    
    def __init__(self) -> None:
        self.risk_enabled = True  # Enable risk management by default
    
    async def dispatch(self, signal: Signal) -> dict[str, Any]:
        """Convert signal to trade order and execute"""
        start_time = time.time()

        try:
            logger.info("Processing signal from strategy: %s", signal.strategy_id)
            # Log signal receipt
            await audit_logger.log_signal(signal.model_dump(), status="received")

            # Skip hold signals
            if signal.action == "hold":
                logger.info("Hold signal received, no action taken")
                await audit_logger.log_signal(signal.model_dump(), status="hold_skipped")
                return {"status": "hold", "message": "No action required"}

            # Process signal through aggregator
            aggregation_result = await signal_aggregator.process_signal(signal)
            await audit_logger.log_signal(signal.model_dump(), status=aggregation_result.get("status", "processed"), extra={"aggregation_result": aggregation_result})
            
            if aggregation_result["status"] != "executed":
                return aggregation_result

            # Convert to trade order
            trade_order = self._signal_to_order(signal, aggregation_result.get("order_params", {}))

            # Execute the order
            execution_result = await self.execute_order(trade_order)

            # Log to audit trail
            await audit_logger.log_order(trade_order.model_dump(), execution_result, status=execution_result.get("status", "executed"), extra={"signal": signal.model_dump()})

            # Update metrics
            trades_total.labels(
                status=execution_result.get("status", "unknown"), 
                type=trade_order.type
            ).inc()
            latency_seconds.observe(time.time() - start_time)

            logger.info("Trade dispatch completed successfully")
            return {
                "status": "success",
                "aggregation_result": aggregation_result,
                "execution_result": execution_result
            }

        except Exception as e:
            logger.error("Error dispatching trade: %s", str(e))
            errors_total.labels(type="dispatch").inc()
            await audit_logger.log_error(str(e), context={"signal": signal.model_dump()})
            return {"status": "error", "error": str(e), "timestamp": time.time()}

    async def execute_order(self, order: TradeOrder) -> dict[str, Any]:
        """Execute a trade order with enhanced features"""
        start_time = time.time()
        
        try:
            logger.info(f"Executing {order.type} {order.side} order for {order.symbol}")
            
            # Fail-safe: Only allow simulation if audit logger is not connected
            if not audit_logger.enabled or not audit_logger.connected:
                logger.error("Audit logging unavailable, refusing real trade execution. Only simulation allowed.")
                order.simulate = True

            # Validate order
            await self._validate_order(order)
            
            # Check risk limits
            if not await self._check_risk_limits(order):
                await audit_logger.log_order(order.model_dump(), {"status": "rejected", "reason": "Risk limits exceeded"}, status="rejected")
                return {"status": "rejected", "reason": "Risk limits exceeded"}
            
            # Execute based on order type
            if order.type == "market":
                result = await self._execute_market_order(order)
            elif order.type == "limit":
                result = await self._execute_limit_order(order)
            elif order.type == "stop":
                result = await self._execute_stop_order(order)
            elif order.type == "stop_limit":
                result = await self._execute_stop_limit_order(order)
            elif order.type == "take_profit":
                result = await self._execute_take_profit_order(order)
            elif order.type == "take_profit_limit":
                result = await self._execute_take_profit_limit_order(order)
            elif order.type == "conditional_limit":
                result = await self._execute_conditional_limit_order(order)
            elif order.type == "conditional_stop":
                result = await self._execute_conditional_stop_order(order)
            else:
                raise ValueError(f"Unsupported order type: {order.type}")
            
            # Update position tracking
            await position_manager.update_position(order, result)
            
            # Track order
            await order_manager.track_order(order, result)
            
            # Log to audit trail
            await audit_logger.log_order(order.model_dump(), result, status=result.get("status", "executed"))
            
            # Update metrics
            trades_total.labels(
                status=result.get("status", "unknown"), 
                type=order.type
            ).inc()
            latency_seconds.observe(time.time() - start_time)
            
            logger.info("Order execution completed successfully")
            return result
            
        except Exception as e:
            logger.error(f"Error executing order: {e}")
            errors_total.labels(type="execution").inc()
            await audit_logger.log_error(str(e), context={"order": order.model_dump()})
            return {"status": "error", "error": str(e), "timestamp": time.time()}

    def _signal_to_order(self, signal: Signal, order_params: Dict[str, Any] = {}) -> TradeOrder:
        """Convert a trading signal to a trade order with advanced order types"""

        # Use order params from aggregator if available
        order_type = order_params.get("type", signal.order_type.value)
        time_in_force = order_params.get("time_in_force", signal.time_in_force.value)
        position_size_pct = order_params.get("position_size_pct", signal.position_size_pct)

        # Determine position size based on confidence
        base_amount = signal.meta.get("base_amount", DEFAULT_BASE_AMOUNT)
        confidence_multiplier = min(signal.confidence, 1.0)  # Cap at 100%
        amount = base_amount * confidence_multiplier

        # Set simulate flag based on meta data or global setting
        simulate = signal.meta.get("simulate", SIMULATION_ENABLED)

        # Calculate stop loss and take profit if not provided
        stop_loss = signal.stop_loss or signal.meta.get("stop_loss")
        take_profit = signal.take_profit or signal.meta.get("take_profit")

        if not stop_loss and signal.meta.get("use_default_stop_loss", True):
            # Calculate stop loss based on signal price and default percentage
            if signal.action == "buy":
                stop_loss = signal.current_price * (1 - STOP_LOSS_DEFAULT / 100)
            else:
                stop_loss = signal.current_price * (1 + STOP_LOSS_DEFAULT / 100)

        if not take_profit and signal.meta.get("use_default_take_profit", True):
            # Calculate take profit based on signal price and default percentage
            if signal.action == "buy":
                take_profit = signal.current_price * (1 + TAKE_PROFIT_DEFAULT / 100)
            else:
                take_profit = signal.current_price * (1 - TAKE_PROFIT_DEFAULT / 100)

        # Determine target price based on order type
        target_price = signal.target_price or signal.current_price

        # Create the trade order
        trade_order = TradeOrder(
            symbol=signal.symbol,
            type=order_type,
            side=signal.action,  # type: ignore # We handle "hold" earlier
            amount=amount,
            target_price=target_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            time_in_force=time_in_force,
            quote_quantity=signal.quote_quantity,
            simulate=simulate,
            strategy_id=signal.strategy_id,
            signal_id=signal.signal_id or signal.strategy_id,
            meta={
                **signal.meta,
                "conditional_price": signal.conditional_price,
                "conditional_direction": signal.conditional_direction,
                "conditional_timeout": signal.conditional_timeout,
                "iceberg_quantity": signal.iceberg_quantity,
                "client_order_id": signal.client_order_id,
            }
        )

        logger.info(
            "Converted signal to %s %s order: %s %s @ %s (SL: %s, TP: %s)",
            order_type,
            signal.action,
            amount,
            signal.symbol,
            target_price or "market",
            stop_loss,
            take_profit,
        )

        return trade_order

    async def _validate_order(self, order: TradeOrder) -> None:
        """Validate order parameters"""
        # Basic validation
        if order.amount <= 0:
            raise ValueError("Order amount must be positive")
        
        if order.side not in ["buy", "sell"]:
            raise ValueError(f"Invalid order side: {order.side}")
        
        # Validate price for limit orders
        if order.type in ["limit", "stop_limit", "take_profit_limit"]:
            if order.target_price is None:
                raise ValueError("Target price required for limit orders")

        # Validate stop price for stop orders
        if order.type in ["stop", "stop_limit"]:
            if order.stop_loss is None:
                raise ValueError("Stop loss price required for stop orders")

    async def _check_risk_limits(self, order: TradeOrder) -> bool:
        """Check risk management limits"""
        # Check position size limits
        if not await position_manager.check_position_limits(order):
            return False
        
        # Check daily loss limits
        if not await position_manager.check_daily_loss_limits():
            return False
        
        return True

    async def _execute_market_order(self, order: TradeOrder) -> dict[str, Any]:
        """Execute a market order"""
        if SIMULATION_ENABLED or order.simulate:
            return await simulator.execute(order)
        else:
            return await binance_exchange.execute(order)

    async def _execute_limit_order(self, order: TradeOrder) -> dict[str, Any]:
        """Execute a limit order"""
        if SIMULATION_ENABLED or order.simulate:
            return await simulator.execute(order)
        else:
            return await binance_exchange.execute(order)

    async def _execute_stop_order(self, order: TradeOrder) -> dict[str, Any]:
        """Execute a stop order"""
        if SIMULATION_ENABLED or order.simulate:
            return await simulator.execute(order)
        else:
            return await binance_exchange.execute(order)

    async def _execute_stop_limit_order(self, order: TradeOrder) -> dict[str, Any]:
        """Execute a stop limit order"""
        if SIMULATION_ENABLED or order.simulate:
            return await simulator.execute(order)
        else:
            return await binance_exchange.execute(order)

    async def _execute_take_profit_order(self, order: TradeOrder) -> dict[str, Any]:
        """Execute a take profit order"""
        if SIMULATION_ENABLED or order.simulate:
            return await simulator.execute(order)
        else:
            return await binance_exchange.execute(order)

    async def _execute_take_profit_limit_order(self, order: TradeOrder) -> dict[str, Any]:
        """Execute a take profit limit order"""
        if SIMULATION_ENABLED or order.simulate:
            return await simulator.execute(order)
        else:
            return await binance_exchange.execute(order)

    async def _execute_conditional_limit_order(self, order: TradeOrder) -> dict[str, Any]:
        """Execute a conditional limit order (if price crosses level)"""
        # This would implement conditional order logic
        # For now, return a placeholder
        return {
            "order_id": f"cond_{int(time.time())}",
            "status": "pending",
            "type": "conditional_limit",
            "symbol": order.symbol,
            "side": order.side,
            "quantity": order.amount,
            "price": order.target_price,
            "conditional_price": order.meta.get("conditional_price"),
            "conditional_direction": order.meta.get("conditional_direction"),
            "timestamp": int(time.time() * 1000),
            "simulated": SIMULATION_ENABLED or order.simulate
        }

    async def _execute_conditional_stop_order(self, order: TradeOrder) -> dict[str, Any]:
        """Execute a conditional stop order"""
        return {
            "order_id": f"cond_stop_{int(time.time())}",
            "status": "pending",
            "type": "conditional_stop",
            "symbol": order.symbol,
            "side": order.side,
            "quantity": order.amount,
            "stop_price": order.stop_loss,
            "conditional_price": order.meta.get("conditional_price"),
            "conditional_direction": order.meta.get("conditional_direction"),
            "timestamp": int(time.time() * 1000),
            "simulated": SIMULATION_ENABLED or order.simulate
        }

    async def get_account_info(self) -> dict[str, Any]:
        """Get detailed account information from Binance"""
        try:
            if not SIMULATION_ENABLED:
                account_data = await binance_exchange.get_account_info()
                
                # Add additional computed fields
                balances = account_data.get("balances", [])
                total_balance_count = len(balances)
                non_zero_balances = [b for b in balances if float(b.get("free", 0)) + float(b.get("locked", 0)) > 0]
                
                # Add summary statistics
                account_data["summary"] = {
                    "total_assets": total_balance_count,
                    "active_assets": len(non_zero_balances),
                    "last_updated": int(time.time() * 1000)
                }
                
                return account_data
            else:
                return {
                    "simulated": True,
                    "message": "Account info not available in simulation mode",
                    "summary": {
                        "total_assets": 0,
                        "active_assets": 0,
                        "last_updated": int(time.time() * 1000)
                    },
                    "balances": [],
                    "can_trade": False,
                    "can_withdraw": False,
                    "can_deposit": False,
                    "maker_commission": 0,
                    "taker_commission": 0,
                    "buyer_commission": 0,
                    "seller_commission": 0
                }
        except Exception as e:
            logger.error("Error getting account info: %s", str(e))
            return {"error": str(e)}

    async def get_symbol_price(self, symbol: str) -> float:
        """Get current symbol price"""
        try:
            if not SIMULATION_ENABLED:
                return await binance_exchange.get_symbol_price(symbol)
            else:
                # Return a simulated price for testing
                return 45000.0  # Default BTC price for simulation
        except Exception as e:
            logger.error("Error getting symbol price: %s", str(e))
            raise

    async def cancel_order(self, symbol: str, order_id: int) -> dict[str, Any]:
        """Cancel an existing order"""
        try:
            if not SIMULATION_ENABLED:
                return await binance_exchange.cancel_order(symbol, order_id)
            else:
                return {
                    "simulated": True,
                    "message": "Order cancellation not available in simulation mode",
                }
        except Exception as e:
            logger.error("Error canceling order: %s", str(e))
            return {"error": str(e)}

    async def get_order_status(self, symbol: str, order_id: int) -> dict[str, Any]:
        """Get order status"""
        try:
            if not SIMULATION_ENABLED:
                return await binance_exchange.get_order_status(symbol, order_id)
            else:
                return {
                    "simulated": True,
                    "message": "Order status not available in simulation mode",
                }
        except Exception as e:
            logger.error("Error getting order status: %s", str(e))
            return {"error": str(e)}


# Global dispatcher instance
dispatcher = TradeDispatcher()
