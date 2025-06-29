import logging
from typing import Dict, Any
from contracts.signal import Signal
from contracts.order import TradeOrder
from tradeengine.exchange.simulator import simulator
from shared.logger import audit_logger
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
            if trade_order.simulate:
                logger.info("Executing simulated trade")
                result = await simulator.execute(trade_order)
            else:
                logger.warning(
                    "Live trading not implemented, falling back to simulation"
                )
                trade_order.simulate = True
                result = await simulator.execute(trade_order)

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
        """Convert a trading signal to a trade order"""

        # Basic conversion logic - can be enhanced with more sophisticated rules
        order_type = "market"  # Default to market orders for now

        # Determine position size based on confidence (simple example)
        base_amount = 100.0  # Base position size
        confidence_multiplier = min(signal.confidence, 1.0)  # Cap at 100%
        amount = base_amount * confidence_multiplier

        # Set simulate flag based on meta data or default to True for safety
        simulate = signal.meta.get("simulate", True)

        return TradeOrder(
            type=order_type,
            side=signal.action,  # "buy" or "sell"
            amount=amount,
            target_price=signal.price,
            simulate=simulate,
        )


# Global dispatcher instance
dispatcher = TradeDispatcher()
