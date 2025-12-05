"""
Exhaustive line-by-line tests for configure_logging() to meet codecov/patch.
Tests every single code path in the new function.
"""

import logging
import sys

from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk.resources import Resource

import otel_init


def test_line_by_line_without_provider():
    """Execute every line of configure_logging without provider."""
    # Line: global _global_logger_provider
    otel_init._global_logger_provider = None

    # Clear handlers
    logging.getLogger().handlers.clear()

    # Line: try:
    # Line: handlers_config = {
    # Line: "stdout": {...}
    # Line: handler_names = ["stdout"]
    # Line: if _global_logger_provider is not None: (False branch)
    # Line: logging_config = {...}
    # Line: logging.config.dictConfig(logging_config)
    result = otel_init.configure_logging()

    # Line: print statements
    # Line: return True
    assert result is True

    # Verify all components configured
    root = logging.getLogger()
    assert root.level == logging.INFO
    assert len(root.handlers) > 0

    # Verify stdout handler details
    stdout_handler = None
    for h in root.handlers:
        if isinstance(h, logging.StreamHandler) and h.stream == sys.stdout:
            stdout_handler = h
            break

    assert stdout_handler is not None
    assert stdout_handler.level == logging.INFO
    assert stdout_handler.formatter is not None

    # Verify formatter format string
    fmt = stdout_handler.formatter._fmt
    assert "%(asctime)s" in fmt
    assert "%(name)s" in fmt
    assert "%(levelname)s" in fmt
    assert "%(message)s" in fmt

    # Verify datetime format
    datefmt = stdout_handler.formatter.datefmt
    assert datefmt == "%Y-%m-%d %H:%M:%S"


def test_line_by_line_with_provider():
    """Execute every line of configure_logging WITH provider."""
    # Create real provider
    resource = Resource.create({"service.name": "test"})
    provider = LoggerProvider(resource=resource)

    # Line: global _global_logger_provider
    otel_init._global_logger_provider = provider

    # Clear handlers
    logging.getLogger().handlers.clear()
    logging.getLogger("uvicorn").handlers.clear()
    logging.getLogger("uvicorn.access").handlers.clear()
    logging.getLogger("uvicorn.error").handlers.clear()

    # Line: try:
    # Line: handlers_config = {
    # Line: handler_names = ["stdout"]
    # Line: if _global_logger_provider is not None: (True branch)
    # Line: handler_names.append("otlp")
    # Line: logging_config = {...}
    # Line: logging.config.dictConfig(logging_config)
    # Line: if _global_logger_provider is not None: (True branch again)
    # Line: otlp_handler = LoggingHandler(...)
    # Line: root_logger = logging.getLogger()
    # Line: root_logger.addHandler(otlp_handler)
    # Line: logging.getLogger("uvicorn").addHandler(otlp_handler)
    # Line: logging.getLogger("uvicorn.access").addHandler(otlp_handler)
    # Line: logging.getLogger("uvicorn.error").addHandler(otlp_handler)
    result = otel_init.configure_logging()

    # Line: print statements
    # Line: return True
    assert result is True

    # Verify OTLP handler added
    from opentelemetry.sdk._logs import LoggingHandler

    root = logging.getLogger()
    otlp_handlers = [h for h in root.handlers if isinstance(h, LoggingHandler)]
    assert len(otlp_handlers) > 0

    # Verify added to uvicorn loggers
    uvicorn = logging.getLogger("uvicorn")
    uvicorn_otlp = [h for h in uvicorn.handlers if isinstance(h, LoggingHandler)]
    assert len(uvicorn_otlp) > 0

    uvicorn_access = logging.getLogger("uvicorn.access")
    access_otlp = [h for h in uvicorn_access.handlers if isinstance(h, LoggingHandler)]
    assert len(access_otlp) > 0

    uvicorn_error = logging.getLogger("uvicorn.error")
    error_otlp = [h for h in uvicorn_error.handlers if isinstance(h, LoggingHandler)]
    assert len(error_otlp) > 0

    # Cleanup
    otel_init._global_logger_provider = None


def test_exception_path_dictconfig_error():
    """Test exception handling path in configure_logging."""
    from unittest.mock import patch

    # Line: except Exception as e:
    # Line: print(f"⚠️  Failed to configure logging: {e}")
    # Line: traceback.print_exc()
    # Line: return False
    with patch("logging.config.dictConfig", side_effect=ValueError("Test error")):
        result = otel_init.configure_logging()
        assert result is False


def test_exception_path_otlp_handler_error():
    """Test exception when OTLP handler creation fails."""
    from unittest.mock import patch

    provider = LoggerProvider(resource=Resource.create({"service.name": "test"}))
    otel_init._global_logger_provider = provider

    logging.getLogger().handlers.clear()

    # Line: except Exception as e: (when LoggingHandler fails)
    # Line: print, traceback, return False
    with patch("otel_init.LoggingHandler", side_effect=RuntimeError("Handler error")):
        result = otel_init.configure_logging()
        assert result is False

    otel_init._global_logger_provider = None


def test_attach_wrapper_executes_every_line():
    """Test attach_logging_handler wrapper executes all its lines."""
    import warnings

    logging.getLogger().handlers.clear()
    otel_init._global_logger_provider = None

    # Capture warnings
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")

        # Line: import warnings
        # Line: warnings.warn(...)
        # Line: return configure_logging()
        result = otel_init.attach_logging_handler()

        assert result is True
        # Verify deprecation warning was issued
        assert len(w) > 0
        assert issubclass(w[0].category, DeprecationWarning)
        assert "deprecated" in str(w[0].message).lower()


def test_logging_config_structure_complete():
    """Test that complete logging_config dict is built."""
    logging.getLogger().handlers.clear()
    otel_init._global_logger_provider = None

    # Execute to ensure all dict building lines run
    result = otel_init.configure_logging()

    assert result is True

    # Verify version 1 was used (dictConfig line)
    # Verify disable_existing_loggers=False worked
    root = logging.getLogger()

    # Verify formatters section worked
    for h in root.handlers:
        if isinstance(h, logging.StreamHandler):
            assert h.formatter is not None
            # Verify standard formatter format
            assert hasattr(h.formatter, "_fmt")

    # Verify loggers section worked
    assert root.level == logging.INFO
    assert logging.getLogger("uvicorn").level == logging.INFO
    assert logging.getLogger("uvicorn.access").level == logging.INFO
    assert logging.getLogger("uvicorn.error").level == logging.INFO

    # Verify handlers section worked
    assert len(root.handlers) > 0
    assert len(logging.getLogger("uvicorn").handlers) > 0
    assert len(logging.getLogger("uvicorn.access").handlers) > 0
    assert len(logging.getLogger("uvicorn.error").handlers) > 0
