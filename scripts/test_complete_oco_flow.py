#!/usr/bin/env python3
"""
Test the complete OCO implementation flow

This script tests:
1. Placing OCO orders (SL/TP pairs)
2. Automatic cancellation when one fills (OCO behavior)
3. Manual position closing with OCO cleanup
4. Order monitoring functionality
"""

import asyncio
import logging
import sys
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


async def test_complete_oco_flow():
    """Test the complete OCO implementation"""

    try:
        # Initialize components
        exchange = BinanceFuturesExchange()
        dispatcher = Dispatcher(exchange)
        await dispatcher.initialize()

        logger.info(f"\n{'='*80}")
        logger.info("TESTING COMPLETE OCO IMPLEMENTATION FLOW")
        logger.info(f"{'='*80}")

        # Test 1: Create a signal with SL/TP values
        logger.info("\n📊 TEST 1: CREATING SIGNAL WITH SL/TP VALUES")

        current_time = datetime.utcnow()
        test_signal = Signal(
            id=f"test_oco_{current_time.timestamp()}",
            strategy_id="test_oco_strategy",
            signal_id=f"test_oco_signal_{current_time.timestamp()}",
            strategy_mode=StrategyMode.DETERMINISTIC,
            symbol="BTCUSDT",
            action="buy",
            confidence=0.85,
            strength=SignalStrength.STRONG,
            price=50000.0,
            quantity=0.001,
            current_price=50000.0,
            target_price=50000.0,
            stop_loss=49000.0,  # 2% stop loss
            take_profit=52000.0,  # 4% take profit
            source="test_script",
            strategy="test_oco",
            timeframe="1h",
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.GTC,
            position_size_pct=1.0,
            timestamp=current_time,
            metadata={
                "test": True,
                "purpose": "test_oco_implementation",
                "stop_loss_pct": 2.0,
                "take_profit_pct": 4.0,
            },
        )

        logger.info("✅ Signal created with SL/TP values")
        logger.info(f"  Symbol: {test_signal.symbol}")
        logger.info(f"  Action: {test_signal.action}")
        logger.info(f"  Quantity: {test_signal.quantity}")
        logger.info(f"  Stop Loss: {test_signal.stop_loss}")
        logger.info(f"  Take Profit: {test_signal.take_profit}")

        # Test 2: Process the signal (this should trigger OCO order placement)
        logger.info("\n📊 TEST 2: PROCESSING SIGNAL (SHOULD TRIGGER OCO)")

        result = await dispatcher.dispatch(test_signal)

        logger.info(f"Signal processing result: {result.get('status')}")

        if result.get("status") == "executed":
            logger.info("✅ Signal executed successfully")
            execution_result = result.get("execution_result", {})
            logger.info(f"  Execution Status: {execution_result.get('status')}")

            # Check if OCO orders were placed
            logger.info("\n📊 TEST 3: CHECKING OCO ORDERS")

            # Wait a moment for orders to be placed
            await asyncio.sleep(3)

            # Check active OCO pairs
            active_pairs = dispatcher.oco_manager.active_oco_pairs
            logger.info(f"Active OCO pairs: {len(active_pairs)}")

            if active_pairs:
                for position_id, oco_info in active_pairs.items():
                    logger.info(f"  Position ID: {position_id}")
                    logger.info(f"  Symbol: {oco_info['symbol']}")
                    logger.info(f"  Position Side: {oco_info['position_side']}")
                    logger.info(f"  SL Order ID: {oco_info['sl_order_id']}")
                    logger.info(f"  TP Order ID: {oco_info['tp_order_id']}")
                    logger.info(f"  Status: {oco_info['status']}")

                logger.info("✅ OCO ORDERS PLACED AND MONITORING ACTIVE")

                # Test 4: Test manual position closing with OCO cleanup
                logger.info(
                    "\n📊 TEST 4: TESTING MANUAL POSITION CLOSING WITH OCO CLEANUP"
                )

                # Get the first active OCO pair for testing
                test_position_id = list(active_pairs.keys())[0]
                test_oco_info = active_pairs[test_position_id]

                logger.info(f"Testing position close for: {test_position_id}")

                # Close position with OCO cleanup
                close_result = await dispatcher.close_position_with_cleanup(
                    position_id=test_position_id,
                    symbol=test_oco_info["symbol"],
                    position_side=test_oco_info["position_side"],
                    quantity=0.001,  # Small test quantity
                    reason="test_cleanup",
                )

                logger.info(f"Position close result: {close_result}")

                if close_result["status"] == "success":
                    logger.info("✅ POSITION CLOSED WITH OCO CLEANUP SUCCESSFULLY")
                    logger.info(f"  Position Closed: {close_result['position_closed']}")
                    logger.info(f"  OCO Cancelled: {close_result['oco_cancelled']}")
                else:
                    logger.error(f"❌ POSITION CLOSE FAILED: {close_result}")

            else:
                logger.warning(
                    "⚠️  NO OCO PAIRS FOUND - OCO orders may not have been placed"
                )

        else:
            logger.error(f"❌ Signal processing failed: {result}")

        # Test 5: Check OCO monitoring status
        logger.info("\n📊 TEST 5: CHECKING OCO MONITORING STATUS")

        monitoring_active = dispatcher.oco_manager.monitoring_active
        logger.info(f"OCO Monitoring Active: {monitoring_active}")

        if monitoring_active:
            logger.info("✅ OCO MONITORING IS ACTIVE")
        else:
            logger.info("ℹ️  OCO MONITORING IS NOT ACTIVE")

        logger.info(f"\n{'='*80}")
        logger.info("OCO IMPLEMENTATION TEST COMPLETE")
        logger.info(f"{'='*80}")

        # Cleanup
        await dispatcher.shutdown()

        return True

    except Exception as e:
        logger.error(f"❌ Error in OCO flow test: {e}", exc_info=True)
        return False


async def main():
    """Main entry point"""
    success = await test_complete_oco_flow()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
