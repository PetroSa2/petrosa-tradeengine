#!/usr/bin/env python3
"""
Manually place SL/TP orders for existing positions to test the fix

This script will place SL/TP orders for the existing BTCUSDT positions
to verify that our fix works and show you the orders in Binance UI.
"""

import asyncio
import logging
import sys
from datetime import datetime

from contracts.order import OrderSide, OrderType, TradeOrder
from contracts.signal import TimeInForce
from tradeengine.exchange.binance import BinanceFuturesExchange

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def place_manual_sltp_orders():
    """Place SL/TP orders for existing positions"""

    try:
        # Initialize Binance exchange
        exchange = BinanceFuturesExchange()

        logger.info(f"\n{'='*80}")
        logger.info("MANUALLY PLACING SL/TP ORDERS FOR EXISTING POSITIONS")
        logger.info(f"{'='*80}")

        # Current market price (approximate)
        current_price = 107980.0

        # Place SL/TP for LONG position (0.002 BTCUSDT)
        logger.info("\n📍 PLACING SL/TP FOR LONG POSITION (0.002 BTCUSDT)")

        # Stop Loss for LONG position (2% below current price)
        sl_price_long = current_price * 0.98  # 2% stop loss
        sl_order_long = TradeOrder(
            symbol="BTCUSDT",
            side=OrderSide.SELL,
            type=OrderType.STOP,
            amount=0.002,  # Match the position size
            target_price=sl_price_long,
            stop_loss=sl_price_long,  # Set stop loss price
            position_side="LONG",  # Hedge mode
            time_in_force=TimeInForce.GTC,
            order_id=f"manual_sl_long_{datetime.now().timestamp()}",
            reduce_only=True,
            simulate=False,
        )

        logger.info(f"  📉 Stop Loss: {sl_price_long:.2f} (2% below current price)")

        try:
            sl_result_long = await exchange.execute(sl_order_long)
            logger.info(f"  ✅ Stop Loss Placed: {sl_result_long}")
        except Exception as e:
            logger.error(f"  ❌ Stop Loss Failed: {e}")

        # Take Profit for LONG position (4% above current price)
        tp_price_long = current_price * 1.04  # 4% take profit
        tp_order_long = TradeOrder(
            symbol="BTCUSDT",
            side=OrderSide.SELL,
            type=OrderType.TAKE_PROFIT,
            amount=0.002,  # Match the position size
            target_price=tp_price_long,
            take_profit=tp_price_long,  # Set take profit price
            position_side="LONG",  # Hedge mode
            time_in_force=TimeInForce.GTC,
            order_id=f"manual_tp_long_{datetime.now().timestamp()}",
            reduce_only=True,
            simulate=False,
        )

        logger.info(f"  📈 Take Profit: {tp_price_long:.2f} (4% above current price)")

        try:
            tp_result_long = await exchange.execute(tp_order_long)
            logger.info(f"  ✅ Take Profit Placed: {tp_result_long}")
        except Exception as e:
            logger.error(f"  ❌ Take Profit Failed: {e}")

        # Place SL/TP for SHORT position (-0.010 BTCUSDT)
        logger.info("\n📍 PLACING SL/TP FOR SHORT POSITION (-0.010 BTCUSDT)")

        # Stop Loss for SHORT position (2% above current price)
        sl_price_short = current_price * 1.02  # 2% stop loss for short
        sl_order_short = TradeOrder(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            type=OrderType.STOP,
            amount=0.010,  # Match the position size (absolute value)
            target_price=sl_price_short,
            stop_loss=sl_price_short,  # Set stop loss price
            position_side="SHORT",  # Hedge mode
            time_in_force=TimeInForce.GTC,
            order_id=f"manual_sl_short_{datetime.now().timestamp()}",
            reduce_only=True,
            simulate=False,
        )

        logger.info(f"  📉 Stop Loss: {sl_price_short:.2f} (2% above current price)")

        try:
            sl_result_short = await exchange.execute(sl_order_short)
            logger.info(f"  ✅ Stop Loss Placed: {sl_result_short}")
        except Exception as e:
            logger.error(f"  ❌ Stop Loss Failed: {e}")

        # Take Profit for SHORT position (4% below current price)
        tp_price_short = current_price * 0.96  # 4% take profit for short
        tp_order_short = TradeOrder(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            type=OrderType.TAKE_PROFIT,
            amount=0.010,  # Match the position size (absolute value)
            target_price=tp_price_short,
            take_profit=tp_price_short,  # Set take profit price
            position_side="SHORT",  # Hedge mode
            time_in_force=TimeInForce.GTC,
            order_id=f"manual_tp_short_{datetime.now().timestamp()}",
            reduce_only=True,
            simulate=False,
        )

        logger.info(f"  📈 Take Profit: {tp_price_short:.2f} (4% below current price)")

        try:
            tp_result_short = await exchange.execute(tp_order_short)
            logger.info(f"  ✅ Take Profit Placed: {tp_result_short}")
        except Exception as e:
            logger.error(f"  ❌ Take Profit Failed: {e}")

        logger.info(f"\n{'='*80}")
        logger.info("MANUAL SL/TP PLACEMENT COMPLETE")
        logger.info(f"{'='*80}")
        logger.info("✅ Check your Binance UI now!")
        logger.info("🎨 You should see SL/TP prices instead of -- / --")
        logger.info("📊 LONG position should show SL/TP around 105820 / 112300")
        logger.info("📊 SHORT position should show SL/TP around 110140 / 103660")

        return True

    except Exception as e:
        logger.error(f"❌ Error placing manual SL/TP orders: {e}", exc_info=True)
        return False


async def main():
    """Main entry point"""
    success = await place_manual_sltp_orders()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
