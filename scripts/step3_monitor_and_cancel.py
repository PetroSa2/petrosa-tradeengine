#!/usr/bin/env python3
"""
Step 3: Monitor orders and implement OCO cancellation
When one order fills, automatically cancel the other
"""

import os
import sys
import time

from binance import Client

print("=" * 60)
print("🔍 STEP 3: MONITOR OCO ORDERS & AUTO-CANCEL")
print("=" * 60)
print()

# Get credentials
api_key = os.getenv("BINANCE_API_KEY")
api_secret = os.getenv("BINANCE_API_SECRET")

if not api_key or not api_secret:
    print("❌ ERROR: API credentials not set")
    sys.exit(1)

# Initialize testnet client
client = Client(api_key, api_secret, testnet=True)
print("✅ Connected to Binance TESTNET")
print()

symbol = "BTCUSDT"

# Get initial orders
print("=" * 60)
print("CHECKING INITIAL ORDERS")
print("=" * 60)

open_orders = client.futures_get_open_orders(symbol=symbol)

if len(open_orders) < 2:
    print("❌ Need 2 orders (SL + TP) to test OCO")
    print("Please run step2b_place_tight_oco_orders.py first")
    sys.exit(1)

sl_order_id = None
tp_order_id = None

for order in open_orders:
    if order["type"] == "STOP_MARKET":
        sl_order_id = order["orderId"]
        sl_price = float(order["stopPrice"])
        print(f"📉 Stop Loss: Order {sl_order_id} @ ${sl_price:,.2f}")
    elif order["type"] == "TAKE_PROFIT_MARKET":
        tp_order_id = order["orderId"]
        tp_price = float(order["stopPrice"])
        print(f"📈 Take Profit: Order {tp_order_id} @ ${tp_price:,.2f}")

print()
print(f"✅ Found {len(open_orders)} orders to monitor")
print()

# Get current price
ticker = client.futures_symbol_ticker(symbol=symbol)
current_price = float(ticker["price"])
print(f"📊 Current Price: ${current_price:,.2f}")
print()

print("=" * 60)
print("🔍 STARTING OCO MONITORING")
print("=" * 60)
print()
print("Monitoring every 5 seconds...")
print("Press Ctrl+C to stop")
print()

check_count = 0

try:
    while True:
        check_count += 1

        # Get current price
        ticker = client.futures_symbol_ticker(symbol=symbol)
        current_price = float(ticker["price"])

        # Get open orders
        open_orders = client.futures_get_open_orders(symbol=symbol)
        open_order_ids = [o["orderId"] for o in open_orders]

        # Check status
        timestamp = time.strftime("%H:%M:%S")
        print(
            f"[{timestamp}] Check #{check_count} - Price: ${current_price:,.2f} - Orders: {len(open_orders)}"
        )

        # Check if SL is missing (filled)
        if sl_order_id and sl_order_id not in open_order_ids:
            print()
            print("=" * 60)
            print("🚨 STOP LOSS ORDER FILLED!")
            print("=" * 60)
            print(f"Order ID {sl_order_id} was executed")
            print(f"Position closed at approximately ${sl_price:,.2f}")
            print()

            # Check if TP is still open
            if tp_order_id in open_order_ids:
                print("🔄 CANCELLING TAKE PROFIT ORDER (OCO BEHAVIOR)")
                print(f"Order ID: {tp_order_id}")

                try:
                    result = client.futures_cancel_order(
                        symbol=symbol, orderId=tp_order_id
                    )
                    print("✅ Take Profit order cancelled successfully!")
                    print()
                    print("=" * 60)
                    print("✅ OCO BEHAVIOR CONFIRMED!")
                    print("=" * 60)
                    print()
                    print("Summary:")
                    print(f"  📉 Stop Loss filled: Order {sl_order_id}")
                    print(f"  ❌ Take Profit cancelled: Order {tp_order_id}")
                    print("  ✅ Only ONE order executed (OCO working!)")
                    print()
                    break
                except Exception as e:
                    print(f"❌ Error cancelling TP: {e}")
            else:
                print("⚠️  Take Profit order already gone")
                print("(May have been manually cancelled or filled)")
                break

        # Check if TP is missing (filled)
        elif tp_order_id and tp_order_id not in open_order_ids:
            print()
            print("=" * 60)
            print("🎉 TAKE PROFIT ORDER FILLED!")
            print("=" * 60)
            print(f"Order ID {tp_order_id} was executed")
            print(f"Position closed at approximately ${tp_price:,.2f}")
            print()

            # Check if SL is still open
            if sl_order_id in open_order_ids:
                print("🔄 CANCELLING STOP LOSS ORDER (OCO BEHAVIOR)")
                print(f"Order ID: {sl_order_id}")

                try:
                    result = client.futures_cancel_order(
                        symbol=symbol, orderId=sl_order_id
                    )
                    print("✅ Stop Loss order cancelled successfully!")
                    print()
                    print("=" * 60)
                    print("✅ OCO BEHAVIOR CONFIRMED!")
                    print("=" * 60)
                    print()
                    print("Summary:")
                    print(f"  📈 Take Profit filled: Order {tp_order_id}")
                    print(f"  ❌ Stop Loss cancelled: Order {sl_order_id}")
                    print("  ✅ Only ONE order executed (OCO working!)")
                    print()
                    break
                except Exception as e:
                    print(f"❌ Error cancelling SL: {e}")
            else:
                print("⚠️  Stop Loss order already gone")
                print("(May have been manually cancelled or filled)")
                break

        # Both orders still there
        else:
            # Show distance to triggers
            if sl_order_id in open_order_ids and tp_order_id in open_order_ids:
                sl_distance = abs(current_price - sl_price)
                tp_distance = abs(tp_price - current_price)
                print(
                    f"           SL distance: ${sl_distance:.2f} | TP distance: ${tp_distance:.2f}"
                )

        # Wait before next check
        time.sleep(5)

except KeyboardInterrupt:
    print()
    print()
    print("=" * 60)
    print("⏸️  MONITORING STOPPED BY USER")
    print("=" * 60)
    print()
    print("Orders are still active on Binance")
    print("You can:")
    print("  - Resume monitoring by running this script again")
    print("  - Check manually on https://testnet.binancefuture.com/")
    print()

except Exception as e:
    print()
    print(f"❌ Error during monitoring: {e}")
    import traceback

    traceback.print_exc()
