#!/usr/bin/env python3
"""
Step 2B: Cancel far orders and place TIGHT OCO orders for quick testing
"""

import os
import sys

from binance import Client
from binance.enums import (
    FUTURE_ORDER_TYPE_STOP_MARKET,
    FUTURE_ORDER_TYPE_TAKE_PROFIT_MARKET,
    SIDE_SELL,
)

print("=" * 60)
print("🚀 STEP 2B: PLACE TIGHT OCO ORDERS (for quick testing)")
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
position_side = "LONG"

# Cancel existing orders first
print("=" * 60)
print("CANCELING EXISTING ORDERS")
print("=" * 60)

try:
    existing_orders = client.futures_get_open_orders(symbol=symbol)
    if existing_orders:
        print(f"Found {len(existing_orders)} existing order(s) to cancel...")
        for order in existing_orders:
            client.futures_cancel_order(symbol=symbol, orderId=order["orderId"])
            print(f"✅ Cancelled Order ID: {order['orderId']} ({order['type']})")
        print()
    else:
        print("No existing orders to cancel")
        print()
except Exception as e:
    print(f"⚠️  Error canceling orders: {e}")
    print()

# Get current position
print("=" * 60)
print("CHECKING CURRENT POSITION")
print("=" * 60)

positions = client.futures_position_information(symbol=symbol)
current_position = None

for pos in positions:
    if pos["positionSide"] == position_side and float(pos["positionAmt"]) != 0:
        current_position = pos
        break

if not current_position:
    print(f"❌ No open {position_side} position found for {symbol}")
    print("Please run step1_open_position.py first")
    sys.exit(1)

position_amt = float(current_position["positionAmt"])
entry_price = float(current_position["entryPrice"])

print(f"✅ Found open {position_side} position:")
print(f"   Amount: {position_amt} BTC")
print(f"   Entry Price: ${entry_price:,.2f}")
print(f"   Current PnL: ${float(current_position['unRealizedProfit']):.2f}")
print()

# Get current price
ticker = client.futures_symbol_ticker(symbol=symbol)
current_price = float(ticker["price"])
print(f"📊 Current Price: ${current_price:,.2f}")
print()

# Calculate TIGHT SL and TP prices for quick testing
# For LONG: SL below current, TP above current
sl_percentage = 0.003  # 0.3% stop loss (VERY TIGHT for testing)
tp_percentage = 0.006  # 0.6% take profit (VERY TIGHT for testing)

sl_price = current_price * (1 - sl_percentage)
tp_price = current_price * (1 + tp_percentage)

# Make sure prices are not too close (Binance has minimum distance)
min_distance = current_price * 0.002  # 0.2% minimum distance
if abs(current_price - sl_price) < min_distance:
    sl_price = current_price * 0.997  # 0.3% below
if abs(tp_price - current_price) < min_distance:
    tp_price = current_price * 1.003  # 0.3% above

print("=" * 60)
print("TIGHT OCO ORDERS (FOR QUICK TESTING)")
print("=" * 60)
print(f"Position: {position_side}")
print(f"Current Price: ${current_price:,.2f}")
print()
print("Stop Loss Order:")
print("   Type: STOP_MARKET")
print(f"   Trigger Price: ${sl_price:,.2f} ({((sl_price/current_price-1)*100):.2f}%)")
print(f"   Distance: ${current_price - sl_price:.2f}")
print(f"   Quantity: {abs(position_amt)} BTC")
print()
print("Take Profit Order:")
print("   Type: TAKE_PROFIT_MARKET")
print(f"   Trigger Price: ${tp_price:,.2f} ({((tp_price/current_price-1)*100):.2f}%)")
print(f"   Distance: ${tp_price - current_price:.2f}")
print(f"   Quantity: {abs(position_amt)} BTC")
print()
print("⚠️  These are VERY TIGHT ranges for quick testing!")
print("⚠️  Price movement of $300-650 will trigger one order")
print()

input("Press ENTER to place TIGHT OCO orders (Ctrl+C to cancel)...")
print()

try:
    # Place Stop Loss order
    print("Placing Stop Loss order...")
    sl_order = client.futures_create_order(
        symbol=symbol,
        side=SIDE_SELL,
        type=FUTURE_ORDER_TYPE_STOP_MARKET,
        quantity=abs(position_amt),
        stopPrice=round(sl_price, 1),
        positionSide=position_side,
    )

    print("✅ Stop Loss Order Placed!")
    print(f"   Order ID: {sl_order['orderId']}")
    print(f"   Status: {sl_order['status']}")
    print(f"   Stop Price: ${sl_order['stopPrice']}")
    print()

    # Place Take Profit order
    print("Placing Take Profit order...")
    tp_order = client.futures_create_order(
        symbol=symbol,
        side=SIDE_SELL,
        type=FUTURE_ORDER_TYPE_TAKE_PROFIT_MARKET,
        quantity=abs(position_amt),
        stopPrice=round(tp_price, 1),
        positionSide=position_side,
    )

    print("✅ Take Profit Order Placed!")
    print(f"   Order ID: {tp_order['orderId']}")
    print(f"   Status: {tp_order['status']}")
    print(f"   Stop Price: ${tp_order['stopPrice']}")
    print()

    print("=" * 60)
    print("✅ TIGHT OCO ORDERS PLACED!")
    print("=" * 60)
    print()

    # Show all open orders
    print("=" * 60)
    print("ALL OPEN ORDERS (CONTINGENT ORDERS)")
    print("=" * 60)

    open_orders = client.futures_get_open_orders(symbol=symbol)

    if open_orders:
        print(f"Found {len(open_orders)} open order(s):")
        print()

        for order in open_orders:
            order_price = float(order["stopPrice"])
            distance = abs(current_price - order_price)
            pct = (order_price / current_price - 1) * 100

            print(f"Order ID: {order['orderId']}")
            print(f"   Type: {order['type']}")
            print(f"   Stop Price: ${order_price:,.2f} ({pct:+.2f}%)")
            print(f"   Distance from current: ${distance:.2f}")
            print(f"   Status: {order['status']}")

            if order["type"] == "STOP_MARKET":
                print(f"   📉 STOP LOSS - Will trigger if price drops ${distance:.2f}")
            elif order["type"] == "TAKE_PROFIT_MARKET":
                print(
                    f"   📈 TAKE PROFIT - Will trigger if price rises ${distance:.2f}"
                )
            print()

    print("=" * 60)
    print("✅ READY FOR TESTING")
    print("=" * 60)
    print()
    print("Summary:")
    print(f"  📊 Current Price: ${current_price:,.2f}")
    print(
        f"  📉 Stop Loss: ${sl_price:,.2f} (needs ${current_price - sl_price:.2f} drop)"
    )
    print(
        f"  📈 Take Profit: ${tp_price:,.2f} (needs ${tp_price - current_price:.2f} rise)"
    )
    print()
    print(
        "These orders are MUCH closer and should trigger soon with normal volatility!"
    )
    print()
    print("🔗 Watch live on: https://testnet.binancefuture.com/")
    print()
    print("Next: Run step3_monitor_and_cancel.py to watch for triggers and test OCO")
    print()

except Exception as e:
    print(f"❌ ERROR placing orders: {e}")
    import traceback

    traceback.print_exc()
    sys.exit(1)
