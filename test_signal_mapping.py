#!/usr/bin/env python3
"""
Test script to verify signal mapping functionality
"""

import os
import sys

from contracts.signal import Signal
from tradeengine.consumer import SignalConsumer

sys.path.append(os.path.dirname(os.path.abspath(__file__)))


def test_signal_mapping():
    """Test the signal mapping functionality"""

    # Create a consumer instance (we don't need to initialize it fully for this test)
    consumer = SignalConsumer.__new__(SignalConsumer)

    # Sample incoming signal data (from the logs)
    incoming_signal = {
        "symbol": "BTCUSDT",
        "signal_type": "BUY",
        "signal_action": "OPEN_LONG",
        "confidence": "HIGH",
        "confidence_score": 0.75,
        "price": 50000.0,
        "timestamp": 1234567890,
        "strategy_name": "test_strategy",
        "metadata": {"timeframe": "1h", "some_other_field": "value"},
    }

    print("🔍 Testing Signal Mapping")
    print("=" * 50)
    print("📥 Incoming signal data:")
    for key, value in incoming_signal.items():
        print(f"   {key}: {value}")

    print("\n🔄 Mapping signal fields...")

    # Test the mapping function
    try:
        assert consumer is not None  # Consumer should be created
        assert incoming_signal is not None  # Signal data should be provided
        mapped_data = consumer._map_signal_fields(incoming_signal)
        assert mapped_data is not None  # Mapping should return data
        print("✅ Mapping successful!")
        print("📤 Mapped signal data:")
        for key, value in mapped_data.items():
            print(f"   {key}: {value}")

        print("\n🧪 Testing Signal model creation...")

        # Test creating a Signal object with the mapped data
        signal = Signal(**mapped_data)
        assert signal is not None  # Signal should be created
        print("✅ Signal model creation successful!")
        print("📊 Created Signal object:")
        print(f"   strategy_id: {signal.strategy_id}")
        print(f"   symbol: {signal.symbol}")
        print(f"   action: {signal.action}")
        print(f"   confidence: {signal.confidence}")
        print(f"   price: {signal.price}")
        print(f"   quantity: {signal.quantity}")
        print(f"   current_price: {signal.current_price}")
        print(f"   source: {signal.source}")
        print(f"   strategy: {signal.strategy}")
        print(f"   timeframe: {signal.timeframe}")
        print(f"   metadata: {signal.metadata}")

        return True

    except Exception as e:
        print(f"❌ Error during mapping: {e}")
        import traceback

        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = test_signal_mapping()
    if success:
        print("\n🎉 All tests passed!")
        sys.exit(0)
    else:
        print("\n💥 Tests failed!")
        sys.exit(1)
