"""
Integration tests for api.py lifespan function.
Actually executes the lifespan to achieve patch coverage for api.py changes.
"""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

from shared.constants import UTC

# Mock OpenTelemetry imports before any imports that might trigger them
mock_logging_instrumentor = MagicMock()
mock_logging_module = MagicMock()
mock_logging_module.LoggingInstrumentor = mock_logging_instrumentor
sys.modules["opentelemetry.instrumentation.logging"] = mock_logging_module

mock_fastapi_instrumentor = MagicMock()
mock_fastapi_module = MagicMock()
mock_fastapi_module.FastAPIInstrumentor = mock_fastapi_instrumentor
sys.modules["opentelemetry.instrumentation.fastapi"] = mock_fastapi_module

sys.modules["opentelemetry.instrumentation.httpx"] = MagicMock()
sys.modules["opentelemetry.instrumentation.requests"] = MagicMock()
sys.modules["opentelemetry.instrumentation.urllib3"] = MagicMock()
sys.modules["opentelemetry.instrumentation.urllib"] = MagicMock()

# NEW: Mock otel_init which is expected by legacy tests
otel_init = MagicMock()
sys.modules["otel_init"] = otel_init

import pytest  # noqa: E402


@pytest.mark.asyncio
@patch.dict("os.environ", {"OTEL_NO_AUTO_INIT": ""}, clear=False)
async def test_lifespan_startup_calls_setup_telemetry():
    """
    Test that lifespan startup actually calls setup_telemetry().
    This provides coverage for the telemetry initialization in api.py.
    """
    # Import inside test to ensure fresh state
    import tradeengine.api as api_module
    import tradeengine.consumer as consumer_module

    setup_was_called = []

    def track_setup(**kwargs):
        setup_was_called.append(True)
        return True

    mock_app = MagicMock()
    mock_app.state = MagicMock()

    # Mock all heavy dependencies
    with (
        patch("tradeengine.api.setup_telemetry", side_effect=track_setup),
        patch("shared.constants.validate_mongodb_config"),
        patch("tradeengine.config_manager.TradingConfigManager") as MockConfig,
        patch.object(api_module, "binance_exchange") as mock_binance,
        patch.object(api_module, "simulator_exchange") as mock_sim,
        patch.object(api_module, "dispatcher") as mock_disp,
        patch.object(consumer_module, "signal_consumer") as mock_consumer,
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
        mock_consumer.start_consuming = AsyncMock()
        mock_consumer.stop_consuming = AsyncMock()

        # Execute lifespan
        async with api_module.lifespan(mock_app):
            pass

        # Verify setup_telemetry was called
        assert len(setup_was_called) > 0


@pytest.mark.asyncio
async def test_lifespan_shutdown_calls_flush_telemetry():
    """Test that lifespan shutdown calls flush_telemetry."""
    import tradeengine.api as api_module
    import tradeengine.consumer as consumer_module

    flush_was_called = []

    def track_flush():
        flush_was_called.append(True)

    mock_app = MagicMock()
    mock_app.state = MagicMock()

    with (
        patch("tradeengine.api.setup_telemetry", return_value=True),
        patch("tradeengine.api.flush_telemetry", side_effect=track_flush),
        patch("shared.constants.validate_mongodb_config"),
        patch("tradeengine.config_manager.TradingConfigManager") as MockConfig,
        patch.object(api_module, "binance_exchange") as mock_binance,
        patch.object(api_module, "simulator_exchange") as mock_sim,
        patch.object(api_module, "dispatcher") as mock_disp,
        patch.object(consumer_module, "signal_consumer") as mock_consumer,
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
        mock_consumer.start_consuming = AsyncMock()
        mock_consumer.stop_consuming = AsyncMock()

        async with api_module.lifespan(mock_app):
            pass

        # Verify flush_telemetry was called during shutdown
        assert len(flush_was_called) > 0


@pytest.mark.asyncio
@patch.dict("os.environ", {"OTEL_NO_AUTO_INIT": ""}, clear=False)
async def test_lifespan_logs_configured_message():
    """
    Test that lifespan logs the success message.
    This provides coverage for the NEW log line in api.py patch.
    """
    import logging

    import tradeengine.api as api_module
    import tradeengine.consumer as consumer_module  # Import to ensure module exists

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
            patch("tradeengine.api.setup_telemetry", return_value=True),
            patch("shared.constants.validate_mongodb_config"),
            patch("tradeengine.config_manager.TradingConfigManager") as MockConfig,
            patch.object(api_module, "binance_exchange") as mock_binance,
            patch.object(api_module, "simulator_exchange") as mock_sim,
            patch.object(api_module, "dispatcher") as mock_disp,
            patch.object(consumer_module, "signal_consumer") as mock_consumer,
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
            msg for msg in log_messages if "Telemetry initialized successfully" in msg
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
    import tradeengine.consumer as consumer_module  # Import to ensure module exists

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
            patch("tradeengine.api.setup_telemetry", return_value=True),
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


@pytest.mark.asyncio
async def test_lifespan_wires_exchange_truth_store_into_position_reconciler():
    """#592 regression guard: PositionReconciler MUST be constructed with
    `store=dispatcher.user_data_consumer.store` (not the default None).

    Before #592, this kwarg was silently omitted, so the AC1 (446-B) REST
    self-heal at the end of every reconcile_once() pass never ran — the
    ExchangeTruthStore (and therefore get_positions() when
    TE_EXCHANGE_TRUTH_STORE_ENABLED=on) only self-corrected on a WebSocket
    reconnect, leaving stale positions (ghost:LTCUSDT:LONG) undetected-stale
    for hours. This test fails again if the `store=` kwarg is ever dropped.
    """
    import tradeengine.api as api_module
    import tradeengine.consumer as consumer_module

    mock_app = MagicMock()
    mock_app.state = MagicMock()

    with (
        patch("tradeengine.api.setup_telemetry", return_value=True),
        patch("shared.constants.validate_mongodb_config"),
        patch.object(api_module, "TradingConfigManager") as MockConfig,
        patch.object(api_module, "binance_exchange") as mock_binance,
        patch.object(api_module, "simulator_exchange") as mock_sim,
        patch.object(api_module, "dispatcher") as mock_disp,
        patch.object(consumer_module, "signal_consumer") as mock_consumer,
        patch("tradeengine.position_reconciler.PositionReconciler") as MockReconciler,
        patch(
            "tradeengine.services.data_manager_boot_probe.DataManagerBootProbe.run",
            new=AsyncMock(return_value=MagicMock(success=True, failure_mode=None)),
        ),
    ):
        mock_config = AsyncMock()
        mock_config.start = AsyncMock()
        mock_config.stop = AsyncMock()
        MockConfig.return_value = mock_config

        mock_binance.initialize = AsyncMock()
        mock_binance.close = AsyncMock()
        mock_binance.get_account_info = AsyncMock(
            return_value={"assets": [], "can_trade": True}
        )
        mock_binance.get_symbol_price = AsyncMock(return_value=0.0)
        mock_binance.start_ping_loop = AsyncMock()
        mock_binance.stop_ping_loop = AsyncMock()
        mock_sim.initialize = AsyncMock()
        mock_sim.close = AsyncMock()
        mock_disp.initialize = AsyncMock()
        mock_disp.close = AsyncMock()
        mock_consumer.initialize = AsyncMock(return_value=False)
        mock_consumer.running = False
        mock_consumer.start_consuming = AsyncMock()
        mock_consumer.stop_consuming = AsyncMock()

        sentinel_store = MagicMock(name="exchange_truth_store")
        mock_disp.user_data_consumer.store = sentinel_store

        mock_reconciler_instance = MagicMock()
        mock_reconciler_instance.start = AsyncMock()
        MockReconciler.return_value = mock_reconciler_instance

        async with api_module.lifespan(mock_app):
            pass

        assert MockReconciler.called, "PositionReconciler was never constructed"
        _, kwargs = MockReconciler.call_args
        assert kwargs.get("store") is sentinel_store, (
            "PositionReconciler must be constructed with "
            "store=dispatcher.user_data_consumer.store (#592)"
        )
        assert kwargs.get("ghost_remediator") is not None, (
            "PositionReconciler must be constructed with a GhostPositionRemediator "
            "(#592)"
        )
        assert kwargs.get("stream_consumer") is mock_disp.user_data_consumer, (
            "PositionReconciler must be constructed with "
            "stream_consumer=dispatcher.user_data_consumer so a stale "
            "ExchangeTruthStore can actually force a WS reconnect instead "
            "of only logging (#609)"
        )
