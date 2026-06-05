"""
Regression tests for issue #355:
NATS consumer must NOT log 'executed' when Binance rejects the order (e.g. -2019 margin).
"""

import sys
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contracts.signal import Signal  # noqa: E402
from shared.constants import UTC  # noqa: E402
from tradeengine.consumer import SignalConsumer  # noqa: E402
from tradeengine.dispatcher import Dispatcher  # noqa: E402

_RECENT_TS = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
_RECENT_DT = datetime.now(UTC) - timedelta(seconds=5)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_signal(**overrides) -> Signal:
    defaults: dict = {
        "strategy_id": "test-strat",
        "symbol": "BTCUSDT",
        "signal_type": "buy",
        "action": "buy",
        "confidence": 0.85,
        "strength": "medium",
        "timeframe": "1h",
        "price": 50000.0,
        "quantity": 0.01,
        "current_price": 50000.0,
        "source": "petrosa-cio",
        "strategy": "test-strat",
        "timestamp": _RECENT_DT,
    }
    defaults.update(overrides)
    return Signal(**defaults)


# ---------------------------------------------------------------------------
# Dispatcher: status derivation from execution_result
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_returns_exchange_failed_on_binance_error() -> None:
    """When Binance rejects the order (status=error), dispatch must return exchange_failed."""
    dispatcher = Dispatcher()

    signal = _make_signal()
    binance_error_result = {
        "status": "error",
        "error": "APIError(code=-2019): Margin is insufficient.",
    }

    with (
        patch.object(
            dispatcher,
            "process_signal",
            new_callable=AsyncMock,
            return_value={"status": "success", "order_params": {"quantity": 0.01}},
        ),
        patch.object(
            dispatcher,
            "_execute_order_with_consensus",
            new_callable=AsyncMock,
            return_value=binance_error_result,
        ),
        patch("tradeengine.dispatcher.distributed_lock_manager") as mock_lock,
        patch.object(dispatcher, "heartbeat_monitor", None),
        patch.object(dispatcher.settings, "enforce_cio_audit", False),
    ):
        mock_lock.execute_with_lock = AsyncMock(return_value=binance_error_result)

        result = await dispatcher.dispatch(signal)

    assert result["status"] == "exchange_failed", (
        f"Expected 'exchange_failed', got '{result['status']}'. "
        "Logging 'executed' on a failed Binance order causes false alerts."
    )
    assert result["execution_result"]["status"] == "error"


@pytest.mark.asyncio
async def test_dispatch_returns_exchange_failed_on_binance_failed_status() -> None:
    """Binance _format_error_result returns status='failed' for API errors (-2019 etc).
    dispatch() must map 'failed' -> 'exchange_failed', not 'executed'."""
    dispatcher = Dispatcher()

    signal = _make_signal()
    # This is the real shape returned by binance.py::_format_error_result
    binance_error_result = {
        "status": "failed",
        "error": "APIError(code=-2019): Margin is insufficient.",
    }

    with (
        patch.object(
            dispatcher,
            "process_signal",
            new_callable=AsyncMock,
            return_value={"status": "success", "order_params": {"quantity": 0.01}},
        ),
        patch.object(
            dispatcher,
            "_execute_order_with_consensus",
            new_callable=AsyncMock,
            return_value=binance_error_result,
        ),
        patch("tradeengine.dispatcher.distributed_lock_manager") as mock_lock,
        patch.object(dispatcher, "heartbeat_monitor", None),
        patch.object(dispatcher.settings, "enforce_cio_audit", False),
    ):
        mock_lock.execute_with_lock = AsyncMock(return_value=binance_error_result)

        result = await dispatcher.dispatch(signal)

    assert result["status"] == "exchange_failed", (
        f"Expected 'exchange_failed', got '{result['status']}'. "
        "binance._format_error_result returns 'failed', which must not map to 'executed'."
    )
    assert result["execution_result"]["status"] == "failed"


@pytest.mark.asyncio
async def test_dispatch_returns_executed_on_binance_new() -> None:
    """When Binance accepts the order (status=NEW), dispatch must return executed."""
    dispatcher = Dispatcher()

    signal = _make_signal()
    binance_success_result = {"status": "NEW", "order_id": "12345"}

    with (
        patch.object(
            dispatcher,
            "process_signal",
            new_callable=AsyncMock,
            return_value={"status": "success", "order_params": {"quantity": 0.01}},
        ),
        patch.object(
            dispatcher,
            "_execute_order_with_consensus",
            new_callable=AsyncMock,
            return_value=binance_success_result,
        ),
        patch("tradeengine.dispatcher.distributed_lock_manager") as mock_lock,
        patch.object(dispatcher, "heartbeat_monitor", None),
        patch.object(dispatcher.settings, "enforce_cio_audit", False),
    ):
        mock_lock.execute_with_lock = AsyncMock(return_value=binance_success_result)

        result = await dispatcher.dispatch(signal)

    assert result["status"] == "executed"


@pytest.mark.asyncio
async def test_dispatch_returns_exchange_failed_on_rejected() -> None:
    """When Binance explicitly rejects an order (status=rejected), dispatch returns exchange_failed."""
    dispatcher = Dispatcher()

    signal = _make_signal()
    rejected_result = {"status": "rejected", "error": "Position side does not match."}

    with (
        patch.object(
            dispatcher,
            "process_signal",
            new_callable=AsyncMock,
            return_value={"status": "success", "order_params": {"quantity": 0.01}},
        ),
        patch.object(
            dispatcher,
            "_execute_order_with_consensus",
            new_callable=AsyncMock,
            return_value=rejected_result,
        ),
        patch("tradeengine.dispatcher.distributed_lock_manager") as mock_lock,
        patch.object(dispatcher, "heartbeat_monitor", None),
        patch.object(dispatcher.settings, "enforce_cio_audit", False),
    ):
        mock_lock.execute_with_lock = AsyncMock(return_value=rejected_result)

        result = await dispatcher.dispatch(signal)

    assert result["status"] == "exchange_failed"


# ---------------------------------------------------------------------------
# Consumer: log prefix reflects actual outcome
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consumer_logs_warning_prefix_on_exchange_failed() -> None:
    """Consumer must NOT use ✅ prefix when dispatcher returns exchange_failed."""
    consumer = SignalConsumer()

    mock_dispatcher = MagicMock()
    mock_dispatcher.dispatch = AsyncMock(
        return_value={
            "status": "exchange_failed",
            "execution_result": {"status": "error", "error": "Margin is insufficient"},
        }
    )
    consumer.dispatcher = mock_dispatcher

    import json

    signal_data = {
        "strategy_id": "test-strat",
        "symbol": "BTCUSDT",
        "signal_type": "buy",
        "action": "buy",
        "confidence": 0.85,
        "strength": "medium",
        "timeframe": "1h",
        "price": 50000.0,
        "quantity": 0.01,
        "current_price": 50000.0,
        "source": "petrosa-cio",
        "strategy": "test-strat",
        "timestamp": _RECENT_TS,
    }

    mock_msg = MagicMock()
    mock_msg.subject = "signals.trading.test"
    mock_msg.data = json.dumps(signal_data).encode()
    mock_msg.reply = None

    log_records: list = []

    import logging

    class CapturingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            log_records.append(self.format(record))

    handler = CapturingHandler()
    import tradeengine.consumer as consumer_module

    original_level = consumer_module.logger.level
    consumer_module.logger.setLevel(logging.INFO)
    consumer_module.logger.addHandler(handler)
    try:
        await consumer._message_handler(mock_msg)
    finally:
        consumer_module.logger.removeHandler(handler)
        consumer_module.logger.setLevel(original_level)

    processed_lines = [line for line in log_records if "NATS MESSAGE PROCESSED" in line]
    assert processed_lines, "Expected a NATS MESSAGE PROCESSED log line"
    assert any("⚠️" in line for line in processed_lines), (
        "Expected ⚠️ prefix for exchange_failed, but got: " + str(processed_lines)
    )
    assert not any("✅" in line for line in processed_lines), (
        "Must not log ✅ when Binance order failed: " + str(processed_lines)
    )


@pytest.mark.asyncio
async def test_consumer_logs_success_prefix_on_executed() -> None:
    """Consumer must use ✅ prefix only when dispatcher returns executed."""
    consumer = SignalConsumer()

    mock_dispatcher = MagicMock()
    mock_dispatcher.dispatch = AsyncMock(
        return_value={
            "status": "executed",
            "execution_result": {"status": "NEW", "order_id": "99999"},
        }
    )
    consumer.dispatcher = mock_dispatcher

    import json

    signal_data = {
        "strategy_id": "test-strat",
        "symbol": "BTCUSDT",
        "signal_type": "buy",
        "action": "buy",
        "confidence": 0.85,
        "strength": "medium",
        "timeframe": "1h",
        "price": 50000.0,
        "quantity": 0.01,
        "current_price": 50000.0,
        "source": "petrosa-cio",
        "strategy": "test-strat",
        "timestamp": _RECENT_TS,
    }

    mock_msg = MagicMock()
    mock_msg.subject = "signals.trading.test"
    mock_msg.data = json.dumps(signal_data).encode()
    mock_msg.reply = None

    log_records: list = []

    import logging

    class CapturingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            log_records.append(self.format(record))

    handler = CapturingHandler()
    import tradeengine.consumer as consumer_module

    original_level = consumer_module.logger.level
    consumer_module.logger.setLevel(logging.INFO)
    consumer_module.logger.addHandler(handler)
    try:
        await consumer._message_handler(mock_msg)
    finally:
        consumer_module.logger.removeHandler(handler)
        consumer_module.logger.setLevel(original_level)

    processed_lines = [line for line in log_records if "NATS MESSAGE PROCESSED" in line]
    assert processed_lines, "Expected a NATS MESSAGE PROCESSED log line"
    assert any("✅" in line for line in processed_lines), (
        "Expected ✅ prefix for executed status"
    )
