#!/usr/bin/env python3
"""
Diagnostic script to check MIN_NOTIONAL values for symbols

This script queries the Binance Futures API to get MIN_NOTIONAL values
and calculates the minimum order quantities needed at current prices.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tradeengine.exchange.binance import BinanceFuturesExchange  # noqa: E402


async def check_min_notional():
    """Check MIN_NOTIONAL values for major trading symbols"""
    exchange = BinanceFuturesExchange()

    try:
        print("\n🔍 Initializing Binance Futures Exchange...")
        await exchange.initialize()
        print("✅ Connected to Binance Futures\n")

        # Major symbols to check
        symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "ADAUSDT", "DOTUSDT"]

        print("=" * 80)
        print("MIN_NOTIONAL Values and Minimum Quantities")
        print("=" * 80 + "\n")

        for symbol in symbols:
            try:
                # Get MIN_NOTIONAL info with current price
                info = await exchange.get_symbol_min_notional(symbol)

                print(f"📊 {symbol}:")
                print(f"  MIN_NOTIONAL:    ${info['min_notional']:.2f}")
                print(f"  Current Price:   ${info['current_price']:.2f}")
                print(f"  Min Quantity:    {info['min_quantity']:.8f}")
                print(f"  Notional Value:  ${info['notional_value']:.2f}")
                print(
                    f"  Status:          {'✅ OK' if info['notional_value'] >= info['min_notional'] else '❌ FAILED'}"
                )
                print()

            except Exception as e:
                print(f"❌ {symbol}: Error - {e}\n")

        print("=" * 80)
        print(
            "\n💡 Note: Min Quantity includes 5% safety margin to avoid rounding errors"
        )
        print("=" * 80 + "\n")

    except Exception as e:
        print(f"\n❌ Fatal error: {e}\n")
        raise

    finally:
        await exchange.close()
        print("✅ Exchange connection closed\n")


if __name__ == "__main__":
    asyncio.run(check_min_notional())
