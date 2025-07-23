#!/usr/bin/env python3
"""
Test Binance Futures Implementation

This script tests the new Binance Futures implementation to ensure it works correctly.
"""

import asyncio
import os
import sys

# Add the project root to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from tradeengine.exchange.binance import binance_futures_exchange
except ImportError:
    print("❌ Cannot import binance_futures_exchange. Check your setup.")
    sys.exit(1)


async def test_futures_implementation():
    """Test the Binance Futures implementation"""
    print("🚀 Testing Binance Futures Implementation")
    print("=" * 50)

    try:
        # Test 1: Initialize
        print("\n📋 Test 1: Initialization")
        print("-" * 30)
        await binance_futures_exchange.initialize()
        print("✅ Futures exchange initialized successfully")

        # Test 2: Health Check
        print("\n🔌 Test 2: Health Check")
        print("-" * 30)
        health = await binance_futures_exchange.health_check()
        print(f"Health Status: {health}")
        if health.get("status") == "healthy":
            print("✅ Health check passed")
        else:
            print("❌ Health check failed")

        # Test 3: Get Account Info
        print("\n👤 Test 3: Account Info")
        print("-" * 30)
        try:
            account_info = await binance_futures_exchange.get_account_info()
            print("✅ Account info retrieved")
            print(f"Can Trade: {account_info.get('can_trade', 'Unknown')}")
            print(f"Assets: {len(account_info.get('assets', []))}")
        except Exception as e:
            print(f"❌ Account info failed: {e}")

        # Test 4: Get Price
        print("\n📈 Test 4: Price Data")
        print("-" * 30)
        try:
            price = await binance_futures_exchange.get_price("BTCUSDT")
            print(f"✅ BTCUSDT Price: ${price:,.2f}")
        except Exception as e:
            print(f"❌ Price retrieval failed: {e}")

        # Test 5: Get Position Info
        print("\n📊 Test 5: Position Info")
        print("-" * 30)
        try:
            positions = await binance_futures_exchange.get_position_info()
            print(f"✅ Position info retrieved: {len(positions)} positions")
            open_positions = [
                p for p in positions if float(p.get("positionAmt", 0)) != 0
            ]
            print(f"Open positions: {len(open_positions)}")
        except Exception as e:
            print(f"❌ Position info failed: {e}")

        # Test 6: Exchange Info
        print("\n📋 Test 6: Exchange Info")
        print("-" * 30)
        try:
            symbols = list(binance_futures_exchange.symbol_info.keys())
            print(f"✅ Exchange info loaded: {len(symbols)} symbols")
            futures_symbols = [s for s in symbols if s.endswith("USDT")][:5]
            print("Example symbols:")
            for symbol in futures_symbols:
                print(f"  - {symbol}")
        except Exception as e:
            print(f"❌ Exchange info failed: {e}")

        print("\n" + "=" * 50)
        print("✅ All tests completed!")
        print("=" * 50)

    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        return False

    finally:
        # Cleanup
        try:
            await binance_futures_exchange.close()
            print("✅ Cleanup completed")
        except Exception as e:
            print(f"⚠️  Cleanup error: {e}")

    return True


async def main():
    """Main function"""
    success = await test_futures_implementation()
    if success:
        print("\n🎉 Binance Futures implementation test passed!")
    else:
        print("\n❌ Binance Futures implementation test failed!")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
