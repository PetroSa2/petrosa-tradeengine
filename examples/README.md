# Petrosa Trading Engine - Examples

This directory contains example scripts and utilities for working with the Petrosa Trading Engine.

## Files

### `publish_signal.py`

A test utility for publishing trading signals to NATS that the Petrosa consumer can process.

**Usage:**
```bash
# Make sure NATS server is running
# Default: nats://localhost:4222

# Run the publisher
python examples/publish_signal.py
```

**What it does:**
- Connects to the configured NATS server
- Publishes a test trading signal to the `signals.trading` subject
- Demonstrates the signal format expected by the consumer

**Prerequisites:**
- NATS server running (install with `nats-server` or use Docker)
- Petrosa consumer running (`make consumer`) to see the signal processing

## NATS Setup

To test with NATS locally:

1. **Install NATS Server:**
   ```bash
   # macOS
   brew install nats-server

   # Or use Docker
   docker run -p 4222:4222 nats:latest
   ```

2. **Start NATS Server:**
   ```bash
   nats-server
   ```

3. **Run the Consumer:**
   ```bash
   make consumer
   ```

4. **Publish Test Signals:**
   ```bash
   python examples/publish_signal.py
   ```

## Signal Format

The expected signal format is:

```json
{
  "strategy_id": "example_strategy",
  "symbol": "BTCUSDT",
  "action": "buy",
  "price": 45000.0,
  "confidence": 0.85,
  "timestamp": "2025-06-29T12:00:00",
  "meta": {
    "simulate": true,
    "indicators": {"rsi": 65},
    "rationale": "Strong momentum"
  }
}
```
