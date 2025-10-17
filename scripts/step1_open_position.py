#!/usr/bin/env python3
"""
Step 1: Open a test position on Binance Testnet
"""

import os
import sys

from binance import Client
from binance.enums import FUTURE_ORDER_TYPE_MARKET, SIDE_BUY

print("=" * 60)
print("🚀 STEP 1: OPEN TEST POSITION ON BINANCE TESTNET")
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

# Get current price
symbol = "BTCUSDT"
ticker = client.futures_symbol_ticker(symbol=symbol)
current_price = float(ticker["price"])
print(f"📊 Current {symbol} Price: ${current_price:,.2f}")
print()

# Small test quantity
quantity = 0.001  # Very small position
position_side = "LONG"

print("=" * 60)
print("OPENING POSITION")
print("=" * 60)
print(f"Symbol: {symbol}")
print("Side: BUY")
print(f"Position Side: {position_side}")
print(f"Quantity: {quantity} BTC")
print("Type: MARKET")
print(f"Estimated Value: ${current_price * quantity:.2f} USDT")
print()

# Confirm
input("Press ENTER to place the order (Ctrl+C to cancel)...")
print()

try:
    # Place market order to open position
    order = client.futures_create_order(
        symbol=symbol,
        side=SIDE_BUY,
        type=FUTURE_ORDER_TYPE_MARKET,
        quantity=quantity,
        positionSide=position_side,
    )

    print("=" * 60)
    print("✅ POSITION OPENED SUCCESSFULLY!")
    print("=" * 60)
    print(f"Order ID: {order['orderId']}")
    print(f"Status: {order['status']}")
    print(f"Symbol: {order['symbol']}")
    print(f"Side: {order['side']}")
    print(f"Position Side: {order['positionSide']}")
    print(f"Quantity: {order['origQty']}")
    print(f"Type: {order['type']}")
    print()

    # Get position info
    print("=" * 60)
    print("POSITION DETAILS")
    print("=" * 60)
    positions = client.futures_position_information(symbol=symbol)

    for pos in positions:
        if pos["positionSide"] == position_side and float(pos["positionAmt"]) != 0:
            print(f"Position Side: {pos['positionSide']}")
            print(f"Position Amount: {pos['positionAmt']} BTC")
            print(f"Entry Price: ${float(pos['entryPrice']):,.2f}")
            print(f"Unrealized PnL: ${float(pos['unRealizedProfit']):.2f}")
            print(f"Leverage: {pos['leverage']}x")
            print(f"Isolated: {pos['isolated']}")
            print()

    print("=" * 60)
    print("✅ STEP 1 COMPLETE")
    print("=" * 60)
    print()
    print(f"📊 You now have an open {position_side} position on {symbol}")
    print(f"📊 Entry price: ${float(pos['entryPrice']):,.2f}")
    print(f"📊 Position size: {quantity} BTC")
    print()
    print("🔗 You can verify this on: https://testnet.binancefuture.com/")
    print("   Go to: Futures → Positions")
    print()
    print("Next: Run step2_place_oco_orders.py to add SL/TP orders")
    print()

except Exception as e:
    print(f"❌ ERROR placing order: {e}")
    sys.exit(1)
