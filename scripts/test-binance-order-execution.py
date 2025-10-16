#!/usr/bin/env python3
"""
Comprehensive Binance Futures Order Execution Test

This script tests actual order execution on Binance Futures testnet
using the same methods and configuration as the production tradeengine.

Usage:
    python scripts/test-binance-order-execution.py
"""

import asyncio
import logging
import os
import sys
from datetime import datetime

# Add the project root to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from binance import Client
    from binance.exceptions import BinanceAPIException
except ImportError as e:
    print(f"❌ Missing required dependencies: {e}")
    print("Install with: pip install python-binance")
    sys.exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class BinanceFuturesValidator:
    """Comprehensive Binance Futures validation for testnet changes"""

    def __init__(self):
        self.client = None
        self.test_results = {}
        self.api_key = os.getenv("BINANCE_API_KEY")
        self.api_secret = os.getenv("BINANCE_API_SECRET")
        self.testnet = os.getenv("BINANCE_TESTNET", "true").lower() == "true"
        self.test_symbol = "BTCUSDT"
        self.created_orders = []

    async def run_all_tests(self):
        """Run all validation tests"""
        print("🚀 Binance Futures Testnet Validation")
        print("=" * 70)
        print(f"Test Symbol: {self.test_symbol}")
        print(f"Testnet Mode: {self.testnet}")
        print(f"Timestamp: {datetime.utcnow().isoformat()}")
        print("=" * 70)

        # Phase 1: Basic Connectivity
        await self.test_environment_config()
        await self.test_client_initialization()
        await self.test_connection()
        await self.test_server_time()

        # Phase 2: Market Data and Info
        await self.test_exchange_info()
        await self.test_account_info()
        await self.test_market_data()
        await self.test_position_info()

        # Phase 3: Order Execution (Real testnet orders)
        await self.test_market_order_validation()
        await self.test_limit_order_creation()
        await self.test_order_query()
        await self.test_order_cancellation()

        # Phase 4: Symbol Filters and Precision
        await self.test_symbol_filters()
        await self.test_min_order_amounts()

        # Phase 5: Futures-Specific Features
        await self.test_leverage_brackets()
        await self.test_account_trades()
        await self.test_income_history()

        # Cleanup
        await self.cleanup_test_orders()

        # Print Results
        self.print_results()

    async def test_environment_config(self):
        """Test 1: Environment configuration"""
        print("\n📋 Test 1: Environment Configuration")
        print("-" * 50)

        api_key_present = bool(self.api_key)
        api_secret_present = bool(self.api_secret)

        print(f"API Key Present: {'✅' if api_key_present else '❌'}")
        if api_key_present:
            print(f"API Key: {self.api_key[:8]}...{self.api_key[-8:]}")
        print(f"API Secret Present: {'✅' if api_secret_present else '❌'}")
        print(f"Testnet Enabled: {'✅' if self.testnet else '❌'}")

        if not api_key_present or not api_secret_present:
            print("❌ Missing API credentials!")
            self.test_results["environment_config"] = False
            raise RuntimeError("API credentials not configured")

        print("✅ Environment configured correctly")
        self.test_results["environment_config"] = True

    async def test_client_initialization(self):
        """Test 2: Futures client initialization"""
        print("\n🔧 Test 2: Client Initialization")
        print("-" * 50)

        try:
            self.client = Client(
                api_key=self.api_key, api_secret=self.api_secret, testnet=self.testnet
            )
            print("✅ Binance Futures client initialized successfully")
            print(f"Client Type: {type(self.client).__name__}")
            self.test_results["client_init"] = True
        except Exception as e:
            print(f"❌ Client initialization failed: {e}")
            self.test_results["client_init"] = False
            raise

    async def test_connection(self):
        """Test 3: API connection"""
        print("\n🔌 Test 3: API Connection (Ping)")
        print("-" * 50)

        try:
            response = self.client.futures_ping()
            print("✅ Futures API connection successful")
            print(f"Response: {response}")
            self.test_results["connection"] = True
        except Exception as e:
            print(f"❌ API connection failed: {e}")
            self.test_results["connection"] = False
            raise

    async def test_server_time(self):
        """Test 4: Server time"""
        print("\n⏰ Test 4: Server Time Sync")
        print("-" * 50)

        try:
            server_time = self.client.get_server_time()
            local_time = int(datetime.utcnow().timestamp() * 1000)
            time_diff = abs(server_time["serverTime"] - local_time)

            print("✅ Server time retrieved")
            print(f"Server Time: {server_time['serverTime']}")
            print(f"Local Time: {local_time}")
            print(f"Time Diff: {time_diff}ms")

            if time_diff > 5000:
                print("⚠️  Warning: Time difference > 5 seconds, may cause issues")

            self.test_results["server_time"] = True
        except Exception as e:
            print(f"❌ Server time test failed: {e}")
            self.test_results["server_time"] = False

    async def test_exchange_info(self):
        """Test 5: Exchange information"""
        print("\n📊 Test 5: Exchange Info")
        print("-" * 50)

        try:
            exchange_info = self.client.futures_exchange_info()
            symbols = exchange_info.get("symbols", [])

            print("✅ Exchange info retrieved successfully")
            print(f"Total symbols: {len(symbols)}")
            print(f"Timezone: {exchange_info.get('timezone')}")

            # Find BTCUSDT
            btc_symbol = next(
                (s for s in symbols if s["symbol"] == self.test_symbol), None
            )
            if btc_symbol:
                print(f"\n{self.test_symbol} Symbol Info:")
                print(f"  Status: {btc_symbol['status']}")
                print(f"  Base Asset: {btc_symbol['baseAsset']}")
                print(f"  Quote Asset: {btc_symbol['quoteAsset']}")
                print(f"  Filters: {len(btc_symbol.get('filters', []))} filters")

            self.test_results["exchange_info"] = True
        except Exception as e:
            print(f"❌ Exchange info failed: {e}")
            self.test_results["exchange_info"] = False

    async def test_account_info(self):
        """Test 6: Account information"""
        print("\n👤 Test 6: Account Info")
        print("-" * 50)

        try:
            account_info = self.client.futures_account()

            print("✅ Account info retrieved successfully")
            print(f"Can Trade: {account_info.get('canTrade', 'Unknown')}")
            print(f"Can Withdraw: {account_info.get('canWithdraw', 'Unknown')}")
            print(f"Can Deposit: {account_info.get('canDeposit', 'Unknown')}")
            print(
                f"Total Wallet Balance: {account_info.get('totalWalletBalance', 0)} USDT"
            )
            print(f"Available Balance: {account_info.get('availableBalance', 0)} USDT")
            print(
                f"Total Unrealized PnL: {account_info.get('totalUnrealizedProfit', 0)} USDT"
            )

            self.test_results["account_info"] = True
        except Exception as e:
            print(f"❌ Account info failed: {e}")
            self.test_results["account_info"] = False

    async def test_market_data(self):
        """Test 7: Market data"""
        print("\n📈 Test 7: Market Data")
        print("-" * 50)

        try:
            # Get ticker price
            ticker = self.client.futures_symbol_ticker(symbol=self.test_symbol)
            print(f"✅ Current {self.test_symbol} price: ${float(ticker['price']):,.2f}")

            # Get 24hr ticker
            ticker_24hr = self.client.futures_ticker(symbol=self.test_symbol)
            print(f"24hr Change: {float(ticker_24hr['priceChangePercent']):.2f}%")
            print(f"24hr Volume: {float(ticker_24hr['volume']):,.2f}")
            print(f"24hr High: ${float(ticker_24hr['highPrice']):,.2f}")
            print(f"24hr Low: ${float(ticker_24hr['lowPrice']):,.2f}")

            self.test_results["market_data"] = True
        except Exception as e:
            print(f"❌ Market data failed: {e}")
            self.test_results["market_data"] = False

    async def test_position_info(self):
        """Test 8: Position information"""
        print("\n📊 Test 8: Position Info")
        print("-" * 50)

        try:
            positions = self.client.futures_position_information()
            print(f"✅ Retrieved {len(positions)} positions")

            # Find BTCUSDT position
            btc_position = next(
                (p for p in positions if p["symbol"] == self.test_symbol), None
            )
            if btc_position:
                print(f"\n{self.test_symbol} Position:")
                print(f"  Position Amount: {btc_position.get('positionAmt', 0)}")
                print(f"  Entry Price: {btc_position.get('entryPrice', 0)}")
                print(
                    f"  Unrealized PnL: {btc_position.get('unRealizedProfit', 0)} USDT"
                )
                print(f"  Leverage: {btc_position.get('leverage', 0)}x")
                print(f"  Margin Type: {btc_position.get('marginType', 'Unknown')}")

            self.test_results["position_info"] = True
        except Exception as e:
            print(f"❌ Position info failed: {e}")
            self.test_results["position_info"] = False

    async def test_market_order_validation(self):
        """Test 9: Market order validation (no execution)"""
        print("\n🧪 Test 9: Market Order Validation")
        print("-" * 50)

        try:
            # Get symbol info for minimum quantity
            exchange_info = self.client.futures_exchange_info()
            symbol_info = next(
                (
                    s
                    for s in exchange_info["symbols"]
                    if s["symbol"] == self.test_symbol
                ),
                None,
            )

            if not symbol_info:
                raise ValueError(f"Symbol {self.test_symbol} not found")

            # Find minimum quantity filter
            lot_size_filter = next(
                (f for f in symbol_info["filters"] if f["filterType"] == "LOT_SIZE"),
                None,
            )
            min_qty = float(lot_size_filter["minQty"]) if lot_size_filter else 0.001

            print("✅ Market order parameters validated")
            print(f"Symbol: {self.test_symbol}")
            print(f"Minimum Quantity: {min_qty}")
            print(f"Status: {symbol_info['status']}")

            self.test_results["market_order_validation"] = True
        except Exception as e:
            print(f"❌ Market order validation failed: {e}")
            self.test_results["market_order_validation"] = False

    async def test_limit_order_creation(self):
        """Test 10: Create limit order (real testnet order)"""
        print("\n📝 Test 10: Limit Order Creation (Real Testnet Order)")
        print("-" * 50)

        try:
            # Get current price
            ticker = self.client.futures_symbol_ticker(symbol=self.test_symbol)
            current_price = float(ticker["price"])

            # Get minimum quantity
            exchange_info = self.client.futures_exchange_info()
            symbol_info = next(
                (
                    s
                    for s in exchange_info["symbols"]
                    if s["symbol"] == self.test_symbol
                ),
                None,
            )
            lot_size_filter = next(
                (f for f in symbol_info["filters"] if f["filterType"] == "LOT_SIZE"),
                None,
            )
            min_qty = float(lot_size_filter["minQty"]) if lot_size_filter else 0.001

            # Create limit order at 50% of current price (won't fill)
            limit_price = round(current_price * 0.5, 2)

            print("Creating LIMIT BUY order:")
            print(f"  Symbol: {self.test_symbol}")
            print(f"  Quantity: {min_qty}")
            print(f"  Price: ${limit_price:,.2f} (current: ${current_price:,.2f})")

            order = self.client.futures_create_order(
                symbol=self.test_symbol,
                side="BUY",
                type="LIMIT",
                timeInForce="GTC",
                quantity=min_qty,
                price=str(limit_price),
            )

            self.created_orders.append(
                {"orderId": order["orderId"], "symbol": self.test_symbol}
            )

            print("✅ Limit order created successfully")
            print(f"  Order ID: {order['orderId']}")
            print(f"  Status: {order['status']}")
            print(f"  Client Order ID: {order.get('clientOrderId', 'N/A')}")

            self.test_results["limit_order_creation"] = True
            return order["orderId"]
        except BinanceAPIException as e:
            print(f"❌ Binance API error: {e.code} - {e.message}")
            self.test_results["limit_order_creation"] = False
            return None
        except Exception as e:
            print(f"❌ Limit order creation failed: {e}")
            self.test_results["limit_order_creation"] = False
            return None

    async def test_order_query(self):
        """Test 11: Query order status"""
        print("\n🔍 Test 11: Order Query")
        print("-" * 50)

        if not self.created_orders:
            print("⚠️  No orders to query (skipped)")
            self.test_results["order_query"] = None
            return

        try:
            order_id = self.created_orders[0]["orderId"]
            symbol = self.created_orders[0]["symbol"]

            order = self.client.futures_get_order(symbol=symbol, orderId=order_id)

            print("✅ Order retrieved successfully")
            print(f"  Order ID: {order['orderId']}")
            print(f"  Symbol: {order['symbol']}")
            print(f"  Status: {order['status']}")
            print(f"  Type: {order['type']}")
            print(f"  Side: {order['side']}")
            print(f"  Price: {order['price']}")
            print(f"  Quantity: {order['origQty']}")
            print(f"  Executed Qty: {order['executedQty']}")

            self.test_results["order_query"] = True
        except Exception as e:
            print(f"❌ Order query failed: {e}")
            self.test_results["order_query"] = False

    async def test_order_cancellation(self):
        """Test 12: Cancel order"""
        print("\n❌ Test 12: Order Cancellation")
        print("-" * 50)

        if not self.created_orders:
            print("⚠️  No orders to cancel (skipped)")
            self.test_results["order_cancellation"] = None
            return

        try:
            order_id = self.created_orders[0]["orderId"]
            symbol = self.created_orders[0]["symbol"]

            result = self.client.futures_cancel_order(symbol=symbol, orderId=order_id)

            print("✅ Order cancelled successfully")
            print(f"  Order ID: {result['orderId']}")
            print(f"  Symbol: {result['symbol']}")
            print(f"  Status: {result['status']}")

            self.test_results["order_cancellation"] = True
        except Exception as e:
            print(f"❌ Order cancellation failed: {e}")
            self.test_results["order_cancellation"] = False

    async def test_symbol_filters(self):
        """Test 13: Symbol filters and precision"""
        print("\n🔬 Test 13: Symbol Filters")
        print("-" * 50)

        try:
            exchange_info = self.client.futures_exchange_info()
            symbol_info = next(
                (
                    s
                    for s in exchange_info["symbols"]
                    if s["symbol"] == self.test_symbol
                ),
                None,
            )

            if not symbol_info:
                raise ValueError(f"Symbol {self.test_symbol} not found")

            print(f"✅ Symbol filters for {self.test_symbol}:")
            for filter_info in symbol_info["filters"]:
                filter_type = filter_info["filterType"]
                print(f"\n  {filter_type}:")
                for key, value in filter_info.items():
                    if key != "filterType":
                        print(f"    {key}: {value}")

            self.test_results["symbol_filters"] = True
        except Exception as e:
            print(f"❌ Symbol filters test failed: {e}")
            self.test_results["symbol_filters"] = False

    async def test_min_order_amounts(self):
        """Test 14: Minimum order amounts calculation"""
        print("\n💰 Test 14: Minimum Order Amounts")
        print("-" * 50)

        try:
            exchange_info = self.client.futures_exchange_info()
            symbol_info = next(
                (
                    s
                    for s in exchange_info["symbols"]
                    if s["symbol"] == self.test_symbol
                ),
                None,
            )

            filters = symbol_info["filters"]

            # LOT_SIZE filter
            lot_size = next((f for f in filters if f["filterType"] == "LOT_SIZE"), None)
            # MIN_NOTIONAL filter
            min_notional = next(
                (f for f in filters if f["filterType"] == "MIN_NOTIONAL"), None
            )
            # PRICE_FILTER
            price_filter = next(
                (f for f in filters if f["filterType"] == "PRICE_FILTER"), None
            )

            print(f"✅ Minimum order requirements for {self.test_symbol}:")
            if lot_size:
                print("\n  LOT_SIZE:")
                print(f"    Min Qty: {lot_size.get('minQty')}")
                print(f"    Max Qty: {lot_size.get('maxQty')}")
                print(f"    Step Size: {lot_size.get('stepSize')}")

            if min_notional:
                print("\n  MIN_NOTIONAL:")
                print(f"    Notional: {min_notional.get('notional')}")

            if price_filter:
                print("\n  PRICE_FILTER:")
                print(f"    Min Price: {price_filter.get('minPrice')}")
                print(f"    Max Price: {price_filter.get('maxPrice')}")
                print(f"    Tick Size: {price_filter.get('tickSize')}")

            self.test_results["min_order_amounts"] = True
        except Exception as e:
            print(f"❌ Min order amounts test failed: {e}")
            self.test_results["min_order_amounts"] = False

    async def test_leverage_brackets(self):
        """Test 15: Leverage brackets"""
        print("\n⚖️  Test 15: Leverage Brackets")
        print("-" * 50)

        try:
            brackets = self.client.futures_leverage_bracket(symbol=self.test_symbol)

            print(f"✅ Leverage brackets retrieved for {self.test_symbol}")
            if isinstance(brackets, list) and len(brackets) > 0:
                symbol_brackets = brackets[0] if "brackets" in brackets[0] else brackets
                if "brackets" in symbol_brackets:
                    print("\nBrackets:")
                    for bracket in symbol_brackets["brackets"][:5]:  # Show first 5
                        print(f"  Leverage: {bracket.get('initialLeverage')}x")
                        print(f"    Notional Cap: {bracket.get('notionalCap')}")
                        print(
                            f"    Maintenance Margin: {bracket.get('maintMarginRatio')}"
                        )

            self.test_results["leverage_brackets"] = True
        except Exception as e:
            print(f"❌ Leverage brackets test failed: {e}")
            self.test_results["leverage_brackets"] = False

    async def test_account_trades(self):
        """Test 16: Account trades"""
        print("\n💱 Test 16: Account Trades")
        print("-" * 50)

        try:
            trades = self.client.futures_account_trades(
                symbol=self.test_symbol, limit=5
            )

            print(f"✅ Retrieved {len(trades)} recent trades for {self.test_symbol}")
            if trades:
                print("\nMost recent trade:")
                trade = trades[0]
                print(f"  Price: {trade.get('price')}")
                print(f"  Quantity: {trade.get('qty')}")
                print(f"  Side: {trade.get('side', 'Unknown')}")
                print(f"  Realized PnL: {trade.get('realizedPnl', 0)}")
            else:
                print("No trades found")

            self.test_results["account_trades"] = True
        except Exception as e:
            print(f"❌ Account trades test failed: {e}")
            self.test_results["account_trades"] = False

    async def test_income_history(self):
        """Test 17: Income history"""
        print("\n💵 Test 17: Income History")
        print("-" * 50)

        try:
            income = self.client.futures_income_history(
                symbol=self.test_symbol, limit=5
            )

            print(f"✅ Retrieved {len(income)} income records")
            if income:
                print("\nMost recent income:")
                inc = income[0]
                print(f"  Type: {inc.get('incomeType')}")
                print(f"  Income: {inc.get('income')}")
                print(f"  Asset: {inc.get('asset')}")
            else:
                print("No income history found")

            self.test_results["income_history"] = True
        except Exception as e:
            print(f"❌ Income history test failed: {e}")
            self.test_results["income_history"] = False

    async def cleanup_test_orders(self):
        """Cleanup any remaining test orders"""
        print("\n🧹 Cleanup: Cancelling Test Orders")
        print("-" * 50)

        for order in self.created_orders:
            try:
                # Check if order is still open
                order_status = self.client.futures_get_order(
                    symbol=order["symbol"], orderId=order["orderId"]
                )
                if order_status["status"] in ["NEW", "PARTIALLY_FILLED"]:
                    self.client.futures_cancel_order(
                        symbol=order["symbol"], orderId=order["orderId"]
                    )
                    print(f"✅ Cancelled order {order['orderId']}")
            except Exception as e:
                print(f"⚠️  Could not cancel order {order['orderId']}: {e}")

    def print_results(self):
        """Print test results summary"""
        print("\n" + "=" * 70)
        print("📋 TEST RESULTS SUMMARY")
        print("=" * 70)

        # Count results
        passed = sum(1 for result in self.test_results.values() if result is True)
        failed = sum(1 for result in self.test_results.values() if result is False)
        skipped = sum(1 for result in self.test_results.values() if result is None)
        total = len(self.test_results)

        print(f"\nTests Passed: {passed}/{total}")
        print(f"Tests Failed: {failed}/{total}")
        print(f"Tests Skipped: {skipped}/{total}")
        if total > 0:
            print(f"Success Rate: {(passed/total)*100:.1f}%")

        print("\nDetailed Results:")
        for test_name, result in self.test_results.items():
            if result is True:
                status = "✅ PASS"
            elif result is False:
                status = "❌ FAIL"
            else:
                status = "⏭️  SKIP"
            print(f"  {test_name.replace('_', ' ').title()}: {status}")

        print("\n" + "=" * 70)
        if failed == 0 and passed > 0:
            print("🎉 All tests passed! Binance Futures testnet is working correctly.")
            print(
                "✅ Your tradeengine configuration is valid for the latest Binance API."
            )
        elif failed > 0:
            print(f"⚠️  {failed} test(s) failed. Review the errors above.")
            print("\nCommon issues after Binance testnet changes:")
            print("1. API endpoint URL changes")
            print("2. New or modified filters on symbols")
            print("3. Changed minimum order requirements")
            print("4. API key permissions or IP restrictions")
        print("=" * 70)


async def main():
    """Main function"""
    validator = BinanceFuturesValidator()
    await validator.run_all_tests()


if __name__ == "__main__":
    asyncio.run(main())
