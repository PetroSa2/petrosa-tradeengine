"""
Tests for graceful telemetry shutdown functions in otel_init.py.

Tests flush_telemetry(), shutdown_telemetry(), and setup_signal_handlers()
to ensure telemetry data is properly flushed and providers are shut down
during graceful shutdown scenarios.
"""

import signal
from unittest.mock import MagicMock, patch

import otel_init


class TestFlushTelemetry:
    """Test suite for flush_telemetry function."""

    def test_flush_telemetry_with_all_providers(self):
        """Test flushing telemetry with all providers configured."""
        # Mock providers
        mock_tracer_provider = MagicMock()
        mock_tracer_provider.force_flush = MagicMock()

        mock_meter_provider = MagicMock()
        mock_meter_provider.force_flush = MagicMock()

        mock_logger_provider = MagicMock()
        mock_logger_provider.force_flush = MagicMock()

        with patch(
            "otel_init.trace.get_tracer_provider", return_value=mock_tracer_provider
        ):
            with patch(
                "otel_init.metrics.get_meter_provider", return_value=mock_meter_provider
            ):
                with patch("otel_init._global_logger_provider", mock_logger_provider):
                    otel_init.flush_telemetry()

        # Verify all providers were flushed
        mock_tracer_provider.force_flush.assert_called_once()
        mock_meter_provider.force_flush.assert_called_once()
        mock_logger_provider.force_flush.assert_called_once()

    def test_flush_telemetry_with_timeout(self):
        """Test flushing telemetry with custom timeout."""
        mock_tracer_provider = MagicMock()
        mock_tracer_provider.force_flush = MagicMock()

        with patch(
            "otel_init.trace.get_tracer_provider", return_value=mock_tracer_provider
        ):
            with patch(
                "otel_init.metrics.get_meter_provider", return_value=MagicMock()
            ):
                with patch("otel_init._global_logger_provider", None):
                    otel_init.flush_telemetry(timeout_seconds=2.0)

        # Verify flush was called (may or may not include timeout parameter)
        assert mock_tracer_provider.force_flush.called

    def test_flush_telemetry_without_providers(self):
        """Test flushing when providers are not configured."""
        mock_tracer_provider = MagicMock(spec=[])  # No force_flush method
        mock_meter_provider = MagicMock(spec=[])  # No force_flush method

        with patch(
            "otel_init.trace.get_tracer_provider", return_value=mock_tracer_provider
        ):
            with patch(
                "otel_init.metrics.get_meter_provider", return_value=mock_meter_provider
            ):
                with patch("otel_init._global_logger_provider", None):
                    # Should not raise exception
                    otel_init.flush_telemetry()

    def test_flush_telemetry_handles_exceptions(self):
        """Test that flush_telemetry handles provider exceptions gracefully."""
        mock_tracer_provider = MagicMock()
        mock_tracer_provider.force_flush = MagicMock(
            side_effect=Exception("Flush failed")
        )

        with patch(
            "otel_init.trace.get_tracer_provider", return_value=mock_tracer_provider
        ):
            with patch(
                "otel_init.metrics.get_meter_provider", return_value=MagicMock()
            ):
                with patch("otel_init._global_logger_provider", None):
                    # Should not raise exception
                    otel_init.flush_telemetry()


class TestShutdownTelemetry:
    """Test suite for shutdown_telemetry function."""

    def test_shutdown_telemetry_with_all_providers(self):
        """Test shutting down telemetry with all providers configured."""
        # Mock providers
        mock_tracer_provider = MagicMock()
        mock_tracer_provider.shutdown = MagicMock()

        mock_meter_provider = MagicMock()
        mock_meter_provider.shutdown = MagicMock()

        mock_logger_provider = MagicMock()
        mock_logger_provider.shutdown = MagicMock()

        with patch(
            "otel_init.trace.get_tracer_provider", return_value=mock_tracer_provider
        ):
            with patch(
                "otel_init.metrics.get_meter_provider", return_value=mock_meter_provider
            ):
                with patch("otel_init._global_logger_provider", mock_logger_provider):
                    otel_init.shutdown_telemetry()

        # Verify all providers were shut down
        mock_tracer_provider.shutdown.assert_called_once()
        mock_meter_provider.shutdown.assert_called_once()
        mock_logger_provider.shutdown.assert_called_once()

    def test_shutdown_telemetry_without_providers(self):
        """Test shutting down when providers are not configured."""
        mock_tracer_provider = MagicMock(spec=[])  # No shutdown method
        mock_meter_provider = MagicMock(spec=[])  # No shutdown method

        with patch(
            "otel_init.trace.get_tracer_provider", return_value=mock_tracer_provider
        ):
            with patch(
                "otel_init.metrics.get_meter_provider", return_value=mock_meter_provider
            ):
                with patch("otel_init._global_logger_provider", None):
                    # Should not raise exception
                    otel_init.shutdown_telemetry()

    def test_shutdown_telemetry_handles_exceptions(self):
        """Test that shutdown_telemetry handles provider exceptions gracefully."""
        mock_tracer_provider = MagicMock()
        mock_tracer_provider.shutdown = MagicMock(
            side_effect=Exception("Shutdown failed")
        )

        with patch(
            "otel_init.trace.get_tracer_provider", return_value=mock_tracer_provider
        ):
            with patch(
                "otel_init.metrics.get_meter_provider", return_value=MagicMock()
            ):
                with patch("otel_init._global_logger_provider", None):
                    # Should not raise exception
                    otel_init.shutdown_telemetry()


class TestSetupSignalHandlers:
    """Test suite for setup_signal_handlers function."""

    def test_setup_signal_handlers_registers_handlers(self):
        """Test that signal handlers are registered for SIGTERM and SIGINT."""
        with patch("signal.signal") as mock_signal:
            otel_init.setup_signal_handlers()

        # Verify signal handlers were registered for both signals
        assert mock_signal.call_count == 2
        calls = [call[0][0] for call in mock_signal.call_args_list]
        assert signal.SIGTERM in calls
        assert signal.SIGINT in calls

    def test_signal_handler_flushes_telemetry(self):
        """Test that signal handler calls flush_telemetry."""
        with patch("signal.signal") as mock_signal:
            otel_init.setup_signal_handlers()

        # Get the registered handler
        handler_call = mock_signal.call_args_list[0]
        registered_handler = handler_call[0][1]

        # Mock flush_telemetry
        with patch("otel_init.flush_telemetry") as mock_flush:
            # Simulate signal
            registered_handler(signal.SIGTERM, None)

        # Verify flush was called
        mock_flush.assert_called_once()

    def test_signal_handler_does_not_exit(self):
        """Test that signal handler does not call sys.exit() (allows FastAPI shutdown)."""
        with patch("signal.signal") as mock_signal:
            otel_init.setup_signal_handlers()

        # Get the registered handler
        handler_call = mock_signal.call_args_list[0]
        registered_handler = handler_call[0][1]

        # Mock flush and verify sys.exit is NOT called
        with patch("otel_init.flush_telemetry"):
            with patch("sys.exit") as mock_exit:
                registered_handler(signal.SIGTERM, None)

        # Verify sys.exit was NOT called (allows uvicorn to handle shutdown)
        mock_exit.assert_not_called()
