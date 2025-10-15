#!/usr/bin/env python3
"""
Local OTLP Logs Test Script

Tests different OTLP log configurations directly against Grafana Cloud
to determine which configuration actually works.

This avoids breaking production by testing locally first.
"""

import logging
import time
from datetime import datetime

from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http._log_exporter import (
    OTLPLogExporter as OTLPLogExporterHTTP,
)
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource


def test_configuration(name, endpoint, headers=None, use_http=False):
    """Test a specific OTLP log configuration"""
    print(f"\n{'='*80}")
    print(f"TEST: {name}")
    print(f"{'='*80}")
    print(f"Endpoint: {endpoint}")
    print(f"Protocol: {'HTTP' if use_http else 'gRPC'}")
    print(f"Headers: {headers}")
    print()

    try:
        # Create resource
        resource = Resource.create(
            {
                "service.name": "local-otlp-test",
                "test.name": name,
                "test.timestamp": datetime.utcnow().isoformat(),
            }
        )

        # Create appropriate exporter
        if use_http:
            log_exporter = OTLPLogExporterHTTP(endpoint=endpoint, headers=headers)
        else:
            log_exporter = OTLPLogExporter(endpoint=endpoint, headers=headers)

        print(f"✅ Created {log_exporter.__class__.__name__}")

        # Create logger provider
        logger_provider = LoggerProvider(resource=resource)
        logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
        print("✅ Created LoggerProvider with BatchLogRecordProcessor")

        # Create handler
        handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)

        # Create test logger
        test_logger = logging.getLogger(
            f"test_{name.replace(' ', '_').replace('/', '_')}"
        )
        test_logger.setLevel(logging.INFO)
        test_logger.addHandler(handler)
        print("✅ Created and attached LoggingHandler")

        # Send test logs
        print("\n📝 Sending test logs...")
        timestamp = datetime.utcnow().isoformat()
        test_logger.info(f"[{name}] TEST LOG 1 at {timestamp}")
        test_logger.warning(f"[{name}] TEST LOG 2 (WARNING) at {timestamp}")
        test_logger.error(f"[{name}] TEST LOG 3 (ERROR) at {timestamp}")
        print("✅ Sent 3 test logs")

        # Wait for batch export
        print("\n⏳ Waiting 10 seconds for batch export...")
        time.sleep(10)

        # Force flush
        print("🔄 Forcing flush...")
        logger_provider.force_flush()
        time.sleep(2)

        print(f"\n✅ Test '{name}' completed")
        print("   Check Grafana Cloud Loki:")
        print(f'   Query: {{service_name="local-otlp-test", test_name="{name}"}}')
        print()

        # Clean up
        test_logger.removeHandler(handler)
        return True

    except Exception as e:
        print(f"\n❌ Test '{name}' failed with error:")
        print(f"   {type(e).__name__}: {e}")
        import traceback

        traceback.print_exc()
        return False


def main():
    """Run all test configurations"""
    print("=" * 80)
    print("OTLP Logs Local Test Suite")
    print("=" * 80)
    print()
    print("Testing different OTLP configurations to find what works")
    print("Results will appear in Grafana Cloud Loki")
    print()
    print("Grafana Cloud URL: https://yurisa2.grafana.net")
    print('Check: Explore → Loki → {service_name="local-otlp-test"}')
    print()

    # Grafana Cloud OTLP endpoint
    otlp_gateway = "otlp-gateway-prod-sa-east-1.grafana.net/otlp"

    # Basic Auth token (user:password in base64)
    basic_auth = "MTQwMjg5NTpnbGNfZXlKdklqb2lNVFUxT0RFeU1TSXNJbTRpT2lKemRHRmpheTB4TkRBeU9EazFMVzkwYkhBdGQzSnBkR1V0YjNSc2NDMTBiMnRsYmlJc0ltc2lPaUkzYzJVMFFWUXdOVmhETTB0dVJqUk5RalZCWW1JMk1UUWlMQ0p0SWpwN0luSWlPaUp3Y205a0xYTmhMV1ZoYzNRdE1TSjlmUT09"

    results = {}

    # Test 1: gRPC without http:// prefix (standard gRPC)
    results["gRPC - No Prefix"] = test_configuration(
        name="gRPC - No Prefix",
        endpoint=otlp_gateway,
        headers={"Authorization": f"Basic {basic_auth}"},
        use_http=False,
    )
    time.sleep(5)

    # Test 2: gRPC with http:// prefix
    results["gRPC - HTTP Prefix"] = test_configuration(
        name="gRPC - HTTP Prefix",
        endpoint=f"http://{otlp_gateway}",
        headers={"Authorization": f"Basic {basic_auth}"},
        use_http=False,
    )
    time.sleep(5)

    # Test 3: gRPC with https:// prefix
    results["gRPC - HTTPS Prefix"] = test_configuration(
        name="gRPC - HTTPS Prefix",
        endpoint=f"https://{otlp_gateway}",
        headers={"Authorization": f"Basic {basic_auth}"},
        use_http=False,
    )
    time.sleep(5)

    # Test 4: HTTP without prefix
    results["HTTP - No Prefix"] = test_configuration(
        name="HTTP - No Prefix",
        endpoint=otlp_gateway,
        headers={"Authorization": f"Basic {basic_auth}"},
        use_http=True,
    )
    time.sleep(5)

    # Test 5: HTTP with https:// prefix (standard for HTTP exporter)
    results["HTTP - HTTPS Prefix"] = test_configuration(
        name="HTTP - HTTPS Prefix",
        endpoint=f"https://{otlp_gateway}",
        headers={"Authorization": f"Basic {basic_auth}"},
        use_http=True,
    )
    time.sleep(5)

    # Print summary
    print("\n" + "=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)
    print()

    for test_name, success in results.items():
        status = "✅ SUCCESS" if success else "❌ FAILED"
        print(f"{status:12} - {test_name}")

    print()
    print("=" * 80)
    print("NEXT STEPS")
    print("=" * 80)
    print()
    print("1. Go to: https://yurisa2.grafana.net")
    print("2. Navigate to: Explore → Loki")
    print('3. Query: {service_name="local-otlp-test"}')
    print("4. Check which test configurations show logs")
    print("5. The one with logs is the correct configuration!")
    print()
    print("Note: Logs may take 1-2 minutes to appear due to batching/processing")
    print()


if __name__ == "__main__":
    main()
