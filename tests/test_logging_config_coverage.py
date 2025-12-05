"""
Direct execution tests for configure_logging() to achieve codecov coverage.
These tests actually execute the code paths without excessive mocking.
"""

import logging
import sys

import otel_init


def test_configure_logging_executes_dictconfig_path():
    """Test that configure_logging actually executes dictConfig."""
    # Clear all loggers completely
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.WARNING)

    # Clear uvicorn loggers
    for name in ["uvicorn", "uvicorn.access", "uvicorn.error"]:
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.setLevel(logging.WARNING)

    # Set no OTLP provider
    otel_init._global_logger_provider = None

    # Execute the actual function
    result = otel_init.configure_logging()

    # Verify it succeeded
    assert result is True

    # Verify dictConfig was applied
    assert root.level == logging.INFO
    assert len(root.handlers) > 0

    # Verify stdout handler exists
    has_stdout = False
    for handler in root.handlers:
        if isinstance(handler, logging.StreamHandler):
            if handler.stream == sys.stdout:
                has_stdout = True
                # Verify formatter
                assert handler.formatter is not None
                # Verify level
                assert handler.level == logging.INFO

    assert has_stdout, "Stdout handler should be configured"

    # Verify uvicorn loggers configured
    uvicorn_logger = logging.getLogger("uvicorn")
    assert uvicorn_logger.level == logging.INFO
    assert uvicorn_logger.propagate is False
    assert len(uvicorn_logger.handlers) > 0

    uvicorn_access = logging.getLogger("uvicorn.access")
    assert uvicorn_access.level == logging.INFO
    assert uvicorn_access.propagate is False
    assert len(uvicorn_access.handlers) > 0

    uvicorn_error = logging.getLogger("uvicorn.error")
    assert uvicorn_error.level == logging.INFO
    assert uvicorn_error.propagate is False
    assert len(uvicorn_error.handlers) > 0


def test_configure_logging_with_real_otlp_provider():
    """Test configure_logging with actual LoggerProvider."""
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk.resources import Resource

    # Clear loggers
    root = logging.getLogger()
    root.handlers.clear()

    # Create REAL logger provider
    resource = Resource.create({"service.name": "test-service"})
    logger_provider = LoggerProvider(resource=resource)
    otel_init._global_logger_provider = logger_provider

    # Execute function with real provider
    result = otel_init.configure_logging()

    # Should succeed
    assert result is True

    # Should have both stdout and OTLP handlers
    assert len(root.handlers) >= 2

    # Verify OTLP handler exists
    from opentelemetry.sdk._logs import LoggingHandler

    has_otlp = any(isinstance(h, LoggingHandler) for h in root.handlers)
    assert has_otlp, "OTLP handler should be configured"

    # Cleanup
    otel_init._global_logger_provider = None


def test_attach_logging_handler_executes_configure():
    """Test that attach_logging_handler actually calls configure_logging."""
    root = logging.getLogger()
    root.handlers.clear()

    otel_init._global_logger_provider = None

    # Execute the wrapper
    result = otel_init.attach_logging_handler()

    # Should succeed
    assert result is True

    # Should have configured logging (handlers present)
    assert len(root.handlers) > 0
    assert root.level == logging.INFO


def test_configure_logging_multiple_calls_stable():
    """Test calling configure_logging multiple times is safe."""
    root = logging.getLogger()
    root.handlers.clear()

    otel_init._global_logger_provider = None

    # Call multiple times
    result1 = otel_init.configure_logging()
    result2 = otel_init.configure_logging()
    result3 = otel_init.configure_logging()

    # All should succeed
    assert result1 is True
    assert result2 is True
    assert result3 is True

    # Handlers should still be configured
    assert len(root.handlers) > 0


def test_configure_logging_handlers_config_structure():
    """Test that handlers_config is built correctly."""
    root = logging.getLogger()
    root.handlers.clear()

    otel_init._global_logger_provider = None

    result = otel_init.configure_logging()

    assert result is True

    # Verify handler has correct attributes
    for handler in root.handlers:
        if isinstance(handler, logging.StreamHandler):
            # Should have formatter
            assert handler.formatter is not None
            # Format should match our pattern
            format_str = handler.formatter._fmt
            assert "asctime" in format_str
            assert "name" in format_str
            assert "levelname" in format_str
            assert "message" in format_str


def test_configure_logging_disable_existing_loggers_false():
    """Test that disable_existing_loggers=False allows existing loggers to work."""
    root = logging.getLogger()
    root.handlers.clear()

    # Create a custom logger before configure_logging
    custom_logger = logging.getLogger("custom.test.logger")
    custom_logger.setLevel(logging.DEBUG)

    otel_init._global_logger_provider = None

    # Configure logging
    result = otel_init.configure_logging()

    assert result is True

    # Custom logger should still be enabled (not disabled)
    assert custom_logger.level == logging.DEBUG
    # Logger should still work (not disabled by dictConfig)
    assert logging.getLogger("custom.test.logger") is custom_logger
