#!/usr/bin/env python3
"""
NATS Signal Publisher - Test utility for sending trading signals to NATS

This script demonstrates how to publish trading signals to the NATS stream
that the Petrosa Trading Engine consumes.

Usage:
    python examples/publish_signal.py
"""

import asyncio
import json
import os
import sys
from datetime import datetime

import nats

from shared.config import settings

# Add project root to path so we can import shared modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def publish_test_signal():
    """Publish a test trading signal to NATS"""

    # Connect to NATS
    nc = await nats.connect(settings.nats_servers)
    print(f"Connected to NATS at {settings.nats_servers}")

    # Create test signal
    test_signal = {
        "strategy_id": "nats_test_strategy",
        "symbol": "BTCUSDT",
        "action": "buy",
        "price": 45000.0,
        "confidence": 0.85,
        "timestamp": datetime.now().isoformat(),
        "meta": {
            "simulate": True,
            "indicators": {"rsi": 65, "macd": "bullish"},
            "rationale": "NATS integration test signal",
            "source": "example_publisher",
        },
    }

    # Publish signal
    await nc.publish(settings.nats_signal_subject, json.dumps(test_signal).encode())

    print(f"Published test signal to subject: {settings.nats_signal_subject}")
    print(f"Signal: {json.dumps(test_signal, indent=2)}")

    # Close connection
    await nc.close()
    print("NATS connection closed")


if __name__ == "__main__":
    print("NATS Signal Publisher for Petrosa Trading Engine")
    print("=" * 50)

    # Publish test signal
    print("\n1. Publishing test signal...")
    asyncio.run(publish_test_signal())

    print("\nDone! Check your consumer logs to see if signals were processed.")
