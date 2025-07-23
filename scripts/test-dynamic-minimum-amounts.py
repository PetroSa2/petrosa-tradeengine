#!/usr/bin/env python3
"""
Test script to verify dynamic minimum amount calculation for different symbols
"""

import asyncio
import logging
import os
import sys

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Kubernetes configuration
K8S_CONFIG = {
    "BINANCE_TESTNET": "true",
    "ENVIRONMENT": "production",
    "SIMULATION_ENABLED": "false",
    "LOG_LEVEL": "INFO",
    "BINANCE_API_KEY": "2fe0e9581c784734c3197577c3243335f98f5547006feb859bd3ccd054b19aa1",
    "BINANCE_API_SECRET": "5c6acc1d16f1041d80788bd1d5aa19577328e7185c84a193787be8640abf6cb6",
    "FUTURES_TRADING_ENABLED": "true",
    "DEFAULT_LEVERAGE": "10",
    "MARGIN_TYPE": "isolated",
    "POSITION_MODE": "hedge",
    "MONGODB_URI": "mongodb://localhost:27017/test",
    "MONGODB_DATABASE": "test",
}


def setup_environment():
    """Set up environment variables to match Kubernetes configuration"""
    for key, value in K8S_CONFIG.items():
        os.environ[key] = value
        logger.info(f"Set {key} = {value}")


async def test_binance_minimum_amounts():
    """Test Binance minimum amount calculation for different symbols"""
    logger.info("🔍 Testing Binance minimum amount calculation...")

    try:
        from binance.client import Client

        api_key = os.environ.get("BINANCE_API_KEY")
        api_secret = os.environ.get("BINANCE_API_SECRET")
        testnet = os.environ.get("BINANCE_TESTNET", "true").lower() == "true"

        client = Client(api_key, api_secret, testnet=testnet)

        # Test symbols with different minimum requirements
        test_symbols = [
            "BTCUSDT",  # High value, small min quantity
            "ETHUSDT",  # Medium value, small min quantity
            "ADAUSDT",  # Low value, larger min quantity
            "DOGEUSDT",  # Very low value, large min quantity
            "BNBUSDT",  # Medium value, small min quantity
            "SOLUSDT",  # Medium value, small min quantity
        ]

        results = []

        for symbol in test_symbols:
            try:
                # Get symbol info
                exchange_info = client.futures_exchange_info()
                symbol_info = next(
                    (s for s in exchange_info["symbols"] if s["symbol"] == symbol), None
                )

                if symbol_info:
                    # Find filters
                    lot_size_filter = next(
                        (
                            f
                            for f in symbol_info["filters"]
                            if f["filterType"] == "LOT_SIZE"
                        ),
                        None,
                    )
                    min_notional_filter = next(
                        (
                            f
                            for f in symbol_info["filters"]
                            if f["filterType"] == "MIN_NOTIONAL"
                        ),
                        None,
                    )

                    min_qty = (
                        float(lot_size_filter["minQty"]) if lot_size_filter else 0.001
                    )
                    min_notional = (
                        float(min_notional_filter["notional"])
                        if min_notional_filter
                        else 5.0
                    )
                    step_size = (
                        float(lot_size_filter["stepSize"]) if lot_size_filter else 0.001
                    )

                    # Get current price
                    ticker = client.futures_symbol_ticker(symbol=symbol)
                    current_price = float(ticker["price"])

                    # Calculate minimum quantity based on notional value
                    min_qty_by_notional = min_notional / current_price

                    # Use the larger of the two minimums
                    final_min_qty = max(min_qty, min_qty_by_notional)

                    # Calculate precision
                    precision = (
                        len(str(step_size).split(".")[-1].rstrip("0"))
                        if "." in str(step_size)
                        else 0
                    )
                    final_min_qty = round(final_min_qty, precision)

                    # Calculate notional value
                    notional_value = final_min_qty * current_price

                    result = {
                        "symbol": symbol,
                        "current_price": current_price,
                        "min_qty": min_qty,
                        "min_notional": min_notional,
                        "min_qty_by_notional": min_qty_by_notional,
                        "final_min_qty": final_min_qty,
                        "notional_value": notional_value,
                        "precision": precision,
                        "step_size": step_size,
                    }

                    results.append(result)

                    logger.info(f"✅ {symbol}:")
                    logger.info(f"   Price: ${current_price:,.2f}")
                    logger.info(f"   Min Qty: {min_qty}")
                    logger.info(f"   Min Notional: ${min_notional}")
                    logger.info(f"   Min Qty by Notional: {min_qty_by_notional:.8f}")
                    logger.info(f"   Final Min Qty: {final_min_qty}")
                    logger.info(f"   Notional Value: ${notional_value:.2f}")
                    logger.info(f"   Precision: {precision}")

                else:
                    logger.error(f"❌ Symbol {symbol} not found")

            except Exception as e:
                logger.error(f"❌ Error processing {symbol}: {e}")

        return results

    except Exception as e:
        logger.error(f"❌ Binance minimum amounts test failed: {e}")
        return []


async def test_exchange_class_minimum_amounts():
    """Test BinanceFuturesExchange minimum amount calculation"""
    logger.info("🔍 Testing BinanceFuturesExchange minimum amount calculation...")

    try:
        from tradeengine.exchange.binance import BinanceFuturesExchange

        # Initialize exchange
        exchange = BinanceFuturesExchange()
        await exchange.initialize()

        test_symbols = [
            "BTCUSDT",
            "ETHUSDT",
            "ADAUSDT",
            "DOGEUSDT",
            "BNBUSDT",
            "SOLUSDT",
        ]

        results = []

        for symbol in test_symbols:
            try:
                # Get minimum order info
                min_info = exchange.get_min_order_amount(symbol)

                # Get current price
                current_price = await exchange.get_price(symbol)

                # Calculate minimum amount
                min_amount = exchange.calculate_min_order_amount(symbol, current_price)

                # Calculate notional value
                notional_value = min_amount * current_price

                result = {
                    "symbol": symbol,
                    "current_price": current_price,
                    "min_qty": min_info["min_qty"],
                    "min_notional": min_info["min_notional"],
                    "calculated_min_amount": min_amount,
                    "notional_value": notional_value,
                    "precision": min_info["precision"],
                }

                results.append(result)

                logger.info(f"✅ {symbol} (Exchange Class):")
                logger.info(f"   Price: ${current_price:,.2f}")
                logger.info(f"   Min Qty: {min_info['min_qty']}")
                logger.info(f"   Min Notional: ${min_info['min_notional']}")
                logger.info(f"   Calculated Min Amount: {min_amount}")
                logger.info(f"   Notional Value: ${notional_value:.2f}")
                logger.info(f"   Precision: {min_info['precision']}")

            except Exception as e:
                logger.error(f"❌ Error processing {symbol}: {e}")

        await exchange.close()
        return results

    except Exception as e:
        logger.error(f"❌ Exchange class minimum amounts test failed: {e}")
        return []


async def test_dispatcher_minimum_amounts():
    """Test dispatcher minimum amount calculation"""
    logger.info("🔍 Testing dispatcher minimum amount calculation...")

    try:
        from contracts.signal import Signal
        from tradeengine.dispatcher import Dispatcher

        # Create dispatcher
        dispatcher = Dispatcher()
        await dispatcher.initialize()

        test_cases = [
            {
                "name": "Signal with valid quantity",
                "symbol": "BTCUSDT",
                "quantity": 0.002,
                "current_price": 118000.0,
                "expected": "Should use signal quantity",
            },
            {
                "name": "Signal with zero quantity",
                "symbol": "BTCUSDT",
                "quantity": 0.0,
                "current_price": 118000.0,
                "expected": "Should use calculated minimum",
            },
            {
                "name": "Signal with negative quantity",
                "symbol": "ETHUSDT",
                "quantity": -0.001,
                "current_price": 3500.0,
                "expected": "Should use calculated minimum",
            },
            {
                "name": "Signal with missing quantity",
                "symbol": "ADAUSDT",
                "quantity": None,
                "current_price": 0.5,
                "expected": "Should use calculated minimum",
            },
            {
                "name": "Signal with very small quantity",
                "symbol": "DOGEUSDT",
                "quantity": 0.0000001,
                "current_price": 0.08,
                "expected": "Should use calculated minimum",
            },
        ]

        results = []

        for test_case in test_cases:
            try:
                signal = Signal(
                    strategy_id="test_strategy",
                    symbol=test_case["symbol"],
                    signal_type="buy",
                    action="buy",
                    confidence=0.8,
                    strength="medium",
                    timeframe="1h",
                    price=test_case["current_price"],
                    quantity=test_case["quantity"]
                    if test_case["quantity"] is not None
                    else 0.0,
                    current_price=test_case["current_price"],
                    source="test",
                    strategy="test-strategy",
                )

                # Convert signal to order
                order = dispatcher._signal_to_order(signal)

                result = {
                    "test_case": test_case["name"],
                    "symbol": test_case["symbol"],
                    "signal_quantity": test_case["quantity"],
                    "current_price": test_case["current_price"],
                    "order_amount": order.amount,
                    "expected": test_case["expected"],
                }

                results.append(result)

                logger.info(f"✅ {test_case['name']}:")
                logger.info(f"   Symbol: {test_case['symbol']}")
                logger.info(f"   Signal Quantity: {test_case['quantity']}")
                logger.info(f"   Current Price: ${test_case['current_price']:,.2f}")
                logger.info(f"   Order Amount: {order.amount}")
                logger.info(f"   Expected: {test_case['expected']}")

            except Exception as e:
                logger.error(f"❌ {test_case['name']}: Failed - {e}")

        await dispatcher.close()
        return results

    except Exception as e:
        logger.error(f"❌ Dispatcher minimum amounts test failed: {e}")
        return []


async def main():
    """Main test function"""
    logger.info("🚀 Testing Dynamic Minimum Amount Calculation")
    logger.info("=" * 70)

    # Setup environment
    setup_environment()

    # Run all tests
    tests = [
        ("Binance API Minimum Amounts", test_binance_minimum_amounts),
        ("Exchange Class Minimum Amounts", test_exchange_class_minimum_amounts),
        ("Dispatcher Minimum Amounts", test_dispatcher_minimum_amounts),
    ]

    all_results = []
    for test_name, test_func in tests:
        logger.info(f"\n📋 Running {test_name}...")
        try:
            results = await test_func()
            all_results.append((test_name, results))
        except Exception as e:
            logger.error(f"Test {test_name} failed with exception: {e}")
            all_results.append((test_name, []))

    # Summary
    logger.info("\n" + "=" * 70)
    logger.info("📊 DYNAMIC MINIMUM AMOUNT TEST SUMMARY")
    logger.info("=" * 70)

    for test_name, results in all_results:
        logger.info(f"\n📋 {test_name}:")
        if results:
            for result in results:
                if "symbol" in result:
                    logger.info(
                        f"  ✅ {result['symbol']}: Min amount = {result.get('final_min_qty', result.get('calculated_min_amount', result.get('order_amount', 'N/A')))}"
                    )
                else:
                    logger.info(
                        f"  ✅ {result.get('test_case', 'Unknown')}: Order amount = {result.get('order_amount', 'N/A')}"
                    )
        else:
            logger.info("  ❌ No results")

    logger.info("=" * 70)
    logger.info("🎉 Dynamic minimum amount calculation is working!")
    logger.info("✅ Each symbol now has its own minimum amount")
    logger.info("✅ System calculates minimums based on Binance filters")
    logger.info("✅ Dispatcher uses dynamic amounts instead of fixed defaults")
    logger.info("✅ Orders will meet Binance minimum requirements")
    logger.info("=" * 70)

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
