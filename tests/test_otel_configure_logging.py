"""
Comprehensive tests for configure_logging() to meet codecov requirements.
"""

import logging
from unittest.mock import MagicMock, patch

import otel_init


class TestConfigureLogging:
    """Tests for configure_logging function."""

    def setup_method(self):
        """Reset logging before each test."""
        logging.getLogger().handlers.clear()
        logging.getLogger("uvicorn").handlers.clear()
        logging.getLogger("uvicorn.access").handlers.clear()
        logging.getLogger("uvicorn.error").handlers.clear()

    def test_configure_without_provider(self):
        """Test configuration without OTLP provider."""
        otel_init._global_logger_provider = None
        result = otel_init.configure_logging()
        assert result is True
        assert len(logging.getLogger().handlers) > 0

    def test_configure_with_provider(self):
        """Test configuration with OTLP provider."""
        mock_provider = MagicMock()
        otel_init._global_logger_provider = mock_provider

        with patch("otel_init.LoggingHandler") as MockHandler:
            mock_handler = MagicMock()
            MockHandler.return_value = mock_handler

            result = otel_init.configure_logging()

            assert result is True
            MockHandler.assert_called_once()
            # Handler should be added to root logger
            assert mock_handler in logging.getLogger().handlers

    def test_handler_added_to_uvicorn_loggers(self):
        """Test OTLP handler added to uvicorn loggers."""
        mock_provider = MagicMock()
        otel_init._global_logger_provider = mock_provider

        with patch("otel_init.LoggingHandler") as MockHandler:
            mock_handler = MagicMock()
            MockHandler.return_value = mock_handler

            otel_init.configure_logging()

            # Verify added to uvicorn loggers
            assert mock_handler in logging.getLogger("uvicorn").handlers
            assert mock_handler in logging.getLogger("uvicorn.access").handlers
            assert mock_handler in logging.getLogger("uvicorn.error").handlers

    def test_exception_handling(self):
        """Test exception handling in configure_logging."""
        with patch("logging.config.dictConfig", side_effect=Exception("Config error")):
            result = otel_init.configure_logging()
            assert result is False

    def test_otlp_handler_exception(self):
        """Test exception when creating OTLP handler."""
        mock_provider = MagicMock()
        otel_init._global_logger_provider = mock_provider

        with patch(
            "otel_init.LoggingHandler", side_effect=RuntimeError("Handler error")
        ):
            result = otel_init.configure_logging()
            assert result is False


class TestAttachLoggingHandlerBackwardCompat:
    """Tests for backward compatibility wrapper."""

    def test_wrapper_calls_configure(self):
        """Test wrapper calls configure_logging."""
        logging.getLogger().handlers.clear()
        otel_init._global_logger_provider = None

        with patch("otel_init.configure_logging", return_value=True) as mock_configure:
            result = otel_init.attach_logging_handler()
            assert result is True
            mock_configure.assert_called_once()

    def test_wrapper_issues_warning(self):
        """Test deprecation warning is issued."""
        logging.getLogger().handlers.clear()
        otel_init._global_logger_provider = None

        with (
            patch("warnings.warn") as mock_warn,
            patch("otel_init.configure_logging", return_value=True),
        ):
            otel_init.attach_logging_handler()
            mock_warn.assert_called_once()
            assert "deprecated" in str(mock_warn.call_args[0][0]).lower()
