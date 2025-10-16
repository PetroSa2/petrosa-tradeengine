#!/usr/bin/env python3
"""
Open a test position on Binance Futures testnet

This script opens a real LONG position on BTCUSDT and leaves it open.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from binance import Client  # noqa: E402
from binance.exceptions import BinanceAPIException  # noqa: E402


async def open_position():
    """Open a test position on Binance Futures testnet"""
    api_key = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_API_SECRET")

    print("🚀 Opening Test Position on Binance Futures Testnet")
    print("=" * 70)

    try:
        # Initialize client
        client = Client(api_key=api_key, api_secret=api_secret, testnet=True)
        print("✅ Client initialized")

        # Get current price
        ticker = client.futures_symbol_ticker(symbol="BTCUSDT")
        current_price = float(ticker["price"])
        print(f"\n📊 Current BTCUSDT price: ${current_price:,.2f}")

        # Get account balance
        account = client.futures_account()
        available_balance = float(account.get("availableBalance", 0))
        print(f"💰 Available Balance: ${available_balance:,.2f} USDT")

        if available_balance < 100:
            print("\n⚠️  Insufficient balance for minimum notional ($100)")
            print("Visit https://testnet.binancefuture.com to get testnet funds")
            return False

        # Calculate quantity that meets MIN_NOTIONAL of $100
        min_notional = 100
        quantity = round((min_notional / current_price) * 1.1, 3)  # 10% above minimum
        notional_value = quantity * current_price

        print("\n📝 Opening LONG position:")
        print("  Symbol: BTCUSDT")
        print("  Side: BUY")
        print("  Type: MARKET")
        print(f"  Quantity: {quantity} BTC")
        print(f"  Notional Value: ${notional_value:,.2f}")

        # Create market buy order to open long position
        print("\n🔨 Executing market order...")
        order = client.futures_create_order(
            symbol="BTCUSDT",
            side="BUY",
            type="MARKET",
            quantity=quantity,
        )

        print("\n✅ Position opened successfully!")
        print(f"  Order ID: {order['orderId']}")
        print(f"  Status: {order['status']}")
        print(f"  Executed Qty: {order['executedQty']}")
        print(f"  Average Price: ${float(order.get('avgPrice', 0)):,.2f}")

        # Get position info
        print("\n📊 Checking position status...")
        positions = client.futures_position_information(symbol="BTCUSDT")

        for position in positions:
            if position["symbol"] == "BTCUSDT":
                pos_amt = float(position["positionAmt"])
                if pos_amt != 0:
                    print("\n🎯 Position Details:")
                    print(f"  Symbol: {position['symbol']}")
                    print(f"  Position Amount: {position['positionAmt']} BTC")
                    print(f"  Entry Price: ${float(position['entryPrice']):,.2f}")
                    print(
                        f"  Unrealized PnL: ${float(position['unRealizedProfit']):,.2f}"
                    )
                    print(f"  Leverage: {position['leverage']}x")
                    print(f"  Margin Type: {position['marginType']}")
                    print(f"  Position Side: {position['positionSide']}")

        # Get account info
        account = client.futures_account()
        print("\n💼 Account Summary:")
        print(
            f"  Total Wallet Balance: ${float(account.get('totalWalletBalance', 0)):,.2f}"
        )
        print(
            f"  Total Unrealized PnL: ${float(account.get('totalUnrealizedProfit', 0)):,.2f}"
        )
        print(f"  Available Balance: ${float(account.get('availableBalance', 0)):,.2f}")

        print("\n" + "=" * 70)
        print("🎉 Position opened and left active!")
        print("✅ You now have an open LONG position on BTCUSDT")
        print("\nTo close this position later, use:")
        print("  python scripts/close-test-position.py")
        print("=" * 70)

        return True

    except BinanceAPIException as e:
        print(f"\n❌ Binance API Error: {e.code} - {e.message}")
        if e.code == -2019:
            print("\n💡 Insufficient margin - you need testnet funds!")
            print("Visit: https://testnet.binancefuture.com")
            print("Click on 'Testnet Faucet' or 'Get Test Funds' to add USDT")
        return False
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    result = asyncio.run(open_position())
    sys.exit(0 if result else 1)
