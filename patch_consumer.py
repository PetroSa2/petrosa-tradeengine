#!/usr/bin/env python3
"""
Patch script to apply signal mapping fix to running container
"""

import sys


def patch_consumer():
    """Apply the signal mapping fix to consumer.py"""

    # Read the current consumer.py file
    with open("tradeengine/consumer.py") as f:
        content = f.read()

    # Find the line with signal = Signal(**signal_data)
    old_line = "                    signal = Signal(**signal_data)"
    new_lines = """                    # Map incoming signal fields to Signal model fields
                    mapped_signal_data = self._map_signal_fields(signal_data)

                    signal = Signal(**mapped_signal_data)"""

    if old_line in content:
        content = content.replace(old_line, new_lines)
        print("✅ Found and replaced signal creation line")
    else:
        print("❌ Could not find signal creation line to replace")
        return False

    # Add the mapping function at the end of the class
    mapping_function = '''
    def _map_signal_fields(self, signal_data: dict) -> dict:
        """Map incoming signal fields to Signal model fields"""
        # Convert confidence from string to float
        confidence_value = signal_data.get("confidence")
        if isinstance(confidence_value, str):
            confidence_map = {
                "LOW": 0.3,
                "MEDIUM": 0.6,
                "HIGH": 0.8,
                "VERY_HIGH": 0.95
            }
            confidence_value = confidence_map.get(confidence_value.upper(), 0.5)
        elif confidence_value is None:
            # Use confidence_score if confidence is not available
            confidence_value = signal_data.get("confidence_score", 0.5)

        # Convert signal_type to action (lowercase)
        signal_type = signal_data.get("signal_type", "HOLD")
        action_map = {
            "BUY": "buy",
            "SELL": "sell",
            "HOLD": "hold",
            "CLOSE": "close"
        }
        action = action_map.get(signal_type.upper(), "hold")

        # Map fields to Signal model structure
        mapped_data = {
            "strategy_id": signal_data.get("strategy_name", "unknown_strategy"),
            "symbol": signal_data.get("symbol", ""),
            "action": action,
            "confidence": float(confidence_value),
            "price": float(signal_data.get("price", 0.0)),
            "quantity": 0.001,  # Default quantity - should be calculated based on position sizing
            "current_price": float(signal_data.get("price", 0.0)),
            "source": "nats",
            "strategy": signal_data.get("strategy_name", "unknown_strategy"),
            "timeframe": signal_data.get("metadata", {}).get("timeframe", "1h"),
            "metadata": signal_data.get("metadata", {})
        }

        # Add signal_action to metadata if available
        if "signal_action" in signal_data:
            mapped_data["metadata"]["signal_action"] = signal_data["signal_action"]

        return mapped_data
'''

    # Find the end of the class and add the mapping function
    if "class SignalConsumer:" in content:
        # Find the last method in the class
        lines = content.split("\n")
        class_start = -1
        for i, line in enumerate(lines):
            if line.strip().startswith("class SignalConsumer:"):
                class_start = i
                break

        if class_start != -1:
            # Find the end of the class (look for the next class or end of file)
            class_end = len(lines)
            for i in range(class_start + 1, len(lines)):
                if lines[i].strip().startswith("class ") and not lines[
                    i
                ].strip().startswith("class SignalConsumer"):
                    class_end = i
                    break

            # Insert the mapping function before the end of the class
            lines.insert(class_end, mapping_function)
            content = "\n".join(lines)
            print("✅ Added mapping function to SignalConsumer class")
        else:
            print("❌ Could not find SignalConsumer class")
            return False
    else:
        print("❌ Could not find SignalConsumer class")
        return False

    # Write the patched content
    with open("tradeengine/consumer_patched.py", "w") as f:
        f.write(content)

    print("✅ Created patched consumer.py file")
    return True


if __name__ == "__main__":
    success = patch_consumer()
    if success:
        print("🎉 Patch created successfully!")
        sys.exit(0)
    else:
        print("💥 Patch failed!")
        sys.exit(1)
