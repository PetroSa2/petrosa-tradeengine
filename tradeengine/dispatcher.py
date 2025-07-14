import logging
from typing import Any

from contracts.order import OrderStatus, TradeOrder
from contracts.signal import Signal
from shared.audit import audit_logger
from shared.config import Settings
from shared.distributed_lock import distributed_lock_manager
from tradeengine.order_manager import OrderManager
from tradeengine.position_manager import PositionManager
from tradeengine.signal_aggregator import SignalAggregator


class Dispatcher:
    """Central dispatcher for trading operations with distributed state management"""

    def __init__(self) -> None:
        self.settings = Settings()
        self.order_manager = OrderManager()
        self.position_manager = PositionManager()
        self.signal_aggregator = SignalAggregator()
        self.logger = logging.getLogger(__name__)

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

    async def dispatch(self, signal: Signal) -> dict[str, Any]:
        """Dispatch a signal for processing with distributed state management"""
        try:
            # Handle hold signals
            if signal.action == "hold":
                return {"status": "hold", "reason": "Signal indicates hold action"}

            # Process the signal
            result = await self.process_signal(signal)

            # If processing was successful, execute the order with distributed lock
            if result.get("status") == "success":
                order = self._signal_to_order(signal)

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

            return result

        except Exception as e:
            self.logger.error(f"Dispatch error: {e}")
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

    async def execute_order(self, order: TradeOrder) -> dict[str, Any]:
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
