#!/usr/bin/env python3
"""
Implementation of OCO (One-Cancels-the-Other) logic for Binance Futures

This script demonstrates how to implement proper OCO functionality for SL/TP orders
since Binance Futures doesn't have native OCO support.
"""

import asyncio
import logging
import sys
from datetime import datetime
from typing import Dict

from contracts.order import OrderSide, OrderType, TradeOrder
from contracts.signal import TimeInForce
from tradeengine.exchange.binance import BinanceFuturesExchange

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class OCOManager:
    """Manages OCO (One-Cancels-the-Other) logic for SL/TP orders"""

    def __init__(self, exchange: BinanceFuturesExchange):
        self.exchange = exchange
        self.active_oco_pairs: Dict[str, Dict[str, str]] = (
            {}
        )  # position_id -> {sl_order_id, tp_order_id}

    async def place_oco_orders(
        self,
        position_id: str,
        symbol: str,
        position_side: str,
        quantity: float,
        stop_loss_price: float,
        take_profit_price: float,
    ) -> Dict[str, str]:
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

        logger.info(f"\n🔄 PLACING OCO ORDERS FOR {symbol} {position_side}")
        logger.info(f"Position ID: {position_id}")
        logger.info(f"Quantity: {quantity}")
        logger.info(f"Stop Loss: {stop_loss_price}")
        logger.info(f"Take Profit: {take_profit_price}")

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
            position_side=position_side,
            time_in_force=TimeInForce.GTC,
            order_id=f"oco_sl_{position_id}_{datetime.now().timestamp()}",
            reduce_only=True,
            simulate=False,
        )

        # Place Take Profit order
        tp_order = TradeOrder(
            symbol=symbol,
            side=tp_side,
            type=OrderType.TAKE_PROFIT,
            amount=quantity,
            target_price=take_profit_price,
            take_profit=take_profit_price,
            position_side=position_side,
            time_in_force=TimeInForce.GTC,
            order_id=f"oco_tp_{position_id}_{datetime.now().timestamp()}",
            reduce_only=True,
            simulate=False,
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
                }

                logger.info("✅ OCO ORDERS PLACED SUCCESSFULLY")
                logger.info(f"  Stop Loss Order ID: {sl_order_id}")
                logger.info(f"  Take Profit Order ID: {tp_order_id}")

                return {
                    "sl_order_id": sl_order_id,
                    "tp_order_id": tp_order_id,
                    "status": "success",
                }
            else:
                logger.error("❌ FAILED TO PLACE OCO ORDERS")
                logger.error(f"  SL Result: {sl_result}")
                logger.error(f"  TP Result: {tp_result}")
                return {"status": "failed"}

        except Exception as e:
            logger.error(f"❌ ERROR PLACING OCO ORDERS: {e}")
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
            logger.warning(f"⚠️  No OCO pair found for position {position_id}")
            return False

        oco_info = self.active_oco_pairs[position_id]
        sl_order_id = oco_info["sl_order_id"]
        tp_order_id = oco_info["tp_order_id"]
        symbol = oco_info["symbol"]

        logger.info(f"\n🔄 CANCELLING OCO PAIR FOR {symbol}")
        logger.info(f"Position ID: {position_id}")
        logger.info(f"SL Order ID: {sl_order_id}")
        logger.info(f"TP Order ID: {tp_order_id}")

        try:
            # Cancel both orders using batch cancellation
            cancel_result = self.exchange.client.futures_cancel_batch_orders(
                symbol=symbol, orderIdList=[sl_order_id, tp_order_id]
            )

            if cancel_result and len(cancel_result) >= 2:
                logger.info("✅ OCO PAIR CANCELLED SUCCESSFULLY")
                self.active_oco_pairs[position_id]["status"] = "cancelled"
                return True
            else:
                logger.error(f"❌ FAILED TO CANCEL OCO PAIR: {cancel_result}")
                return False

        except Exception as e:
            logger.error(f"❌ ERROR CANCELLING OCO PAIR: {e}")
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
            logger.warning(f"⚠️  No OCO pair found for position {position_id}")
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
            logger.warning(f"⚠️  Filled order {filled_order_id} not found in OCO pair")
            return False

        logger.info(f"\n🔄 OCO TRIGGERED: {filled_type} FILLED")
        logger.info(f"Position ID: {position_id}")
        logger.info(f"Filled Order: {filled_order_id} ({filled_type})")
        logger.info(f"Cancelling Order: {order_to_cancel} ({cancel_type})")

        try:
            # Cancel the other order
            cancel_result = self.exchange.client.futures_cancel_order(
                symbol=oco_info["symbol"], orderId=order_to_cancel
            )

            if cancel_result:
                logger.info(f"✅ {cancel_type} ORDER CANCELLED SUCCESSFULLY")
                self.active_oco_pairs[position_id]["status"] = "completed"
                return True
            else:
                logger.error(f"❌ FAILED TO CANCEL {cancel_type} ORDER")
                return False

        except Exception as e:
            logger.error(f"❌ ERROR CANCELLING {cancel_type} ORDER: {e}")
            return False

    async def monitor_orders(self) -> None:
        """
        Monitor active orders for fills and trigger OCO logic
        This should run in a separate task/thread
        """

        logger.info("\n🔍 STARTING ORDER MONITORING")
        logger.info(f"Active OCO pairs: {len(self.active_oco_pairs)}")

        while self.active_oco_pairs:
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
                        logger.info(f"✅ OCO COMPLETED FOR POSITION {position_id}")
                        self.active_oco_pairs[position_id]["status"] = "completed"

                # Remove completed pairs
                self.active_oco_pairs = {
                    pid: info
                    for pid, info in self.active_oco_pairs.items()
                    if info["status"] == "active"
                }

                # Wait before next check
                await asyncio.sleep(1)  # Check every second

            except Exception as e:
                logger.error(f"❌ ERROR IN ORDER MONITORING: {e}")
                await asyncio.sleep(5)  # Wait longer on error


async def demonstrate_oco_implementation():
    """Demonstrate the OCO implementation"""

    try:
        # Initialize exchange and OCO manager
        exchange = BinanceFuturesExchange()
        oco_manager = OCOManager(exchange)

        logger.info(f"\n{'='*80}")
        logger.info("OCO (ONE-CANCELLS-THE-OTHER) IMPLEMENTATION DEMO")
        logger.info(f"{'='*80}")

        # Example: Place OCO orders for the existing SHORT position
        position_id = "demo_short_position"
        symbol = "BTCUSDT"
        position_side = "SHORT"
        quantity = 0.010  # Match the existing SHORT position size
        current_price = 107880.0

        # Calculate SL/TP prices (example: 2% SL, 4% TP)
        stop_loss_price = current_price * 1.02  # 2% above for SHORT
        take_profit_price = current_price * 0.96  # 4% below for SHORT

        # Place OCO orders
        result = await oco_manager.place_oco_orders(
            position_id=position_id,
            symbol=symbol,
            position_side=position_side,
            quantity=quantity,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
        )

        if result["status"] == "success":
            logger.info("\n✅ OCO ORDERS PLACED SUCCESSFULLY")
            logger.info("Now monitoring for fills...")

            # Start monitoring (this would run in background in real implementation)
            # For demo, we'll just show the structure
            logger.info("\n📊 OCO MONITORING STRUCTURE:")
            logger.info("  - Monitor order fills via WebSocket or polling")
            logger.info("  - When one order fills, cancel the other")
            logger.info("  - Clean up completed OCO pairs")

        else:
            logger.error(f"❌ FAILED TO PLACE OCO ORDERS: {result}")

        logger.info(f"\n{'='*80}")
        logger.info("OCO IMPLEMENTATION DEMO COMPLETE")
        logger.info(f"{'='*80}")

        return True

    except Exception as e:
        logger.error(f"❌ Error in OCO demonstration: {e}", exc_info=True)
        return False


async def main():
    """Main entry point"""
    success = await demonstrate_oco_implementation()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
