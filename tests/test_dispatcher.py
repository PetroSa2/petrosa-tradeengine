from datetime import datetime

import pytest

from contracts.signal import Signal
from tradeengine.dispatcher import TradeDispatcher


@pytest.fixture
def sample_signal():
    """Create a sample signal for testing"""
    return Signal(
        strategy_id="test_strategy",
        symbol="BTCUSDT",
        action="buy",
        price=45000.0,
        confidence=0.8,
        timestamp=datetime.now(),
        meta={"simulate": True},
    )


@pytest.fixture
def dispatcher():
    """Create a dispatcher instance for testing"""
    return TradeDispatcher()


@pytest.mark.asyncio
async def test_dispatch_buy_signal(dispatcher, sample_signal):
    """Test dispatching a buy signal"""
    result = await dispatcher.dispatch(sample_signal)

    assert result["status"] in ["filled", "error"]
    if result["status"] == "filled":
        assert "order_id" in result
        assert result["simulated"] is True


@pytest.mark.asyncio
async def test_dispatch_hold_signal(dispatcher):
    """Test dispatching a hold signal"""
    hold_signal = Signal(
        strategy_id="test_strategy",
        symbol="BTCUSDT",
        action="hold",
        price=45000.0,
        confidence=0.5,
        timestamp=datetime.now(),
        meta={},
    )

    result = await dispatcher.dispatch(hold_signal)
    assert result["status"] == "hold"


@pytest.mark.asyncio
async def test_signal_to_order_conversion(dispatcher, sample_signal):
    """Test signal to order conversion"""
    order = dispatcher._signal_to_order(sample_signal)

    assert order.side == "buy"
    assert order.type == "market"
    assert order.amount > 0
    assert order.target_price == 45000.0
    assert order.simulate is True
