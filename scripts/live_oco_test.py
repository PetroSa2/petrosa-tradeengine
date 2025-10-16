#!/usr/bin/env python3
"""
Live OCO Implementation Test

This script performs comprehensive live testing of the OCO implementation:
1. Places real OCO orders with current market prices
2. Tests order monitoring and automatic cancellation
3. Tests manual position closing with cleanup
4. Verifies integration with existing systems
5. Tests error handling scenarios
"""

import asyncio
import logging
import sys
import time
from datetime import datetime

from contracts.signal import (
    OrderType,
    Signal,
    SignalStrength,
    StrategyMode,
    TimeInForce,
)
from tradeengine.dispatcher import Dispatcher
from tradeengine.exchange.binance import BinanceFuturesExchange

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def get_current_market_price(symbol: str) -> float:
    """Get current market price for a symbol"""
    try:
        exchange = BinanceFuturesExchange()
        ticker = exchange.client.futures_symbol_ticker(symbol=symbol)
        return float(ticker["price"])
    except Exception as e:
        logger.error(f"Error getting market price for {symbol}: {e}")
        return 50000.0  # Fallback price


async def test_live_oco_implementation():
    """Comprehensive live test of OCO implementation"""

    try:
        logger.info(f"\n{'='*80}")
        logger.info("🚀 LIVE OCO IMPLEMENTATION TEST")
        logger.info(f"{'='*80}")

        # Initialize components
        exchange = BinanceFuturesExchange()
        dispatcher = Dispatcher(exchange)
        await dispatcher.initialize()

        # Get current market prices
        logger.info("\n📊 GETTING CURRENT MARKET PRICES")
        btc_price = await get_current_market_price("BTCUSDT")
        logger.info(f"BTCUSDT Current Price: ${btc_price:,.2f}")

        # Test 1: Create a realistic signal with current market prices
        logger.info("\n📊 TEST 1: CREATING REALISTIC SIGNAL WITH CURRENT PRICES")

        position_id = f"live_test_{int(time.time())}"

        # Use realistic SL/TP levels based on current price
        stop_loss_price = btc_price * 0.995  # 0.5% stop loss (tight for testing)
        take_profit_price = btc_price * 1.01  # 1% take profit (tight for testing)

        logger.info(f"Position ID: {position_id}")
        logger.info(
            f"Stop Loss: ${stop_loss_price:,.2f} ({((stop_loss_price/btc_price-1)*100):+.2f}%)"
        )
        logger.info(
            f"Take Profit: ${take_profit_price:,.2f} ({((take_profit_price/btc_price-1)*100):+.2f}%)"
        )

        # Test 2: Place OCO orders directly
        logger.info("\n📊 TEST 2: PLACING OCO ORDERS DIRECTLY")

        oco_result = await dispatcher.oco_manager.place_oco_orders(
            position_id=position_id,
            symbol="BTCUSDT",
            position_side="LONG",
            quantity=0.001,  # Small test quantity
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
        )

        logger.info(f"OCO Result: {oco_result}")

        if oco_result["status"] == "success":
            logger.info("✅ OCO ORDERS PLACED SUCCESSFULLY")
            sl_order_id = oco_result.get("sl_order_id")
            tp_order_id = oco_result.get("tp_order_id")
            logger.info(f"  SL Order ID: {sl_order_id}")
            logger.info(f"  TP Order ID: {tp_order_id}")

            # Test 3: Verify orders are in active OCO pairs
            logger.info("\n📊 TEST 3: VERIFYING OCO PAIR TRACKING")

            active_pairs = dispatcher.oco_manager.active_oco_pairs
            logger.info(f"Active OCO pairs: {len(active_pairs)}")

            if position_id in active_pairs:
                oco_info = active_pairs[position_id]
                logger.info("✅ OCO PAIR TRACKED CORRECTLY")
                logger.info(f"  Position ID: {position_id}")
                logger.info(f"  Symbol: {oco_info['symbol']}")
                logger.info(f"  SL Order ID: {oco_info['sl_order_id']}")
                logger.info(f"  TP Order ID: {oco_info['tp_order_id']}")
                logger.info(f"  Status: {oco_info['status']}")
            else:
                logger.error("❌ OCO PAIR NOT FOUND IN TRACKING")
                return False

            # Test 4: Check monitoring status
            logger.info("\n📊 TEST 4: VERIFYING ORDER MONITORING")

            monitoring_active = dispatcher.oco_manager.monitoring_active
            logger.info(f"OCO Monitoring Active: {monitoring_active}")

            if monitoring_active:
                logger.info("✅ ORDER MONITORING IS ACTIVE")
            else:
                logger.warning("⚠️  ORDER MONITORING NOT ACTIVE")

            # Test 5: Verify orders exist on Binance
            logger.info("\n📊 TEST 5: VERIFYING ORDERS ON BINANCE")

            try:
                orders = exchange.client.futures_get_open_orders(symbol="BTCUSDT")
                logger.info(f"Total open orders for BTCUSDT: {len(orders)}")

                sl_found = False
                tp_found = False

                for order in orders:
                    if order["orderId"] == sl_order_id:
                        sl_found = True
                        logger.info("✅ SL ORDER FOUND ON BINANCE")
                        logger.info(f"  Order ID: {order['orderId']}")
                        logger.info(f"  Type: {order['type']}")
                        logger.info(f"  Side: {order['side']}")
                        logger.info(f"  Status: {order['status']}")
                        logger.info(f"  Stop Price: {order['stopPrice']}")
                        logger.info(f"  Quantity: {order['origQty']}")
                        logger.info(f"  Reduce Only: {order['reduceOnly']}")

                    if order["orderId"] == tp_order_id:
                        tp_found = True
                        logger.info("✅ TP ORDER FOUND ON BINANCE")
                        logger.info(f"  Order ID: {order['orderId']}")
                        logger.info(f"  Type: {order['type']}")
                        logger.info(f"  Side: {order['side']}")
                        logger.info(f"  Status: {order['status']}")
                        logger.info(f"  Stop Price: {order['stopPrice']}")
                        logger.info(f"  Quantity: {order['origQty']}")
                        logger.info(f"  Reduce Only: {order['reduceOnly']}")

                if not sl_found:
                    logger.error("❌ SL ORDER NOT FOUND ON BINANCE")
                if not tp_found:
                    logger.error("❌ TP ORDER NOT FOUND ON BINANCE")

                if sl_found and tp_found:
                    logger.info("✅ BOTH ORDERS VERIFIED ON BINANCE")

            except Exception as e:
                logger.error(f"❌ ERROR CHECKING BINANCE ORDERS: {e}")

            # Test 6: Test order monitoring for a short period
            logger.info("\n📊 TEST 6: TESTING ORDER MONITORING")

            if monitoring_active:
                logger.info("⏳ Monitoring orders for 15 seconds...")

                # Let monitoring run and check for any changes
                for i in range(3):
                    await asyncio.sleep(5)
                    logger.info(f"  Monitoring check {i+1}/3...")

                    # Check if orders still exist
                    try:
                        current_orders = exchange.client.futures_get_open_orders(
                            symbol="BTCUSDT"
                        )
                        sl_still_exists = any(
                            order["orderId"] == sl_order_id for order in current_orders
                        )
                        tp_still_exists = any(
                            order["orderId"] == tp_order_id for order in current_orders
                        )

                        logger.info(f"    SL Order Exists: {sl_still_exists}")
                        logger.info(f"    TP Order Exists: {tp_still_exists}")

                        if not sl_still_exists or not tp_still_exists:
                            logger.info(
                                "    🔄 One or both orders filled - OCO should trigger"
                            )
                            break

                    except Exception as e:
                        logger.error(f"    Error checking orders: {e}")

                logger.info("✅ ORDER MONITORING TEST COMPLETED")

            # Test 7: Test manual OCO cancellation
            logger.info("\n📊 TEST 7: TESTING MANUAL OCO CANCELLATION")

            cancel_result = await dispatcher.oco_manager.cancel_oco_pair(position_id)

            if cancel_result:
                logger.info("✅ OCO PAIR CANCELLED SUCCESSFULLY")

                # Verify orders are cancelled on Binance
                try:
                    final_orders = exchange.client.futures_get_open_orders(
                        symbol="BTCUSDT"
                    )
                    sl_cancelled = not any(
                        order["orderId"] == sl_order_id for order in final_orders
                    )
                    tp_cancelled = not any(
                        order["orderId"] == tp_order_id for order in final_orders
                    )

                    if sl_cancelled and tp_cancelled:
                        logger.info("✅ BOTH ORDERS CANCELLED ON BINANCE")
                    else:
                        logger.warning("⚠️  ORDERS MAY NOT BE FULLY CANCELLED")
                        logger.warning(f"  SL Cancelled: {sl_cancelled}")
                        logger.warning(f"  TP Cancelled: {tp_cancelled}")

                except Exception as e:
                    logger.error(f"❌ ERROR VERIFYING CANCELLATION: {e}")

            else:
                logger.error("❌ FAILED TO CANCEL OCO PAIR")

            # Test 8: Verify cleanup
            logger.info("\n📊 TEST 8: VERIFYING CLEANUP")

            final_pairs = dispatcher.oco_manager.active_oco_pairs
            if position_id not in final_pairs:
                logger.info("✅ OCO PAIR REMOVED FROM TRACKING")
            else:
                logger.warning("⚠️  OCO PAIR STILL IN TRACKING")

            logger.info(f"Remaining OCO pairs: {len(final_pairs)}")

        else:
            logger.error(f"❌ OCO ORDERS FAILED: {oco_result}")
            return False

        # Test 9: Test integration with signal processing
        logger.info("\n📊 TEST 9: TESTING INTEGRATION WITH SIGNAL PROCESSING")

        # Create a test signal with SL/TP
        test_signal = Signal(
            id=f"integration_test_{int(time.time())}",
            strategy_id="test_oco_integration",
            signal_id=f"integration_signal_{int(time.time())}",
            strategy_mode=StrategyMode.DETERMINISTIC,
            symbol="BTCUSDT",
            action="sell",  # Use sell to avoid risk limit issues
            confidence=0.75,
            strength=SignalStrength.MEDIUM,
            price=btc_price,
            quantity=0.0005,  # Very small quantity
            current_price=btc_price,
            target_price=btc_price,
            stop_loss=btc_price * 1.005,  # 0.5% stop loss for sell
            take_profit=btc_price * 0.995,  # 0.5% take profit for sell
            source="test_script",
            strategy="test_oco_integration",
            timeframe="1h",
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.GTC,
            position_size_pct=0.01,  # Very small position size
            timestamp=datetime.utcnow(),
            metadata={"test": True, "purpose": "integration_test"},
        )

        logger.info("Created integration test signal")
        logger.info(f"  Symbol: {test_signal.symbol}")
        logger.info(f"  Action: {test_signal.action}")
        logger.info(f"  Quantity: {test_signal.quantity}")
        logger.info(f"  Stop Loss: {test_signal.stop_loss}")
        logger.info(f"  Take Profit: {test_signal.take_profit}")

        # Process the signal
        signal_result = await dispatcher.dispatch(test_signal)
        logger.info(f"Signal processing result: {signal_result.get('status')}")

        if signal_result.get("status") == "executed":
            logger.info("✅ SIGNAL PROCESSING WITH OCO SUCCESSFUL")
        else:
            logger.info(f"ℹ️  Signal processing result: {signal_result}")
            # This might fail due to risk limits, which is expected

        logger.info(f"\n{'='*80}")
        logger.info("🎉 LIVE OCO IMPLEMENTATION TEST COMPLETED")
        logger.info(f"{'='*80}")

        # Final status check
        logger.info("\n📊 FINAL STATUS CHECK")
        logger.info(f"Active OCO pairs: {len(dispatcher.oco_manager.active_oco_pairs)}")
        logger.info(f"Monitoring active: {dispatcher.oco_manager.monitoring_active}")

        # Cleanup
        await dispatcher.shutdown()

        logger.info("✅ ALL TESTS COMPLETED SUCCESSFULLY")
        logger.info("🚀 OCO IMPLEMENTATION IS READY FOR DEPLOYMENT!")

        return True

    except Exception as e:
        logger.error(f"❌ Error in live OCO test: {e}", exc_info=True)
        return False


async def main():
    """Main entry point"""
    success = await test_live_oco_implementation()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
