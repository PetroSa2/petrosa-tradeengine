#!/usr/bin/env python3
"""
Test that handler persists after LoggingInstrumentor with set_logging_format=False
"""

import logging

from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource

print("=" * 80)
print("Handler Persistence Test")
print("=" * 80)
print()

# Create logger provider and handler (simulating otel_init.py)
resource = Resource.create({"service.name": "test"})
log_exporter = OTLPLogExporter(endpoint="localhost:4317")
logger_provider = LoggerProvider(resource=resource)
logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))

# Create and attach handler
handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
root_logger = logging.getLogger()
root_logger.addHandler(handler)

print("Step 1: Handler attached")
print(f"  Handlers: {len(root_logger.handlers)}")
print(
    f"  Handler type: {type(root_logger.handlers[0]).__name__ if root_logger.handlers else 'None'}"
)
print()

# Now run LoggingInstrumentor with set_logging_format=False (THE FIX)
print("Step 2: Running LoggingInstrumentor with set_logging_format=False")
LoggingInstrumentor().instrument(set_logging_format=False)
print()

# Check if handler survived
print("Step 3: After LoggingInstrumentor")
print(f"  Handlers: {len(root_logger.handlers)}")
if len(root_logger.handlers) > 0:
    print(f"  Handler type: {type(root_logger.handlers[0]).__name__}")
    print()
    print("✅ SUCCESS: Handler persisted!")
    print("   LoggingInstrumentor did NOT clear the handler")
else:
    print()
    print("❌ FAILURE: Handler was cleared!")
    print("   LoggingInstrumentor removed the handler")

print()
print("=" * 80)

# For comparison, test with set_logging_format=True
print("COMPARISON: Testing with set_logging_format=True (OLD WAY)")
print("=" * 80)
print()

# Reset
root_logger.handlers.clear()

# Re-attach handler
root_logger.addHandler(handler)
print("Step 1: Handler attached")
print(f"  Handlers: {len(root_logger.handlers)}")
print()

# Run with set_logging_format=True (THE PROBLEM)
print("Step 2: Running LoggingInstrumentor with set_logging_format=True")
try:
    LoggingInstrumentor().uninstrument()  # Reset first
    LoggingInstrumentor().instrument(set_logging_format=True)
except Exception:
    pass  # Ignore errors
print()

# Check
print("Step 3: After LoggingInstrumentor")
print(f"  Handlers: {len(root_logger.handlers)}")
if len(root_logger.handlers) > 0:
    print(f"  Handler type: {type(root_logger.handlers[0]).__name__}")
    print()
    print("✅ Handler persisted (unexpected)")
else:
    print()
    print("❌ Handler was cleared (expected with set_logging_format=True)")

print()
print("=" * 80)
print("CONCLUSION")
print("=" * 80)
print()
print("The fix (set_logging_format=False) should preserve the handler!")
print()
