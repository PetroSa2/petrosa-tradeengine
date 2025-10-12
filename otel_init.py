"""
OpenTelemetry initialization for the Trade Engine service.

This module sets up OpenTelemetry instrumentation for observability
and monitoring of the trading engine service.
"""

import os
from typing import Optional

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.instrumentation.requests import RequestsInstrumentor
from opentelemetry.instrumentation.urllib3 import URLLib3Instrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def setup_telemetry(
    service_name: str = "tradeengine",
    service_version: Optional[str] = None,
    otlp_endpoint: Optional[str] = None,
    enable_metrics: bool = True,
    enable_traces: bool = True,
    enable_logs: bool = True,
) -> None:
    """
    Set up OpenTelemetry instrumentation.

    Args:
        service_name: Name of the service
        service_version: Version of the service
        otlp_endpoint: OTLP endpoint URL
        enable_metrics: Whether to enable metrics
        enable_traces: Whether to enable traces
        enable_logs: Whether to enable logs
    """
    # Get configuration from environment variables
    service_version = service_version or os.getenv("OTEL_SERVICE_VERSION", "1.0.0")
    otlp_endpoint = otlp_endpoint or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    enable_metrics = enable_metrics and os.getenv("ENABLE_METRICS", "true").lower() in (
        "true",
        "1",
        "yes",
    )
    enable_traces = enable_traces and os.getenv("ENABLE_TRACES", "true").lower() in (
        "true",
        "1",
        "yes",
    )
    enable_logs = enable_logs and os.getenv("ENABLE_LOGS", "true").lower() in (
        "true",
        "1",
        "yes",
    )

    # Create resource attributes
    resource_attributes = {
        "service.name": service_name,
        "service.version": service_version,
        "service.instance.id": os.getenv("HOSTNAME", "unknown"),
        "deployment.environment": os.getenv("ENVIRONMENT", "production"),
    }

    # Add custom resource attributes if provided
    custom_attributes = os.getenv("OTEL_RESOURCE_ATTRIBUTES")
    if custom_attributes:
        for attr in custom_attributes.split(","):
            if "=" in attr:
                key, value = attr.split("=", 1)
                resource_attributes[key.strip()] = value.strip()

    # Filter out None values to satisfy type checker
    filtered_attributes = {
        k: v for k, v in resource_attributes.items() if v is not None
    }
    resource = Resource.create(filtered_attributes)

    # Set up tracing if enabled
    if enable_traces and otlp_endpoint:
        try:
            # Create tracer provider
            tracer_provider = TracerProvider(resource=resource)

            # Create OTLP exporter
            headers_env = os.getenv("OTEL_EXPORTER_OTLP_HEADERS")
            headers: dict[str, str] | None = None
            if headers_env:
                # Parse headers as "key1=value1,key2=value2" format
                headers_list = [
                    tuple(h.split("=", 1)) for h in headers_env.split(",") if "=" in h
                ]
                headers = {k: v for k, v in headers_list}
            otlp_exporter = OTLPSpanExporter(
                endpoint=otlp_endpoint,
                headers=headers,
            )

            # Add batch processor
            tracer_provider.add_span_processor(BatchSpanProcessor(otlp_exporter))

            # Set global tracer provider
            trace.set_tracer_provider(tracer_provider)

            print(f"✅ OpenTelemetry tracing enabled for {service_name}")

        except Exception as e:
            print(f"⚠️  Failed to set up OpenTelemetry tracing: {e}")

    # Set up metrics if enabled
    if enable_metrics and otlp_endpoint:
        try:
            # Create metric reader
            headers_env = os.getenv("OTEL_EXPORTER_OTLP_HEADERS")
            headers: dict[str, str] | None = None
            if headers_env:
                # Parse headers as "key1=value1,key2=value2" format
                headers_list = [
                    tuple(h.split("=", 1)) for h in headers_env.split(",") if "=" in h
                ]
                headers = {k: v for k, v in headers_list}
            metric_reader = PeriodicExportingMetricReader(
                OTLPMetricExporter(
                    endpoint=otlp_endpoint,
                    headers=headers,
                ),
                export_interval_millis=int(
                    os.getenv("OTEL_METRIC_EXPORT_INTERVAL", "60000")
                ),
            )

            # Create meter provider
            meter_provider = MeterProvider(
                resource=resource, metric_readers=[metric_reader]
            )

            # Set global meter provider
            metrics.set_meter_provider(meter_provider)

            print(f"✅ OpenTelemetry metrics enabled for {service_name}")

        except Exception as e:
            print(f"⚠️  Failed to set up OpenTelemetry metrics: {e}")

    # Set up logging instrumentation if enabled
    if enable_logs:
        try:
            LoggingInstrumentor().instrument(
                set_logging_format=True, log_level=os.getenv("LOG_LEVEL", "INFO")
            )
            print(f"✅ OpenTelemetry logging enabled for {service_name}")

        except Exception as e:
            print(f"⚠️  Failed to set up OpenTelemetry logging: {e}")

    # Set up HTTP instrumentation
    try:
        RequestsInstrumentor().instrument()
        URLLib3Instrumentor().instrument()
        print(f"✅ OpenTelemetry HTTP instrumentation enabled for {service_name}")

    except Exception as e:
        print(f"⚠️  Failed to set up OpenTelemetry HTTP instrumentation: {e}")

    # Set up FastAPI instrumentation (will be applied when app is created)
    try:
        # FastAPI instrumentation will be applied via instrument_app() call
        print(f"✅ OpenTelemetry FastAPI instrumentation ready for {service_name}")
    except Exception as e:
        print(f"⚠️  Failed to prepare OpenTelemetry FastAPI instrumentation: {e}")

    print(f"🚀 OpenTelemetry setup completed for {service_name} v{service_version}")


def instrument_fastapi_app(app):
    """
    Instrument a FastAPI application.

    Args:
        app: FastAPI application instance
    """
    try:
        FastAPIInstrumentor.instrument_app(app)
        print("✅ FastAPI application instrumented")
    except Exception as e:
        print(f"⚠️  Failed to instrument FastAPI application: {e}")


def get_tracer(name: str = None) -> trace.Tracer:
    """
    Get a tracer instance.

    Args:
        name: Tracer name

    Returns:
        Tracer instance
    """
    return trace.get_tracer(name or "tradeengine")


def get_meter(name: str = None) -> metrics.Meter:
    """
    Get a meter instance.

    Args:
        name: Meter name

    Returns:
        Meter instance
    """
    return metrics.get_meter(name or "tradeengine")


# Auto-setup if environment variable is set
if os.getenv("OTEL_AUTO_SETUP", "true").lower() in ("true", "1", "yes"):
    setup_telemetry()
