from datetime import datetime

import pytest

from contracts.order import TradeOrder
from contracts.signal import Signal


def test_signal_creation():
    """Test Signal model creation and validation"""
    signal = Signal(
        strategy_id="test_strategy",
        symbol="BTCUSDT",
        action="buy",
        price=45000.0,
        confidence=0.85,
        timestamp=datetime.now(),
        meta={"test": True},
    )

    assert signal.strategy_id == "test_strategy"
    assert signal.symbol == "BTCUSDT"
    assert signal.action == "buy"
    assert signal.price == 45000.0
    assert signal.confidence == 0.85
    assert signal.meta == {"test": True}


def test_trade_order_creation():
    """Test TradeOrder model creation and validation"""
    order = TradeOrder(
        symbol="BTCUSDT", type="market", side="buy", amount=100.0, simulate=True
    )

    assert order.type == "market"
    assert order.side == "buy"
    assert order.amount == 100.0
    assert order.simulate is True
    assert order.target_price is None


def test_signal_invalid_action():
    """Test Signal validation with invalid action"""
    with pytest.raises(ValueError):
        Signal(
            strategy_id="test",
            symbol="BTCUSDT",
            action="invalid_action",  # Should fail validation
            price=45000.0,
            confidence=0.85,
            timestamp=datetime.now(),
            meta={},
        )
