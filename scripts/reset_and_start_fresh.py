#!/usr/bin/env python3
"""
Cancel all orders, close position, and start fresh with tight stops
"""
import os
import time

from binance import Client


def main():
    # Initialize client
    client = Client(
        os.getenv("BINANCE_API_KEY"), os.getenv("BINANCE_API_SECRET"), testnet=True
    )

    symbol = "BTCUSDT"
    quantity = 0.001

    print("=== RESETTING EVERYTHING ===")

    # 1. Cancel all open orders
    print("1. Cancelling all open orders...")
    try:
        result = client.futures_cancel_all_open_orders(symbol=symbol)
        print(f"   Cancelled orders: {result}")
    except Exception as e:
        print(f"   Error cancelling orders: {e}")

    # 2. Get current position and close it
    print("2. Closing existing position...")
    positions = client.futures_position_information(symbol=symbol)
    for pos in positions:
        position_amt = float(pos["positionAmt"])
        if position_amt != 0:
            print(f"   Found position: {position_amt} BTC")

            # Close position with market order
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
                print(f"   Position closed: {close_result}")
            except Exception as e:
                print(f"   Error closing position: {e}")

    time.sleep(2)  # Wait for position to close

    # 3. Get current price
    print("3. Getting current price...")
    ticker = client.futures_symbol_ticker(symbol=symbol)
    current_price = float(ticker["price"])
    print(f"   Current Price: ${current_price:,.2f}")

    # 4. Open new LONG position
    print("4. Opening new LONG position...")
    try:
        position_result = client.futures_create_order(
            symbol=symbol,
            side="BUY",
            type="MARKET",
            quantity=quantity,
            positionSide="LONG",
        )
        print(f"   Position opened: {position_result}")
    except Exception as e:
        print(f"   Error opening position: {e}")
        return

    time.sleep(2)  # Wait for position to open

    # 5. Place VERY tight SL/TP orders (0.1% and 0.2%)
    print("5. Placing TIGHT SL/TP orders...")

    # Calculate tight prices
    sl_price = current_price * 0.999  # 0.1% below
    tp_price = current_price * 1.002  # 0.2% above

    print(f"   SL Price: ${sl_price:,.2f} (0.1% below)")
    print(f"   TP Price: ${tp_price:,.2f} (0.2% above)")

    # Place Stop Loss
    try:
        sl_order = client.futures_create_order(
            symbol=symbol,
            side="SELL",
            type="STOP_MARKET",
            quantity=quantity,
            stopPrice=round(sl_price, 1),
            positionSide="LONG",
        )
        print(f"   SL Order placed: {sl_order['orderId']}")
    except Exception as e:
        print(f"   Error placing SL: {e}")

    # Place Take Profit
    try:
        tp_order = client.futures_create_order(
            symbol=symbol,
            side="SELL",
            type="TAKE_PROFIT_MARKET",
            quantity=quantity,
            stopPrice=round(tp_price, 1),
            positionSide="LONG",
        )
        print(f"   TP Order placed: {tp_order['orderId']}")
    except Exception as e:
        print(f"   Error placing TP: {e}")

    # 6. Show final status
    print("\n=== FINAL STATUS ===")

    # Show position
    positions = client.futures_position_information(symbol=symbol)
    for pos in positions:
        position_amt = float(pos["positionAmt"])
        if position_amt != 0:
            print(f"Position: {position_amt} BTC at ${pos['entryPrice']}")

    # Show orders
    orders = client.futures_get_open_orders(symbol=symbol)
    print(f"Open Orders: {len(orders)}")
    for order in orders:
        stop_price = float(order["stopPrice"])
        distance = abs(current_price - stop_price)
        pct = (stop_price / current_price - 1) * 100
        print(
            f"  {order['type']}: ${stop_price:,.2f} ({pct:+.2f}%) - Distance: ${distance:.2f}"
        )


if __name__ == "__main__":
    main()
