#!/usr/bin/env python3
"""
Verify that SL/TP orders are now being placed successfully with the fix

This script opens a small test position and verifies that SL/TP orders appear on Binance.
"""

import asyncio
import json
import logging
import os
import sys

from binance import Client
from binance.enums import (
    FUTURE_ORDER_TYPE_MARKET,
    FUTURE_ORDER_TYPE_STOP_MARKET,
    FUTURE_ORDER_TYPE_TAKE_PROFIT_MARKET,
    SIDE_BUY,
    SIDE_SELL,
)
from binance.exceptions import BinanceAPIException

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def verify_fix():
    """Verify the SL/TP fix is working"""

    # Initialize Binance client
    api_key = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_API_SECRET")
    testnet = os.getenv("BINANCE_TESTNET", "false").lower() == "true"

    if not api_key or not api_secret:
        logger.error("BINANCE_API_KEY and BINANCE_API_SECRET must be set")
        return False

    if testnet:
        client = Client(api_key=api_key, api_secret=api_secret, testnet=True)
        logger.info("🔧 Using Binance TESTNET")
    else:
        client = Client(api_key=api_key, api_secret=api_secret)
        logger.info("⚠️  Using Binance PRODUCTION")

    symbol = "BTCUSDT"
    quantity = "0.001"
    position_side = "LONG"

    try:
        # Get current price
        ticker = client.futures_symbol_ticker(symbol=symbol)
        current_price = float(ticker["price"])
        logger.info(f"📊 Current {symbol} price: ${current_price:,.2f}")

        # Calculate SL/TP prices
        sl_price = f"{current_price * 0.98:.1f}"  # 2% below
        tp_price = f"{current_price * 1.02:.1f}"  # 2% above

        # 1. Open test position
        logger.info(f"\n{'='*80}")
        logger.info("📍 OPENING TEST POSITION")
        logger.info(f"{'='*80}")

        position_params = {
            "symbol": symbol,
            "side": SIDE_BUY,
            "type": FUTURE_ORDER_TYPE_MARKET,
            "quantity": quantity,
            "positionSide": position_side,
        }

        logger.info(f"Position params: {json.dumps(position_params, indent=2)}")
        position_result = client.futures_create_order(**position_params)
        logger.info(f"✅ Position opened: Order ID {position_result['orderId']}")

        # Wait a moment
        await asyncio.sleep(2)

        # 2. Place SL order (with FIX - no reduceOnly parameter)
        logger.info(f"\n{'='*80}")
        logger.info("📉 PLACING STOP LOSS (WITH FIX)")
        logger.info(f"{'='*80}")

        sl_params = {
            "symbol": symbol,
            "side": SIDE_SELL,
            "type": FUTURE_ORDER_TYPE_STOP_MARKET,
            "quantity": quantity,
            "stopPrice": sl_price,
            "positionSide": position_side,
            # NO reduceOnly parameter - this is the fix!
        }

        logger.info(f"SL params: {json.dumps(sl_params, indent=2)}")
        sl_result = client.futures_create_order(**sl_params)
        logger.info(
            f"✅ Stop Loss placed: Order ID {sl_result['orderId']}, Status: {sl_result['status']}"
        )

        # 3. Place TP order (with FIX - no reduceOnly parameter)
        logger.info(f"\n{'='*80}")
        logger.info("📈 PLACING TAKE PROFIT (WITH FIX)")
        logger.info(f"{'='*80}")

        tp_params = {
            "symbol": symbol,
            "side": SIDE_SELL,
            "type": FUTURE_ORDER_TYPE_TAKE_PROFIT_MARKET,
            "quantity": quantity,
            "stopPrice": tp_price,
            "positionSide": position_side,
            # NO reduceOnly parameter - this is the fix!
        }

        logger.info(f"TP params: {json.dumps(tp_params, indent=2)}")
        tp_result = client.futures_create_order(**tp_params)
        logger.info(
            f"✅ Take Profit placed: Order ID {tp_result['orderId']}, Status: {tp_result['status']}"
        )

        # 4. Verify orders exist
        await asyncio.sleep(2)

        logger.info(f"\n{'='*80}")
        logger.info("🔍 VERIFYING ORDERS ON BINANCE")
        logger.info(f"{'='*80}")

        open_orders = client.futures_get_open_orders(symbol=symbol)
        sl_orders = [o for o in open_orders if o["type"] == "STOP_MARKET"]
        tp_orders = [o for o in open_orders if o["type"] == "TAKE_PROFIT_MARKET"]

        logger.info(f"Found {len(sl_orders)} SL orders and {len(tp_orders)} TP orders")

        for order in open_orders:
            logger.info(
                f"  Order {order['orderId']}: {order['type']} {order['side']} "
                f"{order['origQty']} @ stop=${order.get('stopPrice', 'N/A')} "
                f"(positionSide={order.get('positionSide')})"
            )

        # 5. Cleanup
        logger.info(f"\n{'='*80}")
        logger.info("🧹 CLEANING UP")
        logger.info(f"{'='*80}")

        # Cancel all orders
        client.futures_cancel_all_open_orders(symbol=symbol)
        logger.info("Cancelled all open orders")

        # Close position
        close_params = {
            "symbol": symbol,
            "side": SIDE_SELL,
            "type": FUTURE_ORDER_TYPE_MARKET,
            "quantity": quantity,
            "positionSide": position_side,
        }
        client.futures_create_order(**close_params)
        logger.info("Closed test position")

        # Final verification
        logger.info(f"\n{'='*80}")
        logger.info("✅ FIX VERIFICATION RESULT")
        logger.info(f"{'='*80}")

        if len(sl_orders) > 0 and len(tp_orders) > 0:
            logger.info(
                "✅ SUCCESS: SL and TP orders were placed and verified on Binance!"
            )
            logger.info(f"   - Stop Loss orders: {len(sl_orders)}")
            logger.info(f"   - Take Profit orders: {len(tp_orders)}")
            return True
        else:
            logger.error("❌ FAILED: SL/TP orders were not found on Binance")
            logger.error(f"   - Stop Loss orders: {len(sl_orders)}")
            logger.error(f"   - Take Profit orders: {len(tp_orders)}")
            return False

    except BinanceAPIException as e:
        logger.error(f"❌ Binance API error: {e}")
        logger.error(f"   Error code: {e.code}")
        logger.error(f"   Error message: {e.message}")
        return False
    except Exception as e:
        logger.error(f"❌ Error: {e}", exc_info=True)
        return False


async def main():
    """Main entry point"""
    success = await verify_fix()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
