"""
Complete integration test for logging configuration.
Tests the full path from api.py calling otel_init.configure_logging().
"""

import logging
import sys

from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk.resources import Resource

import otel_init


def test_complete_logging_flow_without_otlp():
    """Test complete logging setup flow without OTLP (as api.py would call it)."""
    # Reset logging
    for name in ["", "uvicorn", "uvicorn.access", "uvicorn.error"]:
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.setLevel(logging.WARNING)

    # Simulate what api.py lifespan does
    otel_init._global_logger_provider = None

    # Call configure_logging (line 40 in api.py)
    result = otel_init.configure_logging()

    # Verify success (line 41 in api.py logs this)
    assert result is True

    # Verify logging is actually configured
    root = logging.getLogger()
    assert root.level == logging.INFO
    assert len(root.handlers) > 0

    # Verify stdout handler
    has_stdout_handler = any(
        isinstance(h, logging.StreamHandler) and h.stream == sys.stdout
        for h in root.handlers
    )
    assert has_stdout_handler

    # Verify uvicorn loggers
    assert logging.getLogger("uvicorn").level == logging.INFO
    assert logging.getLogger("uvicorn").propagate is False
    assert len(logging.getLogger("uvicorn").handlers) > 0


def test_complete_logging_flow_with_otlp():
    """Test complete logging setup WITH OTLP provider."""
    # Reset logging
    for name in ["", "uvicorn", "uvicorn.access", "uvicorn.error"]:
        logger = logging.getLogger(name)
        logger.handlers.clear()

    # Create real OTLP provider (as setup_telemetry does)
    resource = Resource.create({"service.name": "tradeengine"})
    provider = LoggerProvider(resource=resource)
    otel_init._global_logger_provider = provider

    # Call configure_logging
    result = otel_init.configure_logging()

    assert result is True

    # Verify OTLP handler added
    from opentelemetry.sdk._logs import LoggingHandler

    root = logging.getLogger()
    has_otlp = any(isinstance(h, LoggingHandler) for h in root.handlers)
    assert has_otlp

    # Verify uvicorn loggers also got OTLP handler
    uvicorn_has_otlp = any(
        isinstance(h, LoggingHandler) for h in logging.getLogger("uvicorn").handlers
    )
    assert uvicorn_has_otlp

    # Cleanup
    otel_init._global_logger_provider = None


def test_logging_survives_basicconfig_call():
    """Test that handlers survive logging.basicConfig (key feature)."""
    # Reset
    root = logging.getLogger()
    root.handlers.clear()

    otel_init._global_logger_provider = None

    # Configure via our function
    otel_init.configure_logging()

    # Get handler count
    handler_count_before = len(root.handlers)

    # Call basicConfig (this would remove handlers in old implementation)
    # Using force=False which is the default
    logging.basicConfig(level=logging.DEBUG, force=False)

    # Handlers should still exist (disable_existing_loggers=False)
    handler_count_after = len(root.handlers)

    # Should have at least the same number (basicConfig might add more but won't remove)
    assert handler_count_after >= handler_count_before


def test_formatter_configuration_details():
    """Test that formatter is configured with correct format."""
    root = logging.getLogger()
    root.handlers.clear()

    otel_init._global_logger_provider = None
    otel_init.configure_logging()

    # Find stdout handler and check formatter
    for handler in root.handlers:
        if isinstance(handler, logging.StreamHandler) and handler.stream == sys.stdout:
            assert handler.formatter is not None
            fmt = handler.formatter._fmt

            # Verify all required format components
            assert "%(asctime)s" in fmt
            assert "%(name)s" in fmt
            assert "%(levelname)s" in fmt
            assert "%(message)s" in fmt

            # Verify date format
            assert handler.formatter.datefmt == "%Y-%m-%d %H:%M:%S"
            break


def test_handler_level_configuration():
    """Test that handler levels are set correctly."""
    root = logging.getLogger()
    root.handlers.clear()

    otel_init._global_logger_provider = None
    otel_init.configure_logging()

    # Stdout handler should be INFO level
    for handler in root.handlers:
        if isinstance(handler, logging.StreamHandler):
            assert handler.level == logging.INFO


def test_deprecation_warning_mechanism():
    """Test the deprecation warning in attach_logging_handler wrapper."""
    import warnings

    root = logging.getLogger()
    root.handlers.clear()
    otel_init._global_logger_provider = None

    # Catch warnings
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")

        # Call deprecated function
        result = otel_init.attach_logging_handler()

        # Should succeed
        assert result is True

        # Should have issued warning
        assert len(w) >= 1

        # Find the deprecation warning
        deprecation_warnings = [
            warning for warning in w if issubclass(warning.category, DeprecationWarning)
        ]
        assert len(deprecation_warnings) >= 1

        # Verify message content
        warning_msg = str(deprecation_warnings[0].message)
        assert "deprecated" in warning_msg.lower()
        assert "configure_logging" in warning_msg
