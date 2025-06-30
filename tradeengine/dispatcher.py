import logging
from typing import Dict, Any
from contracts.signal import Signal
from contracts.order import TradeOrder
from tradeengine.exchange.simulator import simulator
from tradeengine.exchange.binance import binance_exchange
from shared.logger import audit_logger
from shared.constants import (
    SIMULATION_ENABLED, DEFAULT_BASE_AMOUNT, DEFAULT_ORDER_TYPE,
    STOP_LOSS_DEFAULT, TAKE_PROFIT_DEFAULT, SUPPORTED_SYMBOLS
)
from prometheus_client import Counter, Histogram
import time

logger = logging.getLogger(__name__)

# Prometheus metrics
trades_total = Counter(
    "tradeengine_trades_total", "Total number of trades", ["status", "type"]
)
errors_total = Counter("tradeengine_errors_total", "Total number of errors", ["type"])
latency_seconds = Histogram("tradeengine_latency_seconds", "Trade execution latency")


class TradeDispatcher:
    """Converts signals to trade orders and dispatches to execution engines"""

    def __init__(self):
        self.risk_enabled = False  # Placeholder for future risk management

    async def dispatch(self, signal: Signal) -> Dict[str, Any]:
        """Convert signal to trade order and execute"""
        start_time = time.time()

        try:
            logger.info("Processing signal from strategy: %s", signal.strategy_id)

            # Skip hold signals
            if signal.action == "hold":
                logger.info("Hold signal received, no action taken")
                return {"status": "hold", "message": "No action required"}

            # Convert signal to trade order
            trade_order = self._signal_to_order(signal)

            # Execute the order
            if trade_order.simulate or SIMULATION_ENABLED:
                logger.info("Executing simulated trade")
                result = await simulator.execute(trade_order)
            else:
                logger.info("Executing live trade on Binance")
                result = await binance_exchange.execute(trade_order)

            # Log to audit trail
            await audit_logger.log_trade(
                order=trade_order.model_dump(), result=result, signal_meta=signal.meta
            )

            # Update metrics
            trades_total.labels(
                status=result.get("status", "unknown"), type=trade_order.type
            ).inc()
            latency_seconds.observe(time.time() - start_time)

            logger.info("Trade dispatch completed successfully")
            return result

        except Exception as e:
            logger.error("Error dispatching trade: %s", str(e))
            errors_total.labels(type="dispatch").inc()
            return {"status": "error", "error": str(e), "timestamp": time.time()}

    def _signal_to_order(self, signal: Signal) -> TradeOrder:
        """Convert a trading signal to a trade order with advanced order types"""

        # Determine order type based on signal meta or default
        order_type = signal.meta.get("order_type", DEFAULT_ORDER_TYPE)
        
        # Determine position size based on confidence
        base_amount = signal.meta.get("base_amount", DEFAULT_BASE_AMOUNT)
        confidence_multiplier = min(signal.confidence, 1.0)  # Cap at 100%
        amount = base_amount * confidence_multiplier

        # Set simulate flag based on meta data or global setting
        simulate = signal.meta.get("simulate", SIMULATION_ENABLED)

        # Calculate stop loss and take profit if not provided
        stop_loss = signal.meta.get("stop_loss")
        take_profit = signal.meta.get("take_profit")
        
        if not stop_loss and signal.meta.get("use_default_stop_loss", True):
            # Calculate stop loss based on signal price and default percentage
            if signal.action == "buy":
                stop_loss = signal.price * (1 - STOP_LOSS_DEFAULT / 100)
            else:
                stop_loss = signal.price * (1 + STOP_LOSS_DEFAULT / 100)
        
        if not take_profit and signal.meta.get("use_default_take_profit", True):
            # Calculate take profit based on signal price and default percentage
            if signal.action == "buy":
                take_profit = signal.price * (1 + TAKE_PROFIT_DEFAULT / 100)
            else:
                take_profit = signal.price * (1 - TAKE_PROFIT_DEFAULT / 100)

        # Determine target price based on order type
        target_price = None
        if order_type in ["limit", "stop_limit", "take_profit_limit"]:
            target_price = signal.price

        # Create the trade order
        trade_order = TradeOrder(
            symbol=signal.symbol,
            type=order_type,
            side=signal.action,  # "buy" or "sell"
            amount=amount,
            target_price=target_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            time_in_force=signal.meta.get("time_in_force", "GTC"),
            quote_quantity=signal.meta.get("quote_quantity"),
            simulate=simulate,
            strategy_id=signal.strategy_id,
            signal_id=signal.strategy_id,  # Using strategy_id as signal_id for now
            meta=signal.meta
        )

        logger.info(
            "Converted signal to %s %s order: %s %s @ %s (SL: %s, TP: %s)",
            order_type,
            signal.action,
            amount,
            signal.symbol,
            target_price or "market",
            stop_loss,
            take_profit
        )

        return trade_order

    async def get_account_info(self) -> Dict[str, Any]:
        """Get account information from Binance"""
        try:
            if not SIMULATION_ENABLED:
                return await binance_exchange.get_account_info()
            else:
                return {
                    "simulated": True,
                    "message": "Account info not available in simulation mode"
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

    async def cancel_order(self, symbol: str, order_id: int) -> Dict[str, Any]:
        """Cancel an existing order"""
        try:
            if not SIMULATION_ENABLED:
                return await binance_exchange.cancel_order(symbol, order_id)
            else:
                return {
                    "simulated": True,
                    "message": "Order cancellation not available in simulation mode"
                }
        except Exception as e:
            logger.error("Error canceling order: %s", str(e))
            return {"error": str(e)}

    async def get_order_status(self, symbol: str, order_id: int) -> Dict[str, Any]:
        """Get order status"""
        try:
            if not SIMULATION_ENABLED:
                return await binance_exchange.get_order_status(symbol, order_id)
            else:
                return {
                    "simulated": True,
                    "message": "Order status not available in simulation mode"
                }
        except Exception as e:
            logger.error("Error getting order status: %s", str(e))
            return {"error": str(e)}


# Global dispatcher instance
dispatcher = TradeDispatcher()
