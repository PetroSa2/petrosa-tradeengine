"""
Simple tests for configure_logging() in otel_init.py.
Tests the new dictConfig-based logging configuration.
"""

import logging

import otel_init


def test_configure_logging_basic():
    """Test basic configure_logging execution."""
    # Clear handlers
    root = logging.getLogger()
    root.handlers.clear()

    # No OTLP provider
    otel_init._global_logger_provider = None

    # Should succeed
    result = otel_init.configure_logging()
    assert result is True


def test_attach_logging_handler_wrapper():
    """Test backward compatibility wrapper."""
    # Clear handlers
    root = logging.getLogger()
    root.handlers.clear()

    otel_init._global_logger_provider = None

    # Should call configure_logging
    result = otel_init.attach_logging_handler()
    assert result is True
