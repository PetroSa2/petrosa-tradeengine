#!/usr/bin/env python3
"""
Verify Binance Futures Hedge Mode Configuration

This script checks if hedge mode is enabled on the Binance Futures account.
Hedge mode allows holding both LONG and SHORT positions simultaneously on the same symbol.
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import after path modification
from tradeengine.exchange.binance import binance_futures_exchange  # noqa: E402


async def main() -> None:
    """Verify hedge mode configuration"""
    print("=" * 80)
    print("Binance Futures Hedge Mode Verification")
    print("=" * 80)
    print()

    try:
        # Initialize exchange
        print("Initializing Binance Futures exchange...")
        await binance_futures_exchange.initialize()
        print("✅ Exchange initialized successfully")
        print()

        # Verify hedge mode
        print("Checking hedge mode configuration...")
        result = await binance_futures_exchange.verify_hedge_mode()
        print()

        # Display results
        print("=" * 80)
        print("RESULTS")
        print("=" * 80)
        print()

        if "error" in result:
            print(f"❌ Error checking hedge mode: {result['error']}")
            print()
            print("Please ensure:")
            print("  1. Your Binance API credentials are correctly configured")
            print("  2. Your API key has Futures trading permissions")
            print("  3. You have enabled Futures trading on your account")
            sys.exit(1)

        hedge_mode_enabled = result.get("hedge_mode_enabled", False)
        position_mode = result.get("position_mode", "unknown")

        if hedge_mode_enabled:
            print("✅ HEDGE MODE IS ENABLED")
            print()
            print(f"   Position Mode: {position_mode}")
            print(f"   Dual Side Position: {result.get('dual_side_position')}")
            print()
            print("You can now:")
            print("  • Open LONG and SHORT positions on the same symbol simultaneously")
            print("  • Track individual position performance for each strategy")
            print("  • Use hedge strategies for risk management")
            print()
            sys.exit(0)
        else:
            print("⚠️  HEDGE MODE IS NOT ENABLED")
            print()
            print(f"   Current Position Mode: {position_mode}")
            print()
            print("To enable hedge mode:")
            print()
            print("  1. Log in to Binance Futures")
            print("  2. Go to Settings (⚙️) in the top right")
            print("  3. Select 'Preferences'")
            print("  4. Under 'Position Mode', select 'Hedge Mode'")
            print("  5. Confirm the change")
            print()
            print("⚠️  Important Notes:")
            print("  • You cannot switch modes while holding open positions or orders")
            print("  • Close all positions and cancel all orders before switching")
            print("  • Run this script again after enabling hedge mode to verify")
            print()
            sys.exit(1)

    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
