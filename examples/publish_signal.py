"""
Signal Publisher Example - Demonstrates signal publishing to NATS
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_sample_signals() -> list[dict[str, Any]]:
    """Create sample trading signals"""
    signals = [
        {
            "strategy_id": "momentum-1",
            "symbol": "BTCUSDT",
            "signal_type": "buy",
            "action": "buy",
            "confidence": 0.85,
            "strength": "high",
            "timeframe": "1h",
            "price": 45000.0,
            "quantity": 0.1,
            "current_price": 45000.0,
            "source": "momentum-strategy",
            "strategy": "momentum",
            "timestamp": datetime.now().isoformat(),
        },
        {
            "strategy_id": "mean-reversion-1",
            "symbol": "ETHUSDT",
            "signal_type": "sell",
            "action": "sell",
            "confidence": 0.75,
            "strength": "medium",
            "timeframe": "4h",
            "price": 2800.0,
            "quantity": 2.0,
            "current_price": 2800.0,
            "source": "mean-reversion-strategy",
            "strategy": "mean-reversion",
            "timestamp": datetime.now().isoformat(),
        },
        {
            "strategy_id": "arbitrage-1",
            "symbol": "ADAUSDT",
            "signal_type": "buy",
            "action": "buy",
            "confidence": 0.90,
            "strength": "high",
            "timeframe": "5m",
            "price": 0.45,
            "quantity": 1000.0,
            "current_price": 0.45,
            "source": "arbitrage-strategy",
            "strategy": "arbitrage",
            "timestamp": datetime.now().isoformat(),
        },
    ]
    return signals


def create_advanced_signals() -> list[dict[str, Any]]:
    """Create advanced trading signals with metadata"""
    signals = [
        {
            "strategy_id": "risk-management-1",
            "symbol": "BTCUSDT",
            "signal_type": "sell",
            "action": "sell",
            "confidence": 0.95,
            "strength": "high",
            "timeframe": "1h",
            "price": 44000.0,
            "quantity": 0.2,
            "current_price": 44000.0,
            "source": "risk-management",
            "strategy": "risk-management",
            "timestamp": datetime.now().isoformat(),
            "metadata": {
                "risk_level": "high",
                "stop_loss": 43000.0,
                "take_profit": 46000.0,
                "position_size": "large",
            },
        },
        {
            "strategy_id": "portfolio-rebalancing-1",
            "symbol": "ETHUSDT",
            "signal_type": "buy",
            "action": "buy",
            "confidence": 0.80,
            "strength": "medium",
            "timeframe": "1d",
            "price": 2800.0,
            "quantity": 1.5,
            "current_price": 2800.0,
            "source": "portfolio-rebalancing",
            "strategy": "rebalancing",
            "timestamp": datetime.now().isoformat(),
            "metadata": {
                "target_allocation": 0.3,
                "current_allocation": 0.25,
                "rebalancing_threshold": 0.05,
            },
        },
    ]
    return signals


async def publish_signals_to_nats(signals: list[dict[str, Any]]) -> None:
    """Publish signals to NATS (simulated)"""
    logger.info(f"Publishing {len(signals)} signals to NATS...")

    for i, signal in enumerate(signals, 1):
        # Simulate NATS publishing
        signal_json = json.dumps(signal, indent=2)
        logger.info(f"Signal {i}: {signal['symbol']} {signal['action']}")
        logger.debug(f"Signal data: {signal_json}")

        # Simulate network delay
        await asyncio.sleep(0.1)

    logger.info("All signals published successfully!")


async def main() -> None:
    """Main function to publish trading signals"""
    logger.info("Starting signal publisher example...")

    try:
        # Create sample signals
        basic_signals = create_sample_signals()
        advanced_signals = create_advanced_signals()

        # Publish basic signals
        logger.info("Publishing basic signals...")
        await publish_signals_to_nats(basic_signals)

        # Publish advanced signals
        logger.info("Publishing advanced signals...")
        await publish_signals_to_nats(advanced_signals)

        logger.info("Signal publisher example completed successfully!")

    except Exception as e:
        logger.error(f"Error in signal publisher example: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
