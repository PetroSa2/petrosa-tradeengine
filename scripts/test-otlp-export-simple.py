#!/usr/bin/env python3
"""
Simple test to verify OTLP log export configuration
"""

import logging
import os
import time
from datetime import datetime

from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource

print("=" * 80)
print("Simple OTLP Log Export Test")
print("=" * 80)

# Configuration
endpoint = os.getenv(
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "http://grafana-alloy.observability.svc.cluster.local:4317",
)
service_name = os.getenv("OTEL_SERVICE_NAME", "simple-test")
service_version = os.getenv("OTEL_SERVICE_VERSION", "1.0.0")

print(f"Endpoint: {endpoint}")
print(f"Service: {service_name}")
print(f"Version: {service_version}")
print()

try:
    # Create exporter
    print("1. Creating OTLP log exporter...")
    exporter = OTLPLogExporter(endpoint=endpoint)
    print("✅ Exporter created")

    # Create logger provider
    print("2. Creating logger provider...")
    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": service_version,
            "test.timestamp": datetime.utcnow().isoformat(),
        }
    )

    logger_provider = LoggerProvider(resource=resource)
    print("✅ Logger provider created")

    # Create batch processor
    print("3. Creating batch processor...")
    processor = BatchLogRecordProcessor(exporter)
    logger_provider.add_log_record_processor(processor)
    print("✅ Batch processor added")

    # Create handler
    print("4. Creating logging handler...")
    handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
    print("✅ Handler created")

    # Attach to logger
    print("5. Attaching handler...")
    test_logger = logging.getLogger("simple_test")
    test_logger.setLevel(logging.INFO)
    test_logger.addHandler(handler)
    print("✅ Handler attached")
    print(f"   Total handlers: {len(test_logger.handlers)}")

    # Send test logs
    print("\n6. Sending test logs...")
    for i in range(3):
        test_logger.info(f"SIMPLE TEST LOG {i+1} - {datetime.utcnow().isoformat()}")
        print(f"   Sent log {i+1}")
        time.sleep(1)

    # Wait and flush
    print("\n7. Waiting for export...")
    time.sleep(5)

    print("8. Forcing flush...")
    logger_provider.force_flush(timeout_millis=10000)
    print("✅ Flush completed")

    print("\n" + "=" * 80)
    print("Test completed successfully!")
    print("Check Grafana Cloud for 'SIMPLE TEST LOG' messages")
    print("=" * 80)

except Exception as e:
    print(f"\n❌ Test failed: {e}")
    import traceback

    traceback.print_exc()
