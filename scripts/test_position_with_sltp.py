#!/usr/bin/env python3
"""
Test opening a position with SL/TP using the FIXED code

This script:
1. Opens a small LONG position
2. Places SL and TP orders using our FIXED approach (no reduceOnly in hedge mode)
3. Queries Binance to verify orders appear
4. Shows how the UI would display them
5. Cleans up
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


async def test_position_with_sltp():
    """Test opening position with SL/TP"""

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
        logger.info(f"\n{'='*80}")
        logger.info("STEP 1: GET CURRENT PRICE")
        logger.info(f"{'='*80}")

        ticker = client.futures_symbol_ticker(symbol=symbol)
        current_price = float(ticker["price"])
        logger.info(f"📊 Current {symbol} price: ${current_price:,.2f}")

        # Calculate SL/TP prices
        sl_price = f"{current_price * 0.98:.1f}"  # 2% below
        tp_price = f"{current_price * 1.02:.1f}"  # 2% above

        logger.info(f"📉 Stop Loss will be: ${sl_price}")
        logger.info(f"📈 Take Profit will be: ${tp_price}")

        # ============================================================
        logger.info(f"\n{'='*80}")
        logger.info("STEP 2: OPEN POSITION")
        logger.info(f"{'='*80}")

        position_params = {
            "symbol": symbol,
            "side": SIDE_BUY,
            "type": FUTURE_ORDER_TYPE_MARKET,
            "quantity": quantity,
            "positionSide": position_side,
        }

        logger.info(f"Opening {position_side} position...")
        logger.info(f"Params: {json.dumps(position_params, indent=2)}")

        position_result = client.futures_create_order(**position_params)
        position_id = position_result["orderId"]
        logger.info(f"✅ Position opened: Order ID {position_id}")
        logger.info(f"   Status: {position_result['status']}")

        await asyncio.sleep(2)

        # ============================================================
        logger.info(f"\n{'='*80}")
        logger.info("STEP 3: PLACE STOP-LOSS (WITH FIX)")
        logger.info(f"{'='*80}")

        # THIS IS THE FIXED APPROACH - NO reduceOnly parameter
        sl_params = {
            "symbol": symbol,
            "side": SIDE_SELL,  # Opposite to close
            "type": FUTURE_ORDER_TYPE_STOP_MARKET,
            "quantity": quantity,
            "stopPrice": sl_price,
            "positionSide": position_side,  # Same as position
            # ✅ NO reduceOnly parameter - Binance handles it automatically in hedge mode!
        }

        logger.info("Placing Stop Loss...")
        logger.info(f"Params: {json.dumps(sl_params, indent=2)}")

        sl_result = client.futures_create_order(**sl_params)
        sl_id = sl_result["orderId"]
        logger.info(f"✅ Stop Loss placed: Order ID {sl_id}")
        logger.info(f"   Status: {sl_result['status']}")
        logger.info(
            f"   Reduce Only (auto-set by Binance): {sl_result.get('reduceOnly')}"
        )
        logger.info(f"   Close Position: {sl_result.get('closePosition')}")

        # ============================================================
        logger.info(f"\n{'='*80}")
        logger.info("STEP 4: PLACE TAKE-PROFIT (WITH FIX)")
        logger.info(f"{'='*80}")

        # THIS IS THE FIXED APPROACH - NO reduceOnly parameter
        tp_params = {
            "symbol": symbol,
            "side": SIDE_SELL,  # Opposite to close
            "type": FUTURE_ORDER_TYPE_TAKE_PROFIT_MARKET,
            "quantity": quantity,
            "stopPrice": tp_price,
            "positionSide": position_side,  # Same as position
            # ✅ NO reduceOnly parameter - Binance handles it automatically in hedge mode!
        }

        logger.info("Placing Take Profit...")
        logger.info(f"Params: {json.dumps(tp_params, indent=2)}")

        tp_result = client.futures_create_order(**tp_params)
        tp_id = tp_result["orderId"]
        logger.info(f"✅ Take Profit placed: Order ID {tp_id}")
        logger.info(f"   Status: {tp_result['status']}")
        logger.info(
            f"   Reduce Only (auto-set by Binance): {tp_result.get('reduceOnly')}"
        )
        logger.info(f"   Close Position: {tp_result.get('closePosition')}")

        await asyncio.sleep(2)

        # ============================================================
        logger.info(f"\n{'='*80}")
        logger.info("STEP 5: VERIFY ORDERS ON BINANCE")
        logger.info(f"{'='*80}")

        # Get current position
        positions = client.futures_position_information(symbol=symbol)
        long_position = [p for p in positions if p["positionSide"] == "LONG"][0]

        logger.info("\n📍 LONG Position:")
        logger.info(f"   Amount: {long_position['positionAmt']}")
        logger.info(f"   Entry Price: {long_position['entryPrice']}")
        logger.info(f"   Mark Price: {long_position['markPrice']}")
        logger.info(f"   Unrealized PnL: {long_position['unRealizedProfit']}")

        # Get open orders
        open_orders = client.futures_get_open_orders(symbol=symbol)

        logger.info(f"\n📋 Open Orders: {len(open_orders)} total")

        sl_orders = [
            o
            for o in open_orders
            if o["type"] == "STOP_MARKET" and o["positionSide"] == position_side
        ]
        tp_orders = [
            o
            for o in open_orders
            if o["type"] == "TAKE_PROFIT_MARKET" and o["positionSide"] == position_side
        ]

        logger.info(f"\n🎯 Orders for {position_side} position:")

        if sl_orders:
            logger.info(f"  ✅ Stop Loss Orders: {len(sl_orders)}")
            for order in sl_orders:
                logger.info(f"     - Order {order['orderId']}: @ ${order['stopPrice']}")
                logger.info(f"       Side: {order['side']}, Qty: {order['origQty']}")
                logger.info(f"       Reduce Only: {order['reduceOnly']}")
                logger.info(f"       Status: {order['status']}")
        else:
            logger.info("  ❌ No Stop Loss Orders")

        if tp_orders:
            logger.info(f"  ✅ Take Profit Orders: {len(tp_orders)}")
            for order in tp_orders:
                logger.info(f"     - Order {order['orderId']}: @ ${order['stopPrice']}")
                logger.info(f"       Side: {order['side']}, Qty: {order['origQty']}")
                logger.info(f"       Reduce Only: {order['reduceOnly']}")
                logger.info(f"       Status: {order['status']}")
        else:
            logger.info("  ❌ No Take Profit Orders")

        # ============================================================
        logger.info(f"\n{'='*80}")
        logger.info("🎨 HOW BINANCE UI DISPLAYS THIS")
        logger.info(f"{'='*80}")

        if sl_orders or tp_orders:
            sl_display = sl_orders[0]["stopPrice"] if sl_orders else "--"
            tp_display = tp_orders[0]["stopPrice"] if tp_orders else "--"
            logger.info(f"\n✅ Binance UI would show for {position_side} position:")
            logger.info(f"   SL/TP: {sl_display} / {tp_display}")
            logger.info("\n🎉 SUCCESS! Orders are visible in Binance UI!")
        else:
            logger.info("\n❌ Binance UI would show:")
            logger.info("   SL/TP: -- / --")
            logger.info("\n⚠️  Orders NOT visible in Binance UI!")

        # ============================================================
        logger.info(f"\n{'='*80}")
        logger.info("STEP 6: CLEANUP")
        logger.info(f"{'='*80}")

        # Cancel all orders
        logger.info("Cancelling all open orders...")
        client.futures_cancel_all_open_orders(symbol=symbol)
        logger.info("✅ Orders cancelled")

        # Close position
        logger.info("Closing position...")
        close_params = {
            "symbol": symbol,
            "side": SIDE_SELL,
            "type": FUTURE_ORDER_TYPE_MARKET,
            "quantity": quantity,
            "positionSide": position_side,
        }
        client.futures_create_order(**close_params)
        logger.info("✅ Position closed")

        # ============================================================
        logger.info(f"\n{'='*80}")
        logger.info("✅ TEST COMPLETE")
        logger.info(f"{'='*80}")

        success = len(sl_orders) > 0 and len(tp_orders) > 0

        if success:
            logger.info("\n🎉 SUCCESS! The fix is working:")
            logger.info(f"   - Position opened: {position_id}")
            logger.info(f"   - Stop Loss placed: {sl_id}")
            logger.info(f"   - Take Profit placed: {tp_id}")
            logger.info("   - Both orders verified on Binance")
            logger.info("   - Orders visible in Binance UI")
        else:
            logger.info("\n❌ FAILED: Orders not appearing on Binance")

        return success

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
    success = await test_position_with_sltp()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
