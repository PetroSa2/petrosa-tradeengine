#!/usr/bin/env python3
"""
Step 2: Place OCO orders (SL and TP) for the open position
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
print("🚀 STEP 2: PLACE OCO ORDERS (SL + TP)")
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

# Calculate SL and TP prices
# For LONG: SL below entry, TP above entry
sl_percentage = 0.02  # 2% stop loss
tp_percentage = 0.04  # 4% take profit

sl_price = entry_price * (1 - sl_percentage)
tp_price = entry_price * (1 + tp_percentage)

print("=" * 60)
print("OCO ORDERS TO BE PLACED")
print("=" * 60)
print(f"Position: {position_side}")
print(f"Entry Price: ${entry_price:,.2f}")
print()
print("Stop Loss Order:")
print("   Type: STOP_MARKET")
print("   Side: SELL (closes LONG)")
print(f"   Trigger Price: ${sl_price:,.2f} ({-sl_percentage*100:.1f}%)")
print(f"   Quantity: {abs(position_amt)} BTC")
print()
print("Take Profit Order:")
print("   Type: TAKE_PROFIT_MARKET")
print("   Side: SELL (closes LONG)")
print(f"   Trigger Price: ${tp_price:,.2f} (+{tp_percentage*100:.1f}%)")
print(f"   Quantity: {abs(position_amt)} BTC")
print()

input("Press ENTER to place OCO orders (Ctrl+C to cancel)...")
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
        # Note: reduceOnly not needed in hedge mode - Binance handles it automatically
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
        # Note: reduceOnly not needed in hedge mode - Binance handles it automatically
    )

    print("✅ Take Profit Order Placed!")
    print(f"   Order ID: {tp_order['orderId']}")
    print(f"   Status: {tp_order['status']}")
    print(f"   Stop Price: ${tp_order['stopPrice']}")
    print()

    print("=" * 60)
    print("✅ OCO ORDERS PLACED SUCCESSFULLY!")
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
            print(f"Order ID: {order['orderId']}")
            print(f"   Type: {order['type']}")
            print(f"   Side: {order['side']}")
            print(f"   Position Side: {order['positionSide']}")
            print(f"   Quantity: {order['origQty']}")
            print(f"   Stop Price: ${float(order['stopPrice']):,.2f}")
            print(f"   Status: {order['status']}")
            print(f"   Reduce Only: {order['reduceOnly']}")

            # Identify which order is which
            if order["type"] == "STOP_MARKET":
                print("   📉 THIS IS THE STOP LOSS ORDER")
            elif order["type"] == "TAKE_PROFIT_MARKET":
                print("   📈 THIS IS THE TAKE PROFIT ORDER")
            print()
    else:
        print("No open orders found")
        print()

    print("=" * 60)
    print("✅ STEP 2 COMPLETE")
    print("=" * 60)
    print()
    print("Summary:")
    print(
        f"  ✅ Position: {position_side} {abs(position_amt)} BTC @ ${entry_price:,.2f}"
    )
    print(f"  ✅ Stop Loss: Order ID {sl_order['orderId']} @ ${sl_price:,.2f}")
    print(f"  ✅ Take Profit: Order ID {tp_order['orderId']} @ ${tp_price:,.2f}")
    print()
    print("🔗 Verify on Binance Testnet:")
    print("   https://testnet.binancefuture.com/")
    print("   - Positions tab: See your LONG position")
    print("   - Open Orders tab: See both SL and TP orders")
    print()
    print("Next steps:")
    print("  1. Wait for price to move and trigger one order")
    print("  2. When one triggers, manually cancel the other")
    print("  3. Or run step3_test_oco_cancellation.py to test cancellation")
    print()

except Exception as e:
    print(f"❌ ERROR placing orders: {e}")
    import traceback

    traceback.print_exc()
    sys.exit(1)
