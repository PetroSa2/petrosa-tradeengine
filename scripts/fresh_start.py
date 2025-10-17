#!/usr/bin/env python3
import os
import time

from binance import Client

client = Client(
    os.getenv("BINANCE_API_KEY"), os.getenv("BINANCE_API_SECRET"), testnet=True
)
symbol = "BTCUSDT"
quantity = 0.001

print("=== CANCELING ALL ORDERS ===")
try:
    result = client.futures_cancel_all_open_orders(symbol=symbol)
    print(f"Cancelled: {result}")
except Exception as e:
    print(f"Cancel error: {e}")

print("\n=== CLOSING POSITION ===")
positions = client.futures_position_information(symbol=symbol)
for pos in positions:
    position_amt = float(pos["positionAmt"])
    if position_amt != 0:
        print(f"Closing position: {position_amt} BTC")
        side = "SELL" if position_amt > 0 else "BUY"
        position_side = "LONG" if position_amt > 0 else "SHORT"
        try:
            close_result = client.futures_create_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=abs(position_amt),
                positionSide=position_side,
            )
            print(f"Closed: {close_result}")
        except Exception as e:
            print(f"Close error: {e}")

time.sleep(3)

print("\n=== GETTING CURRENT PRICE ===")
ticker = client.futures_symbol_ticker(symbol=symbol)
current_price = float(ticker["price"])
print(f"Current Price: ${current_price:,.2f}")

print("\n=== OPENING NEW POSITION ===")
try:
    position_result = client.futures_create_order(
        symbol=symbol, side="BUY", type="MARKET", quantity=quantity, positionSide="LONG"
    )
    print(f"Position opened: {position_result}")
except Exception as e:
    print(f"Position error: {e}")
    exit()

time.sleep(2)

print("\n=== PLACING TIGHT ORDERS ===")
sl_price = current_price * 0.999
tp_price = current_price * 1.002

print(f"SL: ${sl_price:,.2f} (0.1% below)")
print(f"TP: ${tp_price:,.2f} (0.2% above)")

try:
    sl_order = client.futures_create_order(
        symbol=symbol,
        side="SELL",
        type="STOP_MARKET",
        quantity=quantity,
        stopPrice=round(sl_price, 1),
        positionSide="LONG",
    )
    print(f"SL Order: {sl_order['orderId']}")
except Exception as e:
    print(f"SL error: {e}")

try:
    tp_order = client.futures_create_order(
        symbol=symbol,
        side="SELL",
        type="TAKE_PROFIT_MARKET",
        quantity=quantity,
        stopPrice=round(tp_price, 1),
        positionSide="LONG",
    )
    print(f"TP Order: {tp_order['orderId']}")
except Exception as e:
    print(f"TP error: {e}")

print("\n=== FINAL STATUS ===")
orders = client.futures_get_open_orders(symbol=symbol)
print(f"Open Orders: {len(orders)}")
for order in orders:
    stop_price = float(order["stopPrice"])
    distance = abs(current_price - stop_price)
    print(f"  {order['type']}: ${stop_price:,.2f} - Distance: ${distance:.2f}")

positions = client.futures_position_information(symbol=symbol)
for pos in positions:
    position_amt = float(pos["positionAmt"])
    if position_amt != 0:
        print(f"Position: {position_amt} BTC at ${pos['entryPrice']}")
