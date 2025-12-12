"""
Integration tests for api.py lifespan function.
Actually executes the lifespan to achieve patch coverage for api.py changes.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_lifespan_startup_calls_configure_logging():
    """
    Test that lifespan startup actually calls otel_init.configure_logging().
    This provides coverage for the NEW line in api.py patch.
    """
    # Import inside test to ensure fresh state
    import tradeengine.api as api_module

    configure_was_called = []

    def track_configure():
        configure_was_called.append(True)
        return True

    mock_app = MagicMock()
    mock_app.state = MagicMock()

    # Mock all heavy dependencies
    with (
        patch.object(
            api_module.otel_init, "configure_logging", side_effect=track_configure
        ),
        patch("shared.constants.validate_mongodb_config"),
        patch("tradeengine.config_manager.TradingConfigManager") as MockConfig,
        patch.object(api_module, "binance_exchange") as mock_binance,
        patch.object(api_module, "simulator_exchange") as mock_sim,
        patch.object(api_module, "dispatcher") as mock_disp,
        patch("tradeengine.consumer.signal_consumer") as mock_consumer,
    ):

        # Setup async mocks
        mock_config = AsyncMock()
        mock_config.start = AsyncMock()
        mock_config.stop = AsyncMock()
        MockConfig.return_value = mock_config

        mock_binance.initialize = AsyncMock()
        mock_binance.close = AsyncMock()
        mock_sim.initialize = AsyncMock()
        mock_sim.close = AsyncMock()
        mock_disp.initialize = AsyncMock()
        mock_disp.close = AsyncMock()
        mock_consumer.initialize = AsyncMock(return_value=False)
        mock_consumer.running = False

        # Execute lifespan - THIS RUNS THE ACTUAL api.py CODE
        async with api_module.lifespan(mock_app):
            pass

        # Verify configure_logging was called from within api.py
        assert len(configure_was_called) > 0


@pytest.mark.asyncio
async def test_lifespan_startup_calls_setup_signal_handlers():
    """Test that lifespan startup calls setup_signal_handlers."""
    import tradeengine.api as api_module

    setup_was_called = []

    def track_setup():
        setup_was_called.append(True)

    mock_app = MagicMock()
    mock_app.state = MagicMock()

    with (
        patch("otel_init.configure_logging", return_value=True),
        patch.object(
            api_module.otel_init, "setup_signal_handlers", side_effect=track_setup
        ),
        patch("shared.constants.validate_mongodb_config"),
        patch("tradeengine.config_manager.TradingConfigManager") as MockConfig,
        patch.object(api_module, "binance_exchange") as mock_binance,
        patch.object(api_module, "simulator_exchange") as mock_sim,
        patch.object(api_module, "dispatcher") as mock_disp,
        patch("tradeengine.consumer.signal_consumer") as mock_consumer,
    ):

        mock_config = AsyncMock()
        mock_config.start = AsyncMock()
        mock_config.stop = AsyncMock()
        MockConfig.return_value = mock_config

        mock_binance.initialize = AsyncMock()
        mock_binance.close = AsyncMock()
        mock_sim.initialize = AsyncMock()
        mock_sim.close = AsyncMock()
        mock_disp.initialize = AsyncMock()
        mock_disp.close = AsyncMock()
        mock_consumer.initialize = AsyncMock(return_value=False)
        mock_consumer.running = False

        async with api_module.lifespan(mock_app):
            pass

        # Verify setup_signal_handlers was called
        assert len(setup_was_called) > 0


@pytest.mark.asyncio
async def test_lifespan_shutdown_calls_flush_telemetry():
    """Test that lifespan shutdown calls flush_telemetry and shutdown_telemetry."""
    import tradeengine.api as api_module

    flush_was_called = []
    shutdown_was_called = []

    def track_flush():
        flush_was_called.append(True)

    def track_shutdown():
        shutdown_was_called.append(True)

    mock_app = MagicMock()
    mock_app.state = MagicMock()

    with (
        patch("otel_init.configure_logging", return_value=True),
        patch.object(api_module.otel_init, "setup_signal_handlers"),
        patch.object(api_module.otel_init, "flush_telemetry", side_effect=track_flush),
        patch.object(
            api_module.otel_init, "shutdown_telemetry", side_effect=track_shutdown
        ),
        patch("shared.constants.validate_mongodb_config"),
        patch("tradeengine.config_manager.TradingConfigManager") as MockConfig,
        patch.object(api_module, "binance_exchange") as mock_binance,
        patch.object(api_module, "simulator_exchange") as mock_sim,
        patch.object(api_module, "dispatcher") as mock_disp,
        patch("tradeengine.consumer.signal_consumer") as mock_consumer,
    ):

        mock_config = AsyncMock()
        mock_config.start = AsyncMock()
        mock_config.stop = AsyncMock()
        MockConfig.return_value = mock_config

        mock_binance.initialize = AsyncMock()
        mock_binance.close = AsyncMock()
        mock_sim.initialize = AsyncMock()
        mock_sim.close = AsyncMock()
        mock_disp.initialize = AsyncMock()
        mock_disp.close = AsyncMock()
        mock_consumer.initialize = AsyncMock(return_value=False)
        mock_consumer.running = False

        async with api_module.lifespan(mock_app):
            pass

        # Verify flush_telemetry and shutdown_telemetry were called during shutdown
        assert len(flush_was_called) > 0
        assert len(shutdown_was_called) > 0


@pytest.mark.asyncio
async def test_lifespan_logs_configured_message():
    """
    Test that lifespan logs the success message.
    This provides coverage for the NEW log line in api.py patch.
    """
    import logging

    import tradeengine.api as api_module

    log_messages = []

    # Capture logs from api logger
    class LogCapture(logging.Handler):
        def emit(self, record):
            log_messages.append(self.format(record))

    handler = LogCapture()
    api_logger = logging.getLogger("tradeengine.api")
    api_logger.addHandler(handler)
    api_logger.setLevel(logging.INFO)

    mock_app = MagicMock()
    mock_app.state = MagicMock()

    try:
        with (
            patch("otel_init.configure_logging", return_value=True),
            patch("shared.constants.validate_mongodb_config"),
            patch("tradeengine.config_manager.TradingConfigManager") as MockConfig,
            patch.object(api_module, "binance_exchange") as mock_binance,
            patch.object(api_module, "simulator_exchange") as mock_sim,
            patch.object(api_module, "dispatcher") as mock_disp,
            patch("tradeengine.consumer.signal_consumer") as mock_consumer,
        ):

            mock_config = AsyncMock()
            mock_config.start = AsyncMock()
            mock_config.stop = AsyncMock()
            MockConfig.return_value = mock_config

            mock_binance.initialize = AsyncMock()
            mock_binance.close = AsyncMock()
            mock_sim.initialize = AsyncMock()
            mock_sim.close = AsyncMock()
            mock_disp.initialize = AsyncMock()
            mock_disp.close = AsyncMock()
            mock_consumer.initialize = AsyncMock(return_value=False)
            mock_consumer.running = False

            async with api_module.lifespan(mock_app):
                pass

        # Verify the new log message was emitted
        success_logs = [
            msg
            for msg in log_messages
            if "Logging configured" in msg and "no monitoring" in msg
        ]
        assert len(success_logs) > 0
    finally:
        api_logger.removeHandler(handler)


@pytest.mark.asyncio
async def test_lifespan_error_path_without_watchdog():
    """
    Test error handling path logs message without watchdog reference.
    This provides coverage for the MODIFIED error log line in api.py patch.
    """
    import logging

    import tradeengine.api as api_module

    error_messages = []

    class ErrorCapture(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.ERROR:
                error_messages.append(self.format(record))

    handler = ErrorCapture()
    api_logger = logging.getLogger("tradeengine.api")
    api_logger.addHandler(handler)
    api_logger.setLevel(logging.ERROR)

    mock_app = MagicMock()
    mock_app.state = MagicMock()

    try:
        with (
            patch("otel_init.configure_logging", return_value=True),
            patch(
                "shared.constants.validate_mongodb_config",
                side_effect=Exception("Test"),
            ),
            patch.object(api_module, "binance_exchange") as mock_binance,
            patch.object(api_module, "simulator_exchange") as mock_sim,
            patch.object(api_module, "dispatcher") as mock_disp,
        ):

            mock_binance.close = AsyncMock()
            mock_sim.close = AsyncMock()
            mock_disp.close = AsyncMock()

            async with api_module.lifespan(mock_app):
                pass

        # Verify error message without watchdog reference
        error_logs = [
            msg for msg in error_messages if "Service started with errors" in msg
        ]
        assert len(error_logs) > 0

        # Verify NO watchdog mention
        watchdog_logs = [msg for msg in error_messages if "watchdog" in msg.lower()]
        assert len(watchdog_logs) == 0
    finally:
        api_logger.removeHandler(handler)
