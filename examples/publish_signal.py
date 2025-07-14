#!/usr/bin/env python3
"""
Example script to publish trading signals to the trading engine.
This demonstrates how to send signals to the trading engine API.
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

import aiohttp

from contracts.signal import Signal, SignalType

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def publish_signal(
    signal: Signal, api_url: str = "http://localhost:8000"
) -> dict[str, Any]:
    """
    Publish a trading signal to the trading engine API.

    Args:
        signal: The trading signal to publish
        api_url: The base URL of the trading engine API

    Returns:
        Dict containing the API response
    """
    async with aiohttp.ClientSession() as session:
        url = f"{api_url}/trade"
        payload = signal.dict()

        try:
            async with session.post(url, json=payload) as response:
                result = await response.json()
                logger.info(f"Signal published successfully: {result}")
                return result
        except Exception as e:
            logger.error(f"Failed to publish signal: {e}")
            return {"status": "error", "message": str(e)}


async def publish_multiple_signals(
    signals: list[Signal], api_url: str = "http://localhost:8000"
) -> list[dict[str, Any]]:
    """
    Publish multiple trading signals to the trading engine API.

    Args:
        signals: List of trading signals to publish
        api_url: The base URL of the trading engine API

    Returns:
        List of API responses
    """
    async with aiohttp.ClientSession() as session:
        url = f"{api_url}/trade/batch"
        payload = [signal.dict() for signal in signals]

        try:
            async with session.post(url, json=payload) as response:
                results = await response.json()
                logger.info(f"Multiple signals published successfully: {results}")
                return results
        except Exception as e:
            logger.error(f"Failed to publish multiple signals: {e}")
            return [{"status": "error", "message": str(e)}]


def create_sample_signals() -> list[Signal]:
    """
    Create sample trading signals for demonstration.

    Returns:
        List of sample trading signals
    """
    signals = [
        Signal(
            id="signal-btc-buy-001",
            symbol="BTCUSDT",
            signal_type=SignalType.BUY,
            price=45000.0,
            quantity=0.1,
            timestamp=int(datetime.now().timestamp()),
            source="example_script",
            confidence=0.8,
            metadata={"strategy": "momentum", "timeframe": "1h"},
            timeframe="1h",
            strategy="momentum_strategy",
        ),
        Signal(
            id="signal-eth-sell-001",
            symbol="ETHUSDT",
            signal_type=SignalType.SELL,
            price=3000.0,
            quantity=1.0,
            timestamp=int(datetime.now().timestamp()),
            source="example_script",
            confidence=0.7,
            metadata={"strategy": "mean_reversion", "timeframe": "4h"},
            timeframe="4h",
            strategy="mean_reversion_strategy",
        ),
        Signal(
            id="signal-btc-hold-001",
            symbol="BTCUSDT",
            signal_type=SignalType.HOLD,
            price=45000.0,
            quantity=0.0,
            timestamp=int(datetime.now().timestamp()),
            source="example_script",
            confidence=0.6,
            metadata={"strategy": "trend_following", "timeframe": "1d"},
            timeframe="1d",
            strategy="trend_following_strategy",
        ),
    ]

    return signals


async def main() -> None:
    """Main function to demonstrate signal publishing."""
    logger.info("Starting signal publishing example...")

    # Create sample signals
    signals = create_sample_signals()

    # Publish single signal
    logger.info("Publishing single signal...")
    result = await publish_signal(signals[0])
    print(f"Single signal result: {json.dumps(result, indent=2)}")

    # Publish multiple signals
    logger.info("Publishing multiple signals...")
    results = await publish_multiple_signals(signals)
    print(f"Multiple signals results: {json.dumps(results, indent=2)}")

    logger.info("Signal publishing example completed.")


if __name__ == "__main__":
    asyncio.run(main())
