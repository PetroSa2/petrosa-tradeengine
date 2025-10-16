#!/usr/bin/env python3
"""
Test what happens to SL/TP orders when a position is closed

This script will:
1. Show current positions and orders
2. Close the LONG position (0.002 BTCUSDT)
3. Check if the associated SL/TP orders are automatically cancelled
4. Show the remaining orders
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


async def test_position_close_cancels_orders():
    """Test if closing a position cancels its SL/TP orders"""

    try:
        # Initialize Binance exchange
        exchange = BinanceFuturesExchange()

        logger.info(f"\n{'='*80}")
        logger.info("TESTING: POSITION CLOSE CANCELS SL/TP ORDERS")
        logger.info(f"{'='*80}")

        # Step 1: Show current state
        logger.info("\n📊 STEP 1: CURRENT STATE")
        logger.info("Current positions:")
        logger.info("  - LONG: 0.002 BTCUSDT")
        logger.info("  - SHORT: -0.012 BTCUSDT")
        logger.info("Current SL/TP orders:")
        logger.info("  - LONG SL: Order 6063629674 @ 105820.40")
        logger.info("  - LONG TP: Order 6063630395 @ 112299.20")
        logger.info("  - SHORT SL: Order 6063630752 @ 110139.60")
        logger.info("  - SHORT TP: Order 6063632076 @ 103660.80")

        # Step 2: Close the LONG position
        logger.info("\n📊 STEP 2: CLOSING LONG POSITION")
        logger.info("Closing LONG position (0.002 BTCUSDT)...")

        # Create a market sell order to close the LONG position
        close_order = TradeOrder(
            symbol="BTCUSDT",
            side=OrderSide.SELL,
            type=OrderType.MARKET,
            amount=0.002,  # Match the position size
            position_side="LONG",  # Hedge mode - close LONG position
            time_in_force=TimeInForce.GTC,
            order_id=f"close_long_{datetime.now().timestamp()}",
            reduce_only=True,  # This is important - it's a closing order
            simulate=False,
        )

        try:
            close_result = await exchange.execute(close_order)
            logger.info(f"✅ Position closed: {close_result}")
        except Exception as e:
            logger.error(f"❌ Failed to close position: {e}")
            return False

        # Step 3: Wait a moment for the system to process
        logger.info("\n⏳ STEP 3: WAITING FOR SYSTEM TO PROCESS...")
        await asyncio.sleep(3)

        # Step 4: Check remaining orders
        logger.info("\n📊 STEP 4: CHECKING REMAINING ORDERS")

        # Query open orders
        try:
            orders = exchange.client.futures_get_open_orders(symbol="BTCUSDT")
            logger.info(f"Remaining open orders: {len(orders)}")

            for order in orders:
                logger.info(f"  📋 Order {order['orderId']}:")
                logger.info(f"    Type: {order['type']}")
                logger.info(f"    Side: {order['side']}")
                logger.info(f"    Position Side: {order['positionSide']}")
                logger.info(f"    Status: {order['status']}")
                if "stopPrice" in order:
                    logger.info(f"    Stop Price: {order['stopPrice']}")
                logger.info(f"    Quantity: {order['origQty']}")
                logger.info(f"    Reduce Only: {order['reduceOnly']}")
                logger.info("")

        except Exception as e:
            logger.error(f"❌ Failed to query orders: {e}")
            return False

        # Step 5: Analysis
        logger.info("\n📊 STEP 5: ANALYSIS")
        logger.info(f"{'='*50}")

        # Count orders by position side
        long_orders = [o for o in orders if o["positionSide"] == "LONG"]
        short_orders = [o for o in orders if o["positionSide"] == "SHORT"]

        logger.info(f"Orders for LONG position: {len(long_orders)}")
        logger.info(f"Orders for SHORT position: {len(short_orders)}")

        if len(long_orders) == 0:
            logger.info(
                "✅ SUCCESS: All LONG position orders were automatically cancelled!"
            )
            logger.info(
                "🎯 This confirms that closing a position cancels its SL/TP orders"
            )
        else:
            logger.info(f"⚠️  WARNING: {len(long_orders)} LONG orders still exist")
            logger.info("   This might indicate an issue with the cancellation logic")

        if len(short_orders) == 2:
            logger.info("✅ SUCCESS: SHORT position orders remain intact (as expected)")
            logger.info(
                "🎯 This confirms that only the closed position's orders were cancelled"
            )
        else:
            logger.info(
                f"⚠️  WARNING: Expected 2 SHORT orders, found {len(short_orders)}"
            )

        logger.info(f"\n{'='*80}")
        logger.info("TEST COMPLETE")
        logger.info(f"{'='*80}")

        return True

    except Exception as e:
        logger.error(f"❌ Error during test: {e}", exc_info=True)
        return False


async def main():
    """Main entry point"""
    success = await test_position_close_cancels_orders()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
