#!/usr/bin/env python3
"""
Check current price and existing orders
"""
import os

from binance import Client


def main():
    # Initialize client
    client = Client(
        os.getenv("BINANCE_API_KEY"), os.getenv("BINANCE_API_SECRET"), testnet=True
    )

    # Get current price
    ticker = client.futures_symbol_ticker(symbol="BTCUSDT")
    current_price = float(ticker["price"])
    print(f"Current Price: ${current_price:,.2f}")
    print()

    # Get open orders
    orders = client.futures_get_open_orders(symbol="BTCUSDT")
    print(f"Open Orders: {len(orders)}")
    for order in orders:
        stop_price = float(order["stopPrice"])
        distance = abs(current_price - stop_price)
        pct = (stop_price / current_price - 1) * 100
        print(
            f'  {order["type"]}: ${stop_price:,.2f} ({pct:+.2f}%) - Distance: ${distance:.2f}'
        )

    # Get position
    positions = client.futures_position_information(symbol="BTCUSDT")
    for pos in positions:
        if float(pos["positionAmt"]) != 0:
            print(f'\nPosition: {pos["positionAmt"]} BTC at ${pos["entryPrice"]}')


if __name__ == "__main__":
    main()
