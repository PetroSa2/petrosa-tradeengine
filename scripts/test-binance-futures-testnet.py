#!/usr/bin/env python3
"""
Binance Futures Testnet Testing Script

This script tests Binance Futures testnet API connectivity and functionality.
Run this on your pod to diagnose testnet API key issues.

Usage:
    python scripts/test-binance-futures-testnet.py
"""

import asyncio
import logging
import os
import sys

# Add the project root to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from binance.enums import ORDER_TYPE_LIMIT, SIDE_BUY, TIME_IN_FORCE_GTC
    from binance.um_futures import UMFutures
except ImportError as e:
    print(f"❌ Missing required dependencies: {e}")
    print("Install with: pip install python-binance")
    sys.exit(1)

# Import project constants
try:
    from shared.constants import BINANCE_API_KEY, BINANCE_API_SECRET, BINANCE_TESTNET
except ImportError:
    print("❌ Cannot import project constants. Check your environment setup.")
    sys.exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class BinanceFuturesTestnetTester:
    """Test Binance Futures testnet connectivity and functionality"""

    def __init__(self):
        self.client = None
        self.test_results = {}

    async def run_all_tests(self):
        """Run all testnet tests"""
        print("🚀 Starting Binance Futures Testnet Tests")
        print("=" * 50)

        # Test 1: Environment Variables
        await self.test_environment_variables()

        # Test 2: Client Initialization
        await self.test_client_initialization()

        # Test 3: Connection Test
        await self.test_connection()

        # Test 4: Exchange Info
        await self.test_exchange_info()

        # Test 5: Account Info
        await self.test_account_info()

        # Test 6: Market Data
        await self.test_market_data()

        # Test 7: Order Creation (Simulated)
        await self.test_order_creation()

        # Test 8: Position Info
        await self.test_position_info()

        # Print Results
        self.print_results()

    async def test_environment_variables(self):
        """Test environment variable configuration"""
        print("\n📋 Test 1: Environment Variables")
        print("-" * 30)

        # Check API credentials
        api_key_present = bool(BINANCE_API_KEY)
        api_secret_present = bool(BINANCE_API_SECRET)
        testnet_enabled = BINANCE_TESTNET

        print(f"API Key Present: {'✅' if api_key_present else '❌'}")
        print(f"API Secret Present: {'✅' if api_secret_present else '❌'}")
        print(f"Testnet Enabled: {'✅' if testnet_enabled else '❌'}")

        if not api_key_present or not api_secret_present:
            print("❌ Missing API credentials!")
            print("Set these environment variables:")
            print("  - BINANCE_API_KEY")
            print("  - BINANCE_API_SECRET")
            print("  - BINANCE_TESTNET=true")
            self.test_results["env_vars"] = False
            return

        if not testnet_enabled:
            print(
                "⚠️  Testnet is disabled. Set BINANCE_TESTNET=true for testnet testing."
            )
            self.test_results["env_vars"] = False
            return

        print("✅ Environment variables configured correctly")
        self.test_results["env_vars"] = True

    async def test_client_initialization(self):
        """Test futures client initialization"""
        print("\n🔧 Test 2: Client Initialization")
        print("-" * 30)

        try:
            # Initialize futures client with testnet
            self.client = UMFutures(
                key=BINANCE_API_KEY,
                secret=BINANCE_API_SECRET,
                testnet=True,  # Force testnet for testing
            )
            print("✅ Futures client initialized successfully")
            print(f"Client Type: {type(self.client).__name__}")
            print(f"Testnet Mode: {getattr(self.client, 'testnet', 'Unknown')}")
            self.test_results["client_init"] = True
        except Exception as e:
            print(f"❌ Client initialization failed: {e}")
            self.test_results["client_init"] = False

    async def test_connection(self):
        """Test API connection"""
        print("\n🔌 Test 3: API Connection")
        print("-" * 30)

        if not self.client:
            print("❌ Client not initialized")
            self.test_results["connection"] = False
            return

        try:
            # Test connection with ping
            response = self.client.ping()
            print("✅ API connection successful")
            print(f"Response: {response}")
            self.test_results["connection"] = True
        except Exception as e:
            print(f"❌ API connection failed: {e}")
            self.test_results["connection"] = False

    async def test_exchange_info(self):
        """Test exchange info retrieval"""
        print("\n📊 Test 4: Exchange Info")
        print("-" * 30)

        if not self.client:
            print("❌ Client not initialized")
            self.test_results["exchange_info"] = False
            return

        try:
            # Get futures exchange info
            exchange_info = self.client.exchange_info()
            symbols = exchange_info.get("symbols", [])

            print("✅ Exchange info retrieved successfully")
            print(f"Total symbols: {len(symbols)}")

            # Show some example symbols
            futures_symbols = [s for s in symbols if s["symbol"].endswith("USDT")][:5]
            print("Example symbols:")
            for symbol in futures_symbols:
                print(f"  - {symbol['symbol']} ({symbol['status']})")

            self.test_results["exchange_info"] = True
        except Exception as e:
            print(f"❌ Exchange info failed: {e}")
            self.test_results["exchange_info"] = False

    async def test_account_info(self):
        """Test account information retrieval"""
        print("\n👤 Test 5: Account Info")
        print("-" * 30)

        if not self.client:
            print("❌ Client not initialized")
            self.test_results["account_info"] = False
            return

        try:
            # Get account information
            account_info = self.client.account()

            print("✅ Account info retrieved successfully")
            print(f"Can Trade: {account_info.get('canTrade', 'Unknown')}")
            print(f"Can Withdraw: {account_info.get('canWithdraw', 'Unknown')}")
            print(f"Can Deposit: {account_info.get('canDeposit', 'Unknown')}")

            # Show balances
            balances = account_info.get("assets", [])
            print(f"Total assets: {len(balances)}")

            # Show non-zero balances
            non_zero_balances = [
                b for b in balances if float(b.get("walletBalance", 0)) > 0
            ]
            if non_zero_balances:
                print("Non-zero balances:")
                for balance in non_zero_balances[:5]:  # Show first 5
                    print(f"  - {balance['asset']}: {balance['walletBalance']}")
            else:
                print("No non-zero balances found")

            self.test_results["account_info"] = True
        except Exception as e:
            print(f"❌ Account info failed: {e}")
            self.test_results["account_info"] = False

    async def test_market_data(self):
        """Test market data retrieval"""
        print("\n📈 Test 6: Market Data")
        print("-" * 30)

        if not self.client:
            print("❌ Client not initialized")
            self.test_results["market_data"] = False
            return

        try:
            # Test with BTCUSDT
            symbol = "BTCUSDT"

            # Get ticker
            ticker = self.client.ticker_price(symbol=symbol)
            print(f"✅ Market data retrieved for {symbol}")
            print(f"Current price: ${float(ticker['price']):,.2f}")

            # Get 24hr ticker
            ticker_24hr = self.client.ticker_24hr_price_change(symbol=symbol)
            print(f"24hr change: {float(ticker_24hr['priceChangePercent']):.2f}%")
            print(f"24hr volume: {float(ticker_24hr['volume']):,.2f}")

            self.test_results["market_data"] = True
        except Exception as e:
            print(f"❌ Market data failed: {e}")
            self.test_results["market_data"] = False

    async def test_order_creation(self):
        """Test order creation (simulated)"""
        print("\n📝 Test 7: Order Creation (Simulated)")
        print("-" * 30)

        if not self.client:
            print("❌ Client not initialized")
            self.test_results["order_creation"] = False
            return

        try:
            # Test order creation with minimal quantity (simulated)
            symbol = "BTCUSDT"

            # Get symbol info for minimum quantity
            exchange_info = self.client.exchange_info()
            symbol_info = next(
                (s for s in exchange_info["symbols"] if s["symbol"] == symbol), None
            )

            if not symbol_info:
                print(f"❌ Symbol {symbol} not found")
                self.test_results["order_creation"] = False
                return

            # Find minimum quantity filter
            lot_size_filter = next(
                (f for f in symbol_info["filters"] if f["filterType"] == "LOT_SIZE"),
                None,
            )
            min_qty = float(lot_size_filter["minQty"]) if lot_size_filter else 0.001

            print(f"✅ Order creation test for {symbol}")
            print(f"Minimum quantity: {min_qty}")
            print("Note: This is a simulation - no actual order will be placed")

            # Simulate order parameters
            order_params = {
                "symbol": symbol,
                "side": SIDE_BUY,
                "type": ORDER_TYPE_LIMIT,
                "timeInForce": TIME_IN_FORCE_GTC,
                "quantity": min_qty,
                "price": "10000",  # Very low price that won't execute
            }

            print("Order parameters would be:")
            for key, value in order_params.items():
                print(f"  {key}: {value}")

            self.test_results["order_creation"] = True
        except Exception as e:
            print(f"❌ Order creation test failed: {e}")
            self.test_results["order_creation"] = False

    async def test_position_info(self):
        """Test position information"""
        print("\n📊 Test 8: Position Info")
        print("-" * 30)

        if not self.client:
            print("❌ Client not initialized")
            self.test_results["position_info"] = False
            return

        try:
            # Get position information
            positions = self.client.get_position_info()

            print("✅ Position info retrieved successfully")
            print(f"Total positions: {len(positions)}")

            # Show open positions
            open_positions = [
                p for p in positions if float(p.get("positionAmt", 0)) != 0
            ]
            if open_positions:
                print("Open positions:")
                for position in open_positions:
                    print(
                        f"  - {position['symbol']}: {position['positionAmt']} (P&L: {position.get('unRealizedProfit', '0')})"
                    )
            else:
                print("No open positions")

            self.test_results["position_info"] = True
        except Exception as e:
            print(f"❌ Position info failed: {e}")
            self.test_results["position_info"] = False

    def print_results(self):
        """Print test results summary"""
        print("\n" + "=" * 50)
        print("📋 TEST RESULTS SUMMARY")
        print("=" * 50)

        passed = sum(1 for result in self.test_results.values() if result)
        total = len(self.test_results)

        print(f"Tests Passed: {passed}/{total}")
        print(f"Success Rate: {(passed/total)*100:.1f}%")

        print("\nDetailed Results:")
        for test_name, result in self.test_results.items():
            status = "✅ PASS" if result else "❌ FAIL"
            print(f"  {test_name.replace('_', ' ').title()}: {status}")

        if passed == total:
            print(
                "\n🎉 All tests passed! Your Binance Futures testnet setup is working correctly."
            )
        else:
            print(f"\n⚠️  {total - passed} test(s) failed. Check the errors above.")
            print("\nCommon issues:")
            print("1. Invalid API keys")
            print("2. API keys not configured for futures trading")
            print("3. IP restrictions on API keys")
            print("4. Testnet API keys used on mainnet or vice versa")


async def main():
    """Main function"""
    tester = BinanceFuturesTestnetTester()
    await tester.run_all_tests()


if __name__ == "__main__":
    asyncio.run(main())
