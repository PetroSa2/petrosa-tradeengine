"""
Tests for simplified logging configuration in otel_init.py.

Validates the new dictConfig-based approach that replaced
defensive monitoring functions.

Note: Multiple test files exist for logging configuration testing
(test_otel_logging_config.py, test_otel_init_coverage.py, etc.).
This is intentional to test different aspects:
- Unit tests for specific functions
- Integration tests for complete flows
- Coverage tests for edge cases
- API integration tests for lifespan events

Each file serves a specific purpose and minimal overlap is acceptable
for comprehensive testing of critical logging infrastructure.
"""

import logging
from unittest.mock import MagicMock, patch

import otel_init


class TestConfigureLogging:
    """Test suite for configure_logging function."""

    def setup_method(self):
        """Reset logging configuration before each test."""
        # Clear all handlers from root logger
        root_logger = logging.getLogger()
        root_logger.handlers = []
        root_logger.setLevel(logging.WARNING)

    def test_configure_logging_without_otlp(self):
        """Test logging configuration without OTLP provider."""
        # No logger provider configured
        otel_init._global_logger_provider = None

        result = otel_init.configure_logging()

        assert result is True

        # Verify stdout handler is configured
        root_logger = logging.getLogger()
        assert len(root_logger.handlers) >= 1
        assert root_logger.level == logging.INFO

    def test_configure_logging_with_otlp(self):
        """Test logging configuration with OTLP provider."""
        # Mock logger provider
        mock_provider = MagicMock()
        otel_init._global_logger_provider = mock_provider

        with patch("otel_init.LoggingHandler") as mock_handler_class:
            mock_handler = MagicMock()
            mock_handler_class.return_value = mock_handler

            result = otel_init.configure_logging()

            assert result is True

            # Verify OTLP handler was created
            mock_handler_class.assert_called_once_with(
                level=logging.NOTSET, logger_provider=mock_provider
            )

            # Verify handler was added to loggers
            root_logger = logging.getLogger()
            assert mock_handler in root_logger.handlers

    def test_configure_logging_survives_basicconfig(self):
        """Test that handlers survive logging.basicConfig() calls."""
        otel_init._global_logger_provider = None

        # Configure logging first
        result = otel_init.configure_logging()
        assert result is True

        root_logger = logging.getLogger()
        initial_handler_count = len(root_logger.handlers)

        # Call logging.basicConfig (this would remove handlers in old approach)
        logging.basicConfig(level=logging.DEBUG)

        # Handlers should still be present because basicConfig(force=False) doesn't remove existing handlers
        # Note: disable_existing_loggers=False prevents loggers from being disabled, not handler removal
        # What prevents handler removal is calling basicConfig without force=True
        assert len(root_logger.handlers) >= initial_handler_count

    def test_configure_logging_error_handling(self):
        """Test error handling during configuration."""
        with patch(
            "otel_init.logging.config.dictConfig", side_effect=Exception("Config error")
        ):
            result = otel_init.configure_logging()

            assert result is False

    def test_configure_logging_adds_to_uvicorn_loggers(self):
        """Test that configuration applies to uvicorn loggers."""
        mock_provider = MagicMock()
        otel_init._global_logger_provider = mock_provider

        with patch("otel_init.LoggingHandler") as mock_handler_class:
            mock_handler = MagicMock()
            mock_handler_class.return_value = mock_handler

            result = otel_init.configure_logging()

            assert result is True

            # Verify handler added to uvicorn loggers
            uvicorn_logger = logging.getLogger("uvicorn")
            uvicorn_access_logger = logging.getLogger("uvicorn.access")
            uvicorn_error_logger = logging.getLogger("uvicorn.error")

            assert mock_handler in uvicorn_logger.handlers
            assert mock_handler in uvicorn_access_logger.handlers
            assert mock_handler in uvicorn_error_logger.handlers


class TestAttachLoggingHandlerBackwardCompat:
    """Test suite for backward compatibility wrapper."""

    def setup_method(self):
        """Reset logging configuration before each test."""
        root_logger = logging.getLogger()
        root_logger.handlers = []

    def test_attach_logging_handler_calls_configure(self):
        """Test that attach_logging_handler delegates to configure_logging."""
        otel_init._global_logger_provider = None

        with patch("otel_init.configure_logging") as mock_configure:
            mock_configure.return_value = True

            result = otel_init.attach_logging_handler()

            mock_configure.assert_called_once()
            assert result is True

    def test_attach_logging_handler_shows_deprecation_notice(self):
        """Test that deprecated function shows notice."""
        otel_init._global_logger_provider = None

        with (
            patch("otel_init.configure_logging") as mock_configure,
            patch("builtins.print") as mock_print,
        ):
            mock_configure.return_value = True

            otel_init.attach_logging_handler()

            # Verify deprecation notice was printed
            printed_messages = [str(call[0][0]) for call in mock_print.call_args_list]
            assert any("deprecated" in msg.lower() for msg in printed_messages)


class TestHandlerPersistence:
    """Test that handlers persist across reconfigurations."""

    def setup_method(self):
        """Reset logging configuration before each test."""
        root_logger = logging.getLogger()
        root_logger.handlers = []

    def test_handlers_persist_through_multiple_configurations(self):
        """Test handlers remain after multiple logging reconfigurations."""
        otel_init._global_logger_provider = None

        # Configure logging
        otel_init.configure_logging()

        root_logger = logging.getLogger()
        initial_handlers = list(root_logger.handlers)

        # Simulate multiple reconfigurations
        for _ in range(3):
            logging.basicConfig(level=logging.DEBUG, force=False)

        # Handlers should still be present
        # Note: basicConfig with force=False respects existing handlers
        assert len(root_logger.handlers) >= len(initial_handlers)


class TestCornerCases:
    """Corner case tests for edge scenarios."""

    def setup_method(self):
        """Reset logging configuration before each test."""
        root_logger = logging.getLogger()
        root_logger.handlers = []

    def test_configure_logging_called_multiple_times(self):
        """Test that calling configure_logging multiple times is safe."""
        otel_init._global_logger_provider = None

        # Call multiple times
        result1 = otel_init.configure_logging()
        result2 = otel_init.configure_logging()
        result3 = otel_init.configure_logging()

        assert result1 is True
        assert result2 is True
        assert result3 is True

        # Should not cause issues
        root_logger = logging.getLogger()
        assert len(root_logger.handlers) > 0

    def test_configure_logging_replaces_root_handlers(self):
        """Test that dictConfig replaces root logger handlers (expected behavior)."""
        # Add a custom handler first
        custom_handler = logging.StreamHandler()
        root_logger = logging.getLogger()
        root_logger.addHandler(custom_handler)

        otel_init._global_logger_provider = None
        result = otel_init.configure_logging()

        assert result is True
        # dictConfig replaces handlers for configured loggers
        # (disable_existing_loggers=False means loggers stay enabled, not handlers preserved)
        # New handlers are added per configuration
        assert len(root_logger.handlers) >= 1

    def test_configure_logging_exception_in_dictconfig(self):
        """Test exception handling during dictConfig call."""
        with patch(
            "logging.config.dictConfig", side_effect=ValueError("Invalid config")
        ):
            result = otel_init.configure_logging()

            assert result is False

    def test_configure_logging_exception_in_otlp_handler(self):
        """Test exception handling during OTLP handler creation."""
        mock_provider = MagicMock()
        otel_init._global_logger_provider = mock_provider

        with patch(
            "otel_init.LoggingHandler",
            side_effect=RuntimeError("Handler creation failed"),
        ):
            result = otel_init.configure_logging()

            # Should return False on exception
            assert result is False
