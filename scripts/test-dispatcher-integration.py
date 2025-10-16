#!/usr/bin/env python3
"""
Dispatcher Integration Test with Binance Futures

This script tests the complete signal-to-order flow through the dispatcher
to validate that the tradeengine can properly convert signals into Binance orders.

Usage:
    python scripts/test-dispatcher-integration.py
"""

import asyncio
import logging
import os
import sys
from datetime import datetime

# Add the project root to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from contracts.order import OrderType
    from contracts.signal import Signal, StrategyMode, TimeInForce
    from tradeengine.dispatcher import Dispatcher
    from tradeengine.exchange.binance import BinanceFuturesExchange
except ImportError as e:
    print(f"❌ Missing required dependencies: {e}")
    print("Make sure you're running from the project root")
    sys.exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class DispatcherIntegrationTester:
    """Test dispatcher integration with Binance Futures"""

    def __init__(self):
        self.dispatcher = None
        self.exchange = None
        self.test_results = {}

    async def run_all_tests(self):
        """Run all integration tests"""
        print("🚀 Dispatcher Integration Test with Binance Futures")
        print("=" * 70)
        print(f"Timestamp: {datetime.utcnow().isoformat()}")
        print("=" * 70)

        # Test 1: Initialize exchange
        await self.test_exchange_initialization()

        # Test 2: Initialize dispatcher
        await self.test_dispatcher_initialization()

        # Test 3: Signal to order conversion
        await self.test_signal_to_order_conversion()

        # Test 4: Order amount calculation
        await self.test_order_amount_calculation()

        # Test 5: Dispatcher health check
        await self.test_dispatcher_health_check()

        # Test 6: Complete signal flow (without actual execution)
        await self.test_complete_signal_flow()

        # Cleanup
        await self.cleanup()

        # Print results
        self.print_results()

    async def test_exchange_initialization(self):
        """Test 1: Exchange initialization"""
        print("\n🔧 Test 1: Exchange Initialization")
        print("-" * 50)

        try:
            self.exchange = BinanceFuturesExchange()
            await self.exchange.initialize()

            print("✅ Binance Futures exchange initialized")
            print(f"Initialized: {self.exchange.initialized}")
            print(f"Symbols loaded: {len(self.exchange.symbol_info)}")

            self.test_results["exchange_init"] = True
        except Exception as e:
            print(f"❌ Exchange initialization failed: {e}")
            print(f"Error type: {type(e).__name__}")
            # Continue tests even if this fails
            self.exchange = None
            self.test_results["exchange_init"] = False

    async def test_dispatcher_initialization(self):
        """Test 2: Dispatcher initialization"""
        print("\n🎯 Test 2: Dispatcher Initialization")
        print("-" * 50)

        try:
            self.dispatcher = Dispatcher(exchange=self.exchange)
            await self.dispatcher.initialize()

            print("✅ Dispatcher initialized successfully")
            print(f"Order Manager: {type(self.dispatcher.order_manager).__name__}")
            print(
                f"Position Manager: {type(self.dispatcher.position_manager).__name__}"
            )
            print(
                f"Signal Aggregator: {type(self.dispatcher.signal_aggregator).__name__}"
            )

            self.test_results["dispatcher_init"] = True
        except Exception as e:
            print(f"❌ Dispatcher initialization failed: {e}")
            self.test_results["dispatcher_init"] = False
            raise

    async def test_signal_to_order_conversion(self):
        """Test 3: Signal to order conversion"""
        print("\n🔄 Test 3: Signal to Order Conversion")
        print("-" * 50)

        try:
            # Create a test signal
            test_signal = Signal(
                strategy_id="test_strategy",
                symbol="BTCUSDT",
                action="buy",
                price=50000.0,
                quantity=0.001,
                current_price=50000.0,
                source="test",
                strategy="test_strategy",
                confidence=0.85,
                timeframe="1h",
                timestamp=datetime.utcnow(),
                order_type=OrderType.LIMIT,
                stop_loss=49000.0,
                take_profit=51000.0,
                time_in_force=TimeInForce.GTC,
                strategy_mode=StrategyMode.DETERMINISTIC,
            )

            print("Created test signal:")
            print(f"  Strategy: {test_signal.strategy_id}")
            print(f"  Symbol: {test_signal.symbol}")
            print(f"  Action: {test_signal.action}")
            print(f"  Price: ${test_signal.current_price:,.2f}")
            print(f"  Quantity: {test_signal.quantity}")

            # Convert to order
            order = self.dispatcher._signal_to_order(test_signal)

            print("\n✅ Signal converted to order:")
            print(f"  Order ID: {order.order_id}")
            print(f"  Symbol: {order.symbol}")
            print(f"  Side: {order.side}")
            print(f"  Type: {order.type}")
            print(f"  Amount: {order.amount}")
            print(f"  Target Price: {order.target_price}")
            print(f"  Stop Loss: {order.stop_loss}")
            print(f"  Take Profit: {order.take_profit}")

            self.test_results["signal_to_order"] = True
        except Exception as e:
            print(f"❌ Signal to order conversion failed: {e}")
            self.test_results["signal_to_order"] = False

    async def test_order_amount_calculation(self):
        """Test 4: Order amount calculation"""
        print("\n💰 Test 4: Order Amount Calculation")
        print("-" * 50)

        try:
            # Create signal without quantity (should use minimum)
            test_signal = Signal(
                strategy_id="test_strategy",
                symbol="BTCUSDT",
                action="buy",
                price=50000.0,
                quantity=0.0,  # Will trigger automatic minimum calculation
                current_price=50000.0,
                source="test",
                strategy="test_strategy",
                confidence=0.85,
                timeframe="1h",
                timestamp=datetime.utcnow(),
                order_type=OrderType.MARKET,
                strategy_mode=StrategyMode.DETERMINISTIC,
            )

            print("Testing automatic minimum amount calculation...")
            print(f"Signal quantity: {test_signal.quantity}")
            print(f"Current price: ${test_signal.current_price:,.2f}")

            # Calculate amount
            amount = self.dispatcher._calculate_order_amount(test_signal)

            print("\n✅ Order amount calculated:")
            print(f"  Calculated amount: {amount}")
            print(f"  Symbol: {test_signal.symbol}")

            # Verify it meets minimum requirements
            if self.exchange and self.exchange.initialized:
                min_info = self.exchange.get_min_order_amount("BTCUSDT")
                print("\n  Minimum requirements:")
                print(f"    Min Qty: {min_info['min_qty']}")
                print(f"    Min Notional: ${min_info['min_notional']}")
                print(f"  Meets requirements: {amount >= min_info['min_qty']}")

            self.test_results["order_amount_calc"] = True
        except Exception as e:
            print(f"❌ Order amount calculation failed: {e}")
            print(f"Error: {str(e)}")
            self.test_results["order_amount_calc"] = False

    async def test_dispatcher_health_check(self):
        """Test 5: Dispatcher health check"""
        print("\n🏥 Test 5: Dispatcher Health Check")
        print("-" * 50)

        try:
            health = await self.dispatcher.health_check()

            print("✅ Health check completed:")
            print(f"  Status: {health.get('status')}")
            print("\n  Components:")
            for component, status in health.get("components", {}).items():
                if isinstance(status, dict):
                    print(f"    {component}: {status.get('status', status)}")
                else:
                    print(f"    {component}: {status}")

            self.test_results["health_check"] = True
        except Exception as e:
            print(f"❌ Health check failed: {e}")
            self.test_results["health_check"] = False

    async def test_complete_signal_flow(self):
        """Test 6: Complete signal flow (without execution)"""
        print("\n🔄 Test 6: Complete Signal Flow (Simulation)")
        print("-" * 50)

        try:
            # Create a simulated signal
            test_signal = Signal(
                strategy_id="test_integration",
                symbol="BTCUSDT",
                action="buy",
                price=50000.0,
                quantity=0.001,
                current_price=50000.0,
                source="test",
                strategy="test_integration",
                confidence=0.90,
                timeframe="1h",
                timestamp=datetime.utcnow(),
                order_type=OrderType.LIMIT,
                time_in_force=TimeInForce.GTC,
                strategy_mode=StrategyMode.DETERMINISTIC,
                meta={"simulate": True},  # Force simulation mode
            )

            print("Processing signal through dispatcher:")
            print(f"  Strategy: {test_signal.strategy_id}")
            print(f"  Symbol: {test_signal.symbol}")
            print(f"  Action: {test_signal.action}")
            print("  Mode: SIMULATED")

            # Dispatch the signal
            result = await self.dispatcher.dispatch(test_signal)

            print("\n✅ Signal processing completed:")
            print(f"  Status: {result.get('status')}")
            if "reason" in result:
                print(f"  Reason: {result.get('reason')}")
            if "execution_result" in result:
                exec_result = result["execution_result"]
                print(f"  Execution Status: {exec_result.get('status')}")
                if "simulated" in exec_result:
                    print(f"  Simulated: {exec_result.get('simulated')}")

            self.test_results["complete_flow"] = True
        except Exception as e:
            print(f"❌ Complete signal flow failed: {e}")
            print(f"Error: {str(e)}")
            import traceback

            traceback.print_exc()
            self.test_results["complete_flow"] = False

    async def cleanup(self):
        """Cleanup resources"""
        print("\n🧹 Cleanup")
        print("-" * 50)

        try:
            if self.dispatcher:
                await self.dispatcher.close()
                print("✅ Dispatcher closed")

            if self.exchange:
                await self.exchange.close()
                print("✅ Exchange connection closed")
        except Exception as e:
            print(f"⚠️  Cleanup warning: {e}")

    def print_results(self):
        """Print test results summary"""
        print("\n" + "=" * 70)
        print("📋 TEST RESULTS SUMMARY")
        print("=" * 70)

        passed = sum(1 for result in self.test_results.values() if result is True)
        failed = sum(1 for result in self.test_results.values() if result is False)
        total = len(self.test_results)

        print(f"\nTests Passed: {passed}/{total}")
        print(f"Tests Failed: {failed}/{total}")
        if total > 0:
            print(f"Success Rate: {(passed/total)*100:.1f}%")

        print("\nDetailed Results:")
        for test_name, result in self.test_results.items():
            status = "✅ PASS" if result else "❌ FAIL"
            print(f"  {test_name.replace('_', ' ').title()}: {status}")

        print("\n" + "=" * 70)
        if failed == 0 and passed > 0:
            print("🎉 All integration tests passed!")
            print(
                "✅ Dispatcher can successfully process signals and convert them to orders."
            )
        else:
            print(f"⚠️  {failed} test(s) failed. Review the errors above.")

        print("=" * 70)


async def main():
    """Main function"""
    tester = DispatcherIntegrationTester()
    await tester.run_all_tests()


if __name__ == "__main__":
    # Set up environment
    os.environ.setdefault("BINANCE_TESTNET", "true")
    os.environ.setdefault("SIMULATION_ENABLED", "true")
    os.environ.setdefault("LOG_LEVEL", "INFO")

    asyncio.run(main())
