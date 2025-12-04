"""
Direct coverage tests for otel_init.py configure_logging paths.
"""

import logging
import sys
from unittest.mock import MagicMock, patch

import otel_init


def test_configure_logging_stdout_stream():
    """Test that stdout stream is configured correctly."""
    logging.getLogger().handlers.clear()
    otel_init._global_logger_provider = None

    # Verify sys.stdout is available
    assert sys.stdout is not None

    result = otel_init.configure_logging()
    assert result is True

    # Check that a StreamHandler exists
    root = logging.getLogger()
    has_stream_handler = any(
        isinstance(h, logging.StreamHandler) for h in root.handlers
    )
    assert has_stream_handler


def test_configure_logging_sets_info_level():
    """Test that root logger level is set to INFO."""
    logging.getLogger().handlers.clear()
    otel_init._global_logger_provider = None

    otel_init.configure_logging()

    root = logging.getLogger()
    assert root.level == logging.INFO


def test_configure_logging_uvicorn_propagate_false():
    """Test that uvicorn loggers don't propagate."""
    logging.getLogger().handlers.clear()
    otel_init._global_logger_provider = None

    otel_init.configure_logging()

    # Uvicorn loggers should have propagate=False
    assert logging.getLogger("uvicorn").propagate is False
    assert logging.getLogger("uvicorn.access").propagate is False
    assert logging.getLogger("uvicorn.error").propagate is False


def test_configure_logging_formatters():
    """Test that standard formatter is configured."""
    logging.getLogger().handlers.clear()
    otel_init._global_logger_provider = None

    otel_init.configure_logging()

    # Check that handlers have formatters
    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, logging.StreamHandler):
            assert handler.formatter is not None


def test_configure_logging_with_otlp_creates_handler():
    """Test OTLP handler creation with provider."""
    logging.getLogger().handlers.clear()
    mock_provider = MagicMock()
    otel_init._global_logger_provider = mock_provider

    with patch("otel_init.LoggingHandler") as MockHandler:
        mock_handler = MagicMock()
        MockHandler.return_value = mock_handler

        result = otel_init.configure_logging()

        assert result is True
        # Verify handler was created with correct parameters
        MockHandler.assert_called_once_with(
            level=logging.NOTSET, logger_provider=mock_provider
        )


def test_configure_logging_otlp_added_to_root():
    """Test OTLP handler added to root logger."""
    logging.getLogger().handlers.clear()
    mock_provider = MagicMock()
    otel_init._global_logger_provider = mock_provider

    with patch("otel_init.LoggingHandler") as MockHandler:
        mock_handler = MagicMock()
        MockHandler.return_value = mock_handler

        otel_init.configure_logging()

        root = logging.getLogger()
        assert mock_handler in root.handlers


def test_attach_logging_handler_returns_true():
    """Test backward compat wrapper returns result."""
    logging.getLogger().handlers.clear()
    otel_init._global_logger_provider = None

    with patch("otel_init.configure_logging", return_value=True) as mock_config:
        result = otel_init.attach_logging_handler()
        assert result is True
        mock_config.assert_called_once()


def test_attach_logging_handler_returns_false_on_error():
    """Test backward compat wrapper returns False on error."""
    logging.getLogger().handlers.clear()
    otel_init._global_logger_provider = None

    with patch("otel_init.configure_logging", return_value=False):
        result = otel_init.attach_logging_handler()
        assert result is False
