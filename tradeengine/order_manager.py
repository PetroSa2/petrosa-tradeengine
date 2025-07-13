"""
Order Manager - Tracks orders and manages conditional execution
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta

from contracts.order import TradeOrder
from shared.constants import (
    CONDITIONAL_ORDER_TIMEOUT,
    PRICE_MONITORING_INTERVAL
)
from shared.audit import audit_logger

logger = logging.getLogger(__name__)


class OrderManager:
    """Manages order tracking and conditional execution"""
    
    def __init__(self):
        self.active_orders: Dict[str, Dict[str, Any]] = {}
        self.conditional_orders: Dict[str, Dict[str, Any]] = {}
        self.order_history: List[Dict[str, Any]] = []
        self.price_cache: Dict[str, float] = {}
        self.last_price_update: Dict[str, datetime] = {}
    
    async def track_order(self, order: TradeOrder, result: Dict[str, Any]) -> None:
        """Track an executed order"""
        order_id = result.get("order_id", f"order_{int(datetime.utcnow().timestamp())}")
        
        order_info = {
            "order_id": order_id,
            "symbol": order.symbol,
            "side": order.side,
            "type": order.type,
            "quantity": order.amount,
            "price": order.target_price,
            "status": result.get("status", "unknown"),
            "timestamp": datetime.utcnow(),
            "result": result,
            "original_order": order.model_dump()
        }
        
        if result.get("status") in ["pending", "partial"]:
            self.active_orders[order_id] = order_info
        else:
            self.order_history.append(order_info)
        
        await audit_logger.log_order(order.model_dump(), result, status=result.get("status", "tracked"))

        # Handle conditional orders
        if order.type in ["conditional_limit", "conditional_stop"]:
            await self._setup_conditional_order(order, result)
    
    async def _setup_conditional_order(self, order: TradeOrder, result: Dict[str, Any]) -> None:
        """Setup conditional order monitoring"""
        order_id = result.get("order_id")
        
        conditional_info = {
            "order_id": order_id,
            "symbol": order.symbol,
            "side": order.side,
            "type": order.type,
            "quantity": order.amount,
            "price": order.target_price,
            "conditional_price": order.meta.get("conditional_price"),
            "conditional_direction": order.meta.get("conditional_direction"),
            "timeout": order.meta.get("conditional_timeout", CONDITIONAL_ORDER_TIMEOUT),
            "created_at": datetime.utcnow(),
            "status": "waiting_for_condition",
            "original_order": order.model_dump()
        }
        
        self.conditional_orders[order_id] = conditional_info
        await audit_logger.log_event("conditional_order_setup", conditional_info)
        
        # Start monitoring task
        asyncio.create_task(self._monitor_conditional_order(order_id))
        
        logger.info(f"Started monitoring conditional order {order_id} for {order.symbol}")
    
    async def _monitor_conditional_order(self, order_id: str) -> None:
        """Monitor conditional order for price conditions"""
        if order_id not in self.conditional_orders:
            return
        
        order_info = self.conditional_orders[order_id]
        timeout = order_info.get("timeout", CONDITIONAL_ORDER_TIMEOUT)
        
        start_time = datetime.utcnow()
        
        while datetime.utcnow() - start_time < timedelta(seconds=timeout):
            try:
                # Check current price
                current_price = await self._get_current_price(order_info["symbol"])
                
                # Check condition
                if self._check_condition(order_info, current_price):
                    # Execute conditional order
                    await self._execute_conditional_order(order_id)
                    break
                
                await asyncio.sleep(PRICE_MONITORING_INTERVAL)
                
            except Exception as e:
                logger.error(f"Error monitoring conditional order {order_id}: {e}")
                await audit_logger.log_error(str(e), context={"order_id": order_id, "order_info": order_info})
                break
        
        # Cleanup if timeout reached
        if order_id in self.conditional_orders:
            order_info = self.conditional_orders[order_id]
            order_info["status"] = "timeout"
            order_info["timeout_at"] = datetime.utcnow()
            self.order_history.append(order_info)
            del self.conditional_orders[order_id]
            await audit_logger.log_event("conditional_order_timeout", order_info)
            logger.info(f"Conditional order {order_id} timed out")
    
    def _check_condition(self, order_info: Dict[str, Any], current_price: float) -> bool:
        """Check if conditional order condition is met"""
        conditional_price = order_info.get("conditional_price")
        direction = order_info.get("conditional_direction")
        
        if not conditional_price or not direction:
            return False
        
        if direction == "above":
            return current_price >= conditional_price
        elif direction == "below":
            return current_price <= conditional_price
        
        return False
    
    async def _get_current_price(self, symbol: str) -> float:
        """Get current price for symbol"""
        # Check cache first
        if symbol in self.price_cache:
            last_update = self.last_price_update.get(symbol, datetime.min)
            if datetime.utcnow() - last_update < timedelta(seconds=30):
                return self.price_cache[symbol]
        
        # This would integrate with price feed
        # For now, simulate price movement
        base_price = 45000.0  # Default BTC price
        import random
        current_price = base_price + random.uniform(-1000, 1000)
        
        # Update cache
        self.price_cache[symbol] = current_price
        self.last_price_update[symbol] = datetime.utcnow()
        
        return current_price
    
    async def _execute_conditional_order(self, order_id: str) -> None:
        """Execute a conditional order when conditions are met"""
        if order_id not in self.conditional_orders:
            return
        
        order_info = self.conditional_orders[order_id]
        logger.info(f"Executing conditional order {order_id}")
        
        # This would integrate with the exchange to place the actual order
        # For now, just mark as executed
        order_info["status"] = "executed"
        order_info["executed_at"] = datetime.utcnow()
        order_info["execution_price"] = await self._get_current_price(order_info["symbol"])
        
        # Move to order history
        self.order_history.append(order_info)
        del self.conditional_orders[order_id]
        await audit_logger.log_event("conditional_order_executed", order_info)
        
        logger.info(f"Conditional order {order_id} executed successfully")
    
    def get_active_orders(self) -> List[Dict[str, Any]]:
        """Get all active orders"""
        return list(self.active_orders.values())
    
    def get_conditional_orders(self) -> List[Dict[str, Any]]:
        """Get all conditional orders"""
        return list(self.conditional_orders.values())
    
    def get_order_history(self) -> List[Dict[str, Any]]:
        """Get order history"""
        return self.order_history.copy()
    
    def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        """Get specific order"""
        # Check active orders
        if order_id in self.active_orders:
            return self.active_orders[order_id]
        
        # Check conditional orders
        if order_id in self.conditional_orders:
            return self.conditional_orders[order_id]
        
        # Check history
        for order in self.order_history:
            if order.get("order_id") == order_id:
                return order
        
        return None
    
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order"""
        # Check active orders
        if order_id in self.active_orders:
            order_info = self.active_orders[order_id]
            order_info["status"] = "cancelled"
            order_info["cancelled_at"] = datetime.utcnow()
            self.order_history.append(order_info)
            del self.active_orders[order_id]
            logger.info(f"Order {order_id} cancelled")
            return True
        
        # Check conditional orders
        if order_id in self.conditional_orders:
            order_info = self.conditional_orders[order_id]
            order_info["status"] = "cancelled"
            order_info["cancelled_at"] = datetime.utcnow()
            self.order_history.append(order_info)
            del self.conditional_orders[order_id]
            logger.info(f"Conditional order {order_id} cancelled")
            return True
        
        return False
    
    def get_order_summary(self) -> Dict[str, Any]:
        """Get order summary"""
        active_count = len(self.active_orders)
        conditional_count = len(self.conditional_orders)
        history_count = len(self.order_history)
        
        # Count by status
        status_counts = {}
        for order in self.order_history:
            status = order.get("status", "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1
        
        return {
            "active_orders": active_count,
            "conditional_orders": conditional_count,
            "total_orders": history_count,
            "status_distribution": status_counts
        }


# Global order manager instance
order_manager = OrderManager() 