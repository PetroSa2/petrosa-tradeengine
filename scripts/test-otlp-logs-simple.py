#!/usr/bin/env python3
"""
Simple OTLP Logs Test - Mimics Production Config

Tests the EXACT configuration used in production for tradeengine.
If this works, we know the config is correct.
"""

import logging
import time
from datetime import datetime

from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource

print("=" * 80)
print("OTLP Logs Simple Test - Production Configuration")
print("=" * 80)
print()

# Use the EXACT production configuration
OTLP_ENDPOINT = "http://grafana-alloy.observability.svc.cluster.local:4317"
print(f"Testing with production endpoint: {OTLP_ENDPOINT}")
print("(This will fail locally but shows the exact config)")
print()

# Create resource (same as production)
resource = Resource.create(
    {
        "service.name": "local-test",
        "service.version": "1.0.0",
    }
)

# Test 1: Can we create the exporters?
print("Test 1: Creating exporters...")
try:
    trace_exporter = OTLPSpanExporter(endpoint=OTLP_ENDPOINT)
    print("✅ Created OTLPSpanExporter (traces)")
except Exception as e:
    print(f"❌ Failed to create OTLPSpanExporter: {e}")

try:
    metric_exporter = OTLPMetricExporter(endpoint=OTLP_ENDPOINT)
    print("✅ Created OTLPMetricExporter (metrics)")
except Exception as e:
    print(f"❌ Failed to create OTLPMetricExporter: {e}")

try:
    log_exporter = OTLPLogExporter(endpoint=OTLP_ENDPOINT)
    print("✅ Created OTLPLogExporter (logs)")
except Exception as e:
    print(f"❌ Failed to create OTLPLogExporter: {e}")

print()

# Test 2: Can we create the logger provider and handler?
print("Test 2: Creating logger provider and handler...")
try:
    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
    print("✅ Created LoggerProvider with BatchLogRecordProcessor")

    handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
    print("✅ Created LoggingHandler")

    # Attach to logger
    test_logger = logging.getLogger("test")
    test_logger.setLevel(logging.INFO)
    test_logger.addHandler(handler)
    print("✅ Attached handler to logger")

except Exception as e:
    print(f"❌ Failed: {e}")
    import traceback

    traceback.print_exc()

print()

# Test 3: Send logs
print("Test 3: Sending test logs...")
try:
    timestamp = datetime.utcnow().isoformat()
    test_logger.info(f"TEST LOG at {timestamp}")
    print("✅ Sent test log")

    print("⏳ Waiting for batch export (will fail - no Grafana Alloy locally)...")
    time.sleep(5)

    logger_provider.force_flush()
    print("✅ Forced flush (errors expected)")

except Exception as e:
    print(f"⚠️  Expected error (no Grafana Alloy locally): {e}")

print()
print("=" * 80)
print("FINDINGS")
print("=" * 80)
print()
print("If all exporters were created successfully:")
print("  → Configuration is correct")
print("  → The issue is NOT with exporter creation")
print("  → The issue might be:")
print("    1. Handler gets removed after creation")
print("    2. Logs export differently than metrics/traces")
print("    3. Something specific to LoggingHandler")
print()
print("Next: Check if handler persists and if logs are actually sent")
print()
