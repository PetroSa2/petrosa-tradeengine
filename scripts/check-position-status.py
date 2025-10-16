#!/usr/bin/env python3
"""
Check current position status on Binance Futures testnet
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from binance import Client


async def check_positions():
    """Check all current positions"""
    api_key = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_API_SECRET")

    print("🔍 Checking Positions on Binance Futures Testnet")
    print("=" * 70)

    try:
        # Initialize client
        client = Client(api_key=api_key, api_secret=api_secret, testnet=True)

        # Get all positions
        print("\n📊 Fetching all positions...")
        positions = client.futures_position_information()

        # Filter only positions with non-zero amount
        active_positions = [p for p in positions if float(p["positionAmt"]) != 0]

        if not active_positions:
            print("\n⚠️  No active positions found")
            print("\nPossible reasons:")
            print("1. Position was auto-closed due to liquidation")
            print("2. Order didn't fill (unlikely for market order)")
            print("3. Looking at wrong account")
            print("4. Position closed by another process")
        else:
            print(f"\n✅ Found {len(active_positions)} active position(s):")

            for pos in active_positions:
                print(f"\n{'=' * 70}")
                print(f"Symbol: {pos['symbol']}")
                print(f"Position Amount: {pos['positionAmt']} (+ = LONG, - = SHORT)")
                print(f"Entry Price: ${float(pos['entryPrice']):,.2f}")
                print(f"Mark Price: ${float(pos['markPrice']):,.2f}")
                print(f"Unrealized PnL: ${float(pos['unRealizedProfit']):,.2f}")
                print(f"Leverage: {pos['leverage']}x")
                print(f"Margin Type: {pos['marginType']}")
                print(f"Position Side: {pos['positionSide']}")
                print(
                    f"Liquidation Price: ${float(pos['liquidationPrice']):,.2f}"
                    if float(pos["liquidationPrice"]) > 0
                    else "Liquidation Price: N/A"
                )

        # Get recent orders
        print(f"\n{'=' * 70}")
        print("📋 Recent Orders (BTCUSDT):")
        try:
            orders = client.futures_get_all_orders(symbol="BTCUSDT", limit=5)

            if orders:
                for order in orders[:5]:  # Show last 5 orders
                    print(f"\n  Order ID: {order['orderId']}")
                    print(f"  Time: {order['time']}")
                    print(f"  Side: {order['side']}")
                    print(f"  Type: {order['type']}")
                    print(f"  Status: {order['status']}")
                    print(f"  Original Qty: {order['origQty']}")
                    print(f"  Executed Qty: {order['executedQty']}")
                    print(f"  Average Price: ${float(order.get('avgPrice', 0)):,.2f}")
            else:
                print("  No recent orders found")
        except Exception as e:
            print(f"  Could not fetch orders: {e}")

        # Get account info
        print(f"\n{'=' * 70}")
        print("💼 Account Summary:")
        account = client.futures_account()
        print(
            f"  Total Wallet Balance: ${float(account.get('totalWalletBalance', 0)):,.2f}"
        )
        print(
            f"  Total Unrealized PnL: ${float(account.get('totalUnrealizedProfit', 0)):,.2f}"
        )
        print(f"  Available Balance: ${float(account.get('availableBalance', 0)):,.2f}")

        # GUI Access Info
        print(f"\n{'=' * 70}")
        print("🌐 Binance Futures Testnet GUI Access:")
        print("=" * 70)
        print("\n1. Visit: https://testnet.binancefuture.com")
        print("\n2. Login with your testnet account (NOT your main Binance account)")
        print(f"\n3. Your API Key: {api_key[:16]}...{api_key[-8:]}")
        print("\n4. Look for:")
        print("   • Top right: Your account balance")
        print("   • Bottom section: 'Positions' tab")
        print("   • Order History: 'Order History' tab")
        print("\n5. Make sure you're looking at:")
        print("   • BTCUSDT trading pair")
        print("   • USD-M Futures (not Coin-M)")
        print("   • Positions tab (not just orders)")

        print("\n" + "=" * 70)

        return True

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    result = asyncio.run(check_positions())
    sys.exit(0 if result else 1)
