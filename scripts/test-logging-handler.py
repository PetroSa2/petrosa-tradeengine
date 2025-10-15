#!/usr/bin/env python3
"""Test if Python logging is being captured by OTLP handler

Run this inside a tradeengine pod to test logging:
kubectl exec -it <pod-name> -- python scripts/test-logging-handler.py
"""

import logging

# Get root logger
root_logger = logging.getLogger()

print("🔍 Logging Handler Diagnostic")
print("=" * 60)
print(f"\nRoot logger level: {logging.getLevelName(root_logger.level)}")
print(f"Root logger handlers: {len(root_logger.handlers)}")
print("\nHandlers:")
for i, handler in enumerate(root_logger.handlers):
    print(f"  {i+1}. {type(handler).__name__}: {handler}")
    if hasattr(handler, "level"):
        print(f"     Level: {logging.getLevelName(handler.level)}")
print()

# Test logging at different levels
print("📝 Sending test logs at different levels...")
logger = logging.getLogger(__name__)

logger.debug("TEST DEBUG - This is a debug message")
logger.info("TEST INFO - This is an info message")
logger.warning("TEST WARNING - This is a warning message")
logger.error("TEST ERROR - This is an error message")

print("\n✅ Test logs sent")
print("\nIf LoggingHandler (OTLP) is configured:")
print("  - These logs should be captured")
print("  - They'll be batched")
print("  - Exported within 5-10 seconds")
print()
print("Check Grafana Alloy logs for export activity:")
print("  kubectl logs -n observability -l app=grafana-alloy --since=30s")
