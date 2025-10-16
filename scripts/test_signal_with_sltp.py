#!/usr/bin/env python3
"""
Test sending a signal with SL/TP values to trigger the risk management placement

This script sends a test signal to the trading engine to verify that SL/TP orders
are now being placed after the fix.
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime

import nats

from contracts.signal import (
    OrderType,
    Signal,
    SignalStrength,
    StrategyMode,
    TimeInForce,
)

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def send_test_signal():
    """Send a test signal with SL/TP values"""

    # Get NATS URL
    nats_url = os.getenv("NATS_URL", "nats://localhost:4222")

    try:
        logger.info(f"Connecting to NATS at {nats_url}")
        nc = await nats.connect(nats_url)

        # Create a test signal with SL/TP values
        current_time = datetime.utcnow()

        signal = Signal(
            id=f"test_sl_tp_{current_time.timestamp()}",
            strategy_id="test_sl_tp_strategy",
            signal_id=f"test_signal_{current_time.timestamp()}",
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
            strategy="test_sl_tp",
            timeframe="1h",
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.GTC,
            position_size_pct=1.0,
            timestamp=current_time,
            metadata={
                "test": True,
                "purpose": "verify_sl_tp_placement",
                "stop_loss_pct": 2.0,
                "take_profit_pct": 4.0,
            },
        )

        # Convert to dict for NATS
        signal_data = signal.model_dump()

        logger.info(f"\n{'='*80}")
        logger.info("SENDING TEST SIGNAL WITH SL/TP")
        logger.info(f"{'='*80}")
        logger.info(f"Symbol: {signal.symbol}")
        logger.info(f"Action: {signal.action}")
        logger.info(f"Quantity: {signal.quantity}")
        logger.info(f"Stop Loss: {signal.stop_loss}")
        logger.info(f"Take Profit: {signal.take_profit}")
        logger.info(f"Confidence: {signal.confidence}")

        # Send to NATS
        subject = "signals.trading"
        message = json.dumps(signal_data, default=str)

        logger.info(f"\nPublishing to subject: {subject}")
        await nc.publish(subject, message.encode())
        logger.info("✅ Signal sent successfully!")

        # Wait a moment for processing
        await asyncio.sleep(3)

        # Close connection
        await nc.close()

        logger.info(f"\n{'='*80}")
        logger.info("SIGNAL SENT - CHECK TRADING ENGINE LOGS")
        logger.info(f"{'='*80}")
        logger.info("Look for these log messages:")
        logger.info("  📉 PLACING STOP LOSS: ...")
        logger.info("  ✅ STOP LOSS PLACED: ...")
        logger.info("  📈 PLACING TAKE PROFIT: ...")
        logger.info("  ✅ TAKE PROFIT PLACED: ...")

        return True

    except Exception as e:
        logger.error(f"❌ Error sending signal: {e}", exc_info=True)
        return False


async def main():
    """Main entry point"""
    success = await send_test_signal()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
