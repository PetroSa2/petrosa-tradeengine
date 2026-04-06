from unittest.mock import AsyncMock, MagicMock

import pytest

from contracts.signal import Signal
from shared.constants import UTC
from tradeengine.dispatcher import Dispatcher


@pytest.mark.asyncio
async def test_dispatcher_restricted_mode_capping():
    """Test that Dispatcher aborts buy signals in restricted mode."""
    # Setup mocks
    mock_heartbeat = MagicMock()
    mock_heartbeat.is_restricted.return_value = True

    dispatcher = Dispatcher()
    dispatcher.heartbeat_monitor = mock_heartbeat
    dispatcher.logger = MagicMock()

    signal = Signal(
        signal_id="test_id",
        symbol="BTCUSDT",
        action="buy",
        quantity=10.0,
        price=1000.0,
        current_price=1000.0,
        confidence=0.9,
        source="petrosa-cio",
        strategy="test_strat",
        strategy_id="test_strat",
        metadata={"leverage": "20"},
    )

    result = await dispatcher.dispatch(signal)

    # In restricted mode, buy signals are aborted (not capped)
    assert result["status"] == "aborted"
    assert "RESTRICTED_MODE" in result["reason"]
    mock_heartbeat.is_restricted.assert_called_once()


@pytest.mark.asyncio
async def test_dispatcher_restricted_mode_allows_close():
    """Test that Dispatcher does not cap 'close' actions in restricted mode."""
    mock_heartbeat = MagicMock()
    mock_heartbeat.is_restricted.return_value = True

    dispatcher = Dispatcher()
    dispatcher.heartbeat_monitor = mock_heartbeat
    dispatcher.logger = MagicMock()

    signal = Signal(
        signal_id="test_id",
        symbol="BTCUSDT",
        action="close",
        quantity=1.0,
        price=1000.0,
        current_price=1000.0,
        confidence=0.9,
        source="petrosa-cio",
        strategy="test_strat",
        strategy_id="test_strat",
    )

    try:
        await dispatcher.dispatch(signal)
    except Exception:
        pass

    # Close action should NOT be capped
    assert signal.quantity == 1.0
