#!/usr/bin/env python3
"""
Close the test position on Binance Futures testnet

This script closes any open BTCUSDT position.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from binance import Client
from binance.exceptions import BinanceAPIException


async def close_position():
    """Close the open position on Binance Futures testnet"""
    api_key = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_API_SECRET")

    print("🚀 Closing Test Position on Binance Futures Testnet")
    print("=" * 70)

    try:
        # Initialize client
        client = Client(api_key=api_key, api_secret=api_secret, testnet=True)
        print("✅ Client initialized")

        # Get current position
        print("\n📊 Checking current position...")
        positions = client.futures_position_information(symbol="BTCUSDT")

        position_amt = 0
        entry_price = 0
        unrealized_pnl = 0

        for position in positions:
            if position["symbol"] == "BTCUSDT":
                position_amt = float(position["positionAmt"])
                entry_price = float(position["entryPrice"])
                unrealized_pnl = float(position["unRealizedProfit"])

        if position_amt == 0:
            print("⚠️  No open position found for BTCUSDT")
            return True

        print("\n🎯 Current Position:")
        print("  Symbol: BTCUSDT")
        print(f"  Position Amount: {position_amt} BTC")
        print(f"  Entry Price: ${entry_price:,.2f}")
        print(f"  Unrealized PnL: ${unrealized_pnl:,.2f}")

        # Get current price
        ticker = client.futures_symbol_ticker(symbol="BTCUSDT")
        current_price = float(ticker["price"])
        print(f"  Current Price: ${current_price:,.2f}")

        # Calculate PnL
        price_change = ((current_price - entry_price) / entry_price) * 100
        print(f"  Price Change: {price_change:+.2f}%")

        # Determine side to close
        if position_amt > 0:
            side = "SELL"
            print(f"\n📝 Closing LONG position (SELL {abs(position_amt)} BTC)")
        else:
            side = "BUY"
            print(f"\n📝 Closing SHORT position (BUY {abs(position_amt)} BTC)")

        # Close position with market order
        print("\n🔨 Executing market order to close position...")
        order = client.futures_create_order(
            symbol="BTCUSDT",
            side=side,
            type="MARKET",
            quantity=abs(position_amt),
        )

        print("\n✅ Position closed successfully!")
        print(f"  Order ID: {order['orderId']}")
        print(f"  Status: {order['status']}")
        print(f"  Executed Qty: {order['executedQty']}")
        print(f"  Average Price: ${float(order.get('avgPrice', 0)):,.2f}")

        # Verify position is closed
        print("\n📊 Verifying position closure...")
        positions = client.futures_position_information(symbol="BTCUSDT")

        for position in positions:
            if position["symbol"] == "BTCUSDT":
                final_amt = float(position["positionAmt"])
                if final_amt == 0:
                    print("✅ Position fully closed")
                else:
                    print(f"⚠️  Remaining position: {final_amt} BTC")

        # Get final account summary
        account = client.futures_account()
        print("\n💼 Final Account Summary:")
        print(
            f"  Total Wallet Balance: ${float(account.get('totalWalletBalance', 0)):,.2f}"
        )
        print(
            f"  Total Unrealized PnL: ${float(account.get('totalUnrealizedProfit', 0)):,.2f}"
        )
        print(f"  Available Balance: ${float(account.get('availableBalance', 0)):,.2f}")

        # Show realized PnL if available
        print("\n📈 Trade Result:")
        print(f"  Realized PnL: ${unrealized_pnl:,.2f}")

        print("\n" + "=" * 70)
        print("🎉 Position closed successfully!")
        print("=" * 70)

        return True

    except BinanceAPIException as e:
        print(f"\n❌ Binance API Error: {e.code} - {e.message}")
        return False
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    result = asyncio.run(close_position())
    sys.exit(0 if result else 1)
