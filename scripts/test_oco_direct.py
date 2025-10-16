#!/usr/bin/env python3
"""
Direct test of OCO functionality without signal processing

This script tests the OCO implementation directly without going through
the signal processing pipeline that has risk limits.
"""

import asyncio
import logging
import sys
import time

from tradeengine.dispatcher import Dispatcher
from tradeengine.exchange.binance import BinanceFuturesExchange

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def test_oco_direct():
    """Test OCO functionality directly"""

    try:
        # Initialize components
        exchange = BinanceFuturesExchange()
        dispatcher = Dispatcher(exchange)
        await dispatcher.initialize()

        logger.info(f"\n{'='*80}")
        logger.info("TESTING OCO IMPLEMENTATION DIRECTLY")
        logger.info(f"{'='*80}")

        # Test 1: Place OCO orders directly
        logger.info("\n📊 TEST 1: PLACING OCO ORDERS DIRECTLY")

        position_id = f"test_oco_direct_{int(time.time())}"
        symbol = "BTCUSDT"
        position_side = "LONG"
        quantity = 0.001
        current_price = 50000.0
        stop_loss_price = current_price * 0.98  # 2% stop loss
        take_profit_price = current_price * 1.04  # 4% take profit

        logger.info(f"Position ID: {position_id}")
        logger.info(f"Symbol: {symbol}")
        logger.info(f"Position Side: {position_side}")
        logger.info(f"Quantity: {quantity}")
        logger.info(f"Stop Loss: {stop_loss_price}")
        logger.info(f"Take Profit: {take_profit_price}")

        # Place OCO orders
        oco_result = await dispatcher.oco_manager.place_oco_orders(
            position_id=position_id,
            symbol=symbol,
            position_side=position_side,
            quantity=quantity,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
        )

        logger.info(f"OCO Result: {oco_result}")

        if oco_result["status"] == "success":
            logger.info("✅ OCO ORDERS PLACED SUCCESSFULLY")
            logger.info(f"  SL Order ID: {oco_result.get('sl_order_id')}")
            logger.info(f"  TP Order ID: {oco_result.get('tp_order_id')}")

            # Test 2: Check active OCO pairs
            logger.info("\n📊 TEST 2: CHECKING ACTIVE OCO PAIRS")

            active_pairs = dispatcher.oco_manager.active_oco_pairs
            logger.info(f"Active OCO pairs: {len(active_pairs)}")

            for pid, oco_info in active_pairs.items():
                logger.info(f"  Position ID: {pid}")
                logger.info(f"  Symbol: {oco_info['symbol']}")
                logger.info(f"  Position Side: {oco_info['position_side']}")
                logger.info(f"  SL Order ID: {oco_info['sl_order_id']}")
                logger.info(f"  TP Order ID: {oco_info['tp_order_id']}")
                logger.info(f"  Status: {oco_info['status']}")
                logger.info(f"  Created At: {oco_info['created_at']}")

            # Test 3: Check monitoring status
            logger.info("\n📊 TEST 3: CHECKING MONITORING STATUS")

            monitoring_active = dispatcher.oco_manager.monitoring_active
            logger.info(f"OCO Monitoring Active: {monitoring_active}")

            if monitoring_active:
                logger.info("✅ OCO MONITORING IS ACTIVE")

                # Let monitoring run for a few seconds
                logger.info("⏳ Letting monitoring run for 10 seconds...")
                await asyncio.sleep(10)

                # Check if orders are still active
                logger.info("\n📊 TEST 4: CHECKING ORDER STATUS AFTER MONITORING")

                try:
                    orders = exchange.client.futures_get_open_orders(symbol=symbol)
                    logger.info(f"Open orders for {symbol}: {len(orders)}")

                    for order in orders:
                        if order["orderId"] in [
                            oco_result.get("sl_order_id"),
                            oco_result.get("tp_order_id"),
                        ]:
                            logger.info(
                                f"  Order {order['orderId']}: {order['type']} - {order['status']}"
                            )

                except Exception as e:
                    logger.error(f"Error checking orders: {e}")

            else:
                logger.warning("⚠️  OCO MONITORING IS NOT ACTIVE")

            # Test 5: Test manual cancellation
            logger.info("\n📊 TEST 5: TESTING MANUAL OCO CANCELLATION")

            cancel_result = await dispatcher.oco_manager.cancel_oco_pair(position_id)

            if cancel_result:
                logger.info("✅ OCO PAIR CANCELLED SUCCESSFULLY")
            else:
                logger.error("❌ FAILED TO CANCEL OCO PAIR")

            # Check final status
            final_pairs = dispatcher.oco_manager.active_oco_pairs
            logger.info(f"Remaining OCO pairs: {len(final_pairs)}")

        else:
            logger.error(f"❌ OCO ORDERS FAILED: {oco_result}")

        logger.info(f"\n{'='*80}")
        logger.info("OCO DIRECT TEST COMPLETE")
        logger.info(f"{'='*80}")

        # Cleanup
        await dispatcher.shutdown()

        return True

    except Exception as e:
        logger.error(f"❌ Error in OCO direct test: {e}", exc_info=True)
        return False


async def main():
    """Main entry point"""
    success = await test_oco_direct()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
