import logging
from typing import Any

from contracts.order import OrderStatus, TradeOrder
from contracts.signal import Signal
from shared.audit import audit_logger
from shared.config import Settings
from tradeengine.order_manager import OrderManager
from tradeengine.position_manager import PositionManager
from tradeengine.signal_aggregator import SignalAggregator


class Dispatcher:
    """Central dispatcher for trading operations"""

    def __init__(self) -> None:
        self.settings = Settings()
        self.order_manager = OrderManager()
        self.position_manager = PositionManager()
        self.signal_aggregator = SignalAggregator()
        self.logger = logging.getLogger(__name__)

    async def initialize(self) -> None:
        """Initialize dispatcher components"""
        try:
            # Initialize components
            await self.order_manager.initialize()
            await self.position_manager.initialize()
            self.logger.info("Dispatcher initialized successfully")
        except Exception as e:
            self.logger.error(f"Dispatcher initialization error: {e}")
            raise

    async def close(self) -> None:
        """Close dispatcher components"""
        try:
            await self.order_manager.close()
            await self.position_manager.close()
            self.logger.info("Dispatcher closed successfully")
        except Exception as e:
            self.logger.error(f"Dispatcher close error: {e}")

    async def health_check(self) -> dict[str, Any]:
        """Check dispatcher health"""
        try:
            # Check if components have health_check methods
            order_manager_health = {"status": "unknown"}
            position_manager_health = {"status": "unknown"}

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

            return {
                "status": "healthy",
                "components": {
                    "order_manager": order_manager_health,
                    "position_manager": position_manager_health,
                    "signal_aggregator": "active",
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
        """Process a trading signal"""
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

    async def dispatch(self, signal: Signal) -> dict[str, Any]:
        """Dispatch a signal for processing"""
        try:
            # Handle hold signals
            if signal.action == "hold":
                return {"status": "hold", "reason": "Signal indicates hold action"}

            # Process the signal
            result = await self.process_signal(signal)

            # If processing was successful, execute the order
            if result.get("status") == "success":
                order = self._signal_to_order(signal)
                execution_result = await self.execute_order(order)
                result["execution_result"] = execution_result
                result[
                    "status"
                ] = "executed"  # Change status to executed for consistency

            return result

        except Exception as e:
            self.logger.error(f"Dispatch error: {e}")
            return {"status": "error", "error": str(e)}

    def _signal_to_order(self, signal: Signal) -> TradeOrder:
        """Convert a signal to a trade order"""
        from datetime import datetime

        # Calculate order amount based on position size percentage
        amount = 0.001  # Default to 0.001 BTC
        if signal.position_size_pct:
            # This would need account balance to calculate actual amount
            # For now, use a fixed amount
            amount = 0.001

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
            filled_amount=0.0,
            average_price=0.0,
            time_in_force=signal.time_in_force.value,
            position_size_pct=signal.position_size_pct,
            created_at=signal.timestamp,
            updated_at=signal.timestamp,
            simulate=signal.meta.get("simulate", False) if signal.meta else False,
        )

        return order

    async def execute_order(self, order: TradeOrder) -> dict[str, Any] | None:
        """Execute a trading order"""
        try:
            # Log order
            if audit_logger.enabled and audit_logger.connected:
                audit_logger.log_order(order.model_dump())

            # Execute order
            await self.order_manager.track_order(order, {"status": "pending"})

            # For now, assume order is pending
            result = {"status": "pending"}

            # Log result
            if audit_logger.enabled and audit_logger.connected:
                audit_logger.log_order(
                    {
                        "order": order.model_dump(),
                        "result": result,
                    }
                )

            return result or {"status": "pending"}

        except Exception as e:
            self.logger.error(f"Order execution error: {e}")
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
        """Get active orders"""
        return self.order_manager.get_active_orders()

    def get_conditional_orders(self) -> list[dict[str, Any]]:
        """Get conditional orders"""
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
            # Get account info from order manager
            account_info = await self.order_manager.get_account_info()
            return account_info
        except Exception as e:
            self.logger.error(f"Account info error: {e}")
            return {"error": str(e)}

    async def get_price(self, symbol: str) -> float:
        """Get current price for a symbol"""
        try:
            # Get price from order manager
            price = await self.order_manager.get_price(symbol)
            return price
        except Exception as e:
            self.logger.error(f"Price check error: {e}")
            return 0.0

    def get_metrics(self) -> dict[str, Any]:
        """Get dispatcher metrics"""
        try:
            order_metrics = self.order_manager.get_metrics()
            position_metrics = self.position_manager.get_metrics()
            signal_metrics = self.get_signal_summary()

            return {
                "orders": order_metrics,
                "positions": position_metrics,
                "signals": signal_metrics,
            }
        except Exception as e:
            self.logger.error(f"Metrics error: {e}")
            return {"error": str(e)}
