#!/usr/bin/env python3
"""
Direct test of OTLP log export to Grafana Alloy.
This script sends logs via OTLP to verify the pipeline works.

Run inside cluster:
kubectl run -it --rm otlp-log-test --image=python:3.11-slim --namespace=petrosa-apps -- bash
pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp-proto-grpc
python test-otlp-logs-direct.py
"""

import logging
import os
import time
from datetime import datetime

from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource

# Configuration
OTLP_ENDPOINT = os.getenv(
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "grafana-alloy.observability.svc.cluster.local:4317",
)

# Remove http:// prefix if present
if OTLP_ENDPOINT.startswith("http://"):
    OTLP_ENDPOINT = OTLP_ENDPOINT[7:]

print("🔍 Testing OTLP Log Export")
print(f"Endpoint: {OTLP_ENDPOINT}")
print(f"Time: {datetime.utcnow().isoformat()}")
print("")

# Create resource
resource = Resource.create(
    {
        "service.name": "otlp-log-test",
        "service.version": "1.0.0",
        "test.type": "direct-otlp-logs",
    }
)

# Create OTLP log exporter
try:
    log_exporter = OTLPLogExporter(endpoint=OTLP_ENDPOINT, insecure=True)
    print("✅ Created OTLPLogExporter")
except Exception as e:
    print(f"❌ Failed to create log exporter: {e}")
    exit(1)

# Create logger provider
try:
    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
    print("✅ Created LoggerProvider with BatchLogRecordProcessor")
except Exception as e:
    print(f"❌ Failed to create logger provider: {e}")
    exit(1)

# Attach OTLP handler to root logger
try:
    handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)
    print("✅ Attached LoggingHandler to root logger")
except Exception as e:
    print(f"❌ Failed to attach handler: {e}")
    exit(1)

print("")
print("📝 Generating test logs...")
print("")

# Generate test logs
logger = logging.getLogger(__name__)

for i in range(10):
    logger.info(
        f"Test log message {i+1} - Testing OTLP log export at {datetime.utcnow().isoformat()}"
    )
    logger.warning(f"Test warning message {i+1}")
    if i == 5:
        logger.error("Test error message - This is a test error")
    time.sleep(1)

print("")
print("⏳ Waiting for batch export (10 seconds)...")
time.sleep(10)

# Force flush
try:
    logger_provider.force_flush()
    print("✅ Forced flush of log batch")
except Exception as e:
    print(f"⚠️  Flush error: {e}")

print("")
print("✅ Test complete!")
print("")
print("Next steps:")
print("1. Check Grafana Alloy logs:")
print("   kubectl logs -n observability -l app=grafana-alloy --tail=100")
print("2. Check Grafana Cloud Loki:")
print('   Query: {service_name="otlp-log-test"}')
print("3. Look for 10 info messages, 10 warnings, 1 error")
