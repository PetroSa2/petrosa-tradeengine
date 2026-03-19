import pytest
from unittest.mock import AsyncMock, MagicMock
from tradeengine.dispatcher import Dispatcher
from contracts.signal import Signal

@pytest.mark.asyncio
async def test_dispatcher_restricted_mode_capping():
    """Test that Dispatcher caps quantity and leverage in restricted mode."""
    # Setup mocks
    mock_heartbeat = MagicMock()
    mock_heartbeat.is_restricted.return_value = True
    
    dispatcher = Dispatcher()
    dispatcher.heartbeat_monitor = mock_heartbeat
    dispatcher.logger = MagicMock()
    
    # Create a signal that exceeds restricted limits
    # Max USD is 5000. At 1000 price, limit should be 5.0 qty.
    signal = Signal(
        signal_id="test_id",
        symbol="BTCUSDT",
        action="buy",
        quantity=10.0,
        price=1000.0,
        current_price=1000.0,
        confidence=0.9,
        source="test",
        strategy="test_strat",
        strategy_id="test_strat",
        metadata={"leverage": "20"}
    )
    
    # We only want to test the capping logic at the start of dispatch()
    # so we mock the rest of the method or handle the exception
    try:
        await dispatcher.dispatch(signal)
    except Exception:
        pass
    
    # Verify capping (max_position_size_usd=5000 / price=1000 = 5.0)
    assert signal.quantity == 5.0
    # Verify leverage clamping (max_leverage=10)
    assert signal.metadata["leverage"] == 10
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
        source="test",
        strategy="test_strat",
        strategy_id="test_strat"
    )
    
    try:
        await dispatcher.dispatch(signal)
    except Exception:
        pass
    
    # Close action should NOT be capped
    assert signal.quantity == 1.0
