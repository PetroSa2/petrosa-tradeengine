#!/usr/bin/env python3
"""
Detailed test to see if OTLPLogExporter is actually exporting logs
"""

import logging
import time
from datetime import datetime

from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource

print("=" * 80)
print("Detailed OTLP Log Export Test")
print("=" * 80)
print()

# Create exporter with endpoint
endpoint = "http://grafana-alloy.observability.svc.cluster.local:4317"
print(f"Endpoint: {endpoint}")

try:
    exporter = OTLPLogExporter(endpoint=endpoint)
    print("✅ Created OTLPLogExporter")
except Exception as e:
    print(f"❌ Failed to create exporter: {e}")
    exit(1)

# Create logger provider
resource = Resource.create(
    {"service.name": "detailed-test", "test.timestamp": datetime.utcnow().isoformat()}
)

logger_provider = LoggerProvider(resource=resource)

# Create batch processor with small batch size for quick export
batch_processor = BatchLogRecordProcessor(
    exporter,
    max_queue_size=2048,
    schedule_delay_millis=1000,  # Export every 1 second
    export_timeout_millis=30000,
    max_export_batch_size=512,
)

logger_provider.add_log_record_processor(batch_processor)
print("✅ Created LoggerProvider with BatchLogRecordProcessor")
print("   Schedule delay: 1 second (fast export)")

# Create handler
handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
print("✅ Created LoggingHandler")

# Attach to logger
test_logger = logging.getLogger("detailed_test")
test_logger.setLevel(logging.INFO)
test_logger.addHandler(handler)
print("✅ Attached handler to logger")
print()

# Send logs
print("📝 Sending 5 test logs...")
for i in range(5):
    test_logger.info(f"DETAILED TEST LOG {i+1} - timestamp: {time.time()}")
    print(f"  Sent log {i+1}")
    time.sleep(1)

print()
print("⏳ Waiting 5 seconds for batch export...")
time.sleep(5)

print("🔄 Forcing flush...")
try:
    logger_provider.force_flush(timeout_millis=10000)
    print("✅ Flush completed")
except Exception as e:
    print(f"⚠️  Flush error: {e}")

print()
print("⏳ Waiting 5 more seconds...")
time.sleep(5)

print()
print("=" * 80)
print("Test Complete")
print("=" * 80)
print()
print("If no errors above, logs were exported via OTLP")
print("Check:")
print("  1. Grafana Alloy logs for OTLP activity")
print("  2. Grafana Cloud Loki for these test logs")
print()
