from datetime import datetime

import pytest

from contracts.order import TradeOrder
from contracts.signal import (
    OrderType,
    Signal,
    SignalStrength,
    StrategyMode,
    TimeFrame,
    TimeInForce,
)


def test_signal_creation():
    """Test Signal model creation and validation"""
    signal = Signal(
        strategy_id="test_strategy",
        symbol="BTCUSDT",
        action="buy",
        confidence=0.85,
        strength=SignalStrength.STRONG,
        timeframe=TimeFrame.HOUR_1,
        current_price=45000.0,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.GTC,
        strategy_mode=StrategyMode.DETERMINISTIC,
        timestamp=datetime.now(),
        meta={"test": True},
    )

    assert signal.strategy_id == "test_strategy"
    assert signal.symbol == "BTCUSDT"
    assert signal.action == "buy"
    assert signal.current_price == 45000.0
    assert signal.confidence == 0.85
    assert signal.timeframe == TimeFrame.HOUR_1
    assert signal.strategy_mode == StrategyMode.DETERMINISTIC
    assert signal.meta == {"test": True}


def test_signal_with_timeframe():
    """Test Signal model with different timeframes"""
    timeframes = [
        TimeFrame.MINUTE_1,
        TimeFrame.MINUTE_5,
        TimeFrame.HOUR_1,
        TimeFrame.HOUR_4,
        TimeFrame.DAY_1,
    ]

    for timeframe in timeframes:
        signal = Signal(
            strategy_id="test_strategy",
            symbol="BTCUSDT",
            action="buy",
            confidence=0.85,
            strength=SignalStrength.STRONG,
            timeframe=timeframe,
            current_price=45000.0,
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.GTC,
            strategy_mode=StrategyMode.DETERMINISTIC,
        )

        assert signal.timeframe == timeframe
        assert signal.timeframe.value in ["1m", "5m", "1h", "4h", "1d"]


def test_signal_with_strategy_modes():
    """Test Signal model with different strategy modes"""
    modes = [
        StrategyMode.DETERMINISTIC,
        StrategyMode.ML_LIGHT,
        StrategyMode.LLM_REASONING,
    ]

    for mode in modes:
        signal = Signal(
            strategy_id="test_strategy",
            symbol="BTCUSDT",
            action="buy",
            confidence=0.85,
            strength=SignalStrength.STRONG,
            timeframe=TimeFrame.HOUR_1,
            current_price=45000.0,
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.GTC,
            strategy_mode=mode,
        )

        assert signal.strategy_mode == mode


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
            confidence=0.85,
            strength=SignalStrength.STRONG,
            timeframe=TimeFrame.HOUR_1,
            current_price=45000.0,
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.GTC,
            strategy_mode=StrategyMode.DETERMINISTIC,
        )


def test_signal_invalid_confidence():
    """Test Signal validation with invalid confidence"""
    with pytest.raises(ValueError):
        Signal(
            strategy_id="test",
            symbol="BTCUSDT",
            action="buy",
            confidence=1.5,  # Should fail validation (> 1.0)
            strength=SignalStrength.STRONG,
            timeframe=TimeFrame.HOUR_1,
            current_price=45000.0,
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.GTC,
            strategy_mode=StrategyMode.DETERMINISTIC,
        )


def test_signal_with_advanced_features():
    """Test Signal model with advanced features"""
    signal = Signal(
        strategy_id="advanced_strategy",
        symbol="BTCUSDT",
        action="buy",
        confidence=0.9,
        strength=SignalStrength.EXTREME,
        timeframe=TimeFrame.HOUR_4,
        current_price=45000.0,
        target_price=46000.0,
        order_type=OrderType.STOP_LIMIT,
        time_in_force=TimeInForce.GTC,
        strategy_mode=StrategyMode.LLM_REASONING,
        position_size_pct=0.1,
        stop_loss=44000.0,
        take_profit=47000.0,
        conditional_price=45500.0,
        conditional_direction="above",
        model_confidence=0.85,
        llm_reasoning="Comprehensive analysis indicates strong buy signal",
        indicators={"rsi": 65, "macd": "bullish"},
        rationale="Advanced signal with comprehensive analysis",
        meta={"strategy_type": "advanced", "timeframe": "4h", "sentiment_score": 0.8},
    )

    assert signal.strategy_mode == StrategyMode.LLM_REASONING
    assert signal.timeframe == TimeFrame.HOUR_4
    assert signal.strength == SignalStrength.EXTREME
    assert signal.order_type == OrderType.STOP_LIMIT
    assert signal.conditional_price == 45500.0
    assert signal.conditional_direction == "above"
    assert signal.llm_reasoning is not None
    assert signal.indicators is not None
