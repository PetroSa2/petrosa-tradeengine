"""
Pyroscope continuous profiling initialization for the Trade Engine service.

This module sets up Pyroscope profiling for performance analysis and optimization.
"""

import os

import pyroscope


def setup_profiler(
    service_name: str = "tradeengine",
    service_version: str = None,
) -> None:
    """
    Set up Pyroscope continuous profiling.

    Args:
        service_name: Name of the service
        service_version: Version of the service
    """
    # Check if profiling is enabled
    if os.getenv("ENABLE_PROFILER", "false").lower() not in ("true", "1", "yes"):
        return

    # Get configuration from environment variables
    service_version = service_version or os.getenv("OTEL_SERVICE_VERSION", "1.0.0")
    server_address = os.getenv("PYROSCOPE_SERVER_ADDRESS")
    auth_token = os.getenv("PYROSCOPE_AUTH_TOKEN", "")

    if not server_address:
        print("⚠️  PYROSCOPE_SERVER_ADDRESS not set, profiling disabled")
        return

    if not auth_token:
        print("⚠️  PYROSCOPE_AUTH_TOKEN not set, profiling disabled")
        return

    try:
        # Configure Pyroscope
        pyroscope.configure(
            application_name=service_name,
            server_address=server_address,
            auth_token=auth_token,
            # Tags for filtering and grouping profiles
            tags={
                "service.name": service_name,
                "service.version": service_version,
                "service.instance.id": os.getenv("HOSTNAME", "unknown"),
                "environment": os.getenv("ENVIRONMENT", "production"),
                "namespace": "petrosa-apps",
            },
            # Profiling configuration
            detect_subprocesses=True,  # Profile child processes
            oncpu=True,  # CPU profiling (wall time)
            native=False,  # Don't profile native C extensions (lower overhead)
            gil_only=True,  # Only profile when GIL is held (Python-specific)
            # Sample rate (default: 100Hz = 100 samples/second)
            sample_rate=100,
        )

        print(f"✅ Pyroscope continuous profiling enabled for {service_name}")
        print(f"   Server: {server_address}")
        print(f"   Version: {service_version}")
        print("   Profiling: CPU (oncpu), Sample rate: 100Hz")

    except Exception as e:
        print(f"⚠️  Failed to set up Pyroscope profiling: {e}")


# Auto-setup if environment variable is set
if os.getenv("ENABLE_PROFILER", "false").lower() in ("true", "1", "yes"):
    if not os.getenv("PYROSCOPE_NO_AUTO_INIT"):
        setup_profiler()
