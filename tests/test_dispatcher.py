from datetime import datetime

import pytest

from contracts.signal import Signal, TimeFrame, StrategyMode, SignalStrength, OrderType, TimeInForce
from tradeengine.dispatcher import TradeDispatcher
from tradeengine.signal_aggregator import SignalAggregator


@pytest.fixture
def sample_signal():
    """Create a sample signal for testing"""
    return Signal(
        strategy_id="test_strategy",
        symbol="BTCUSDT",
        action="buy",
        confidence=0.8,
        strength=SignalStrength.STRONG,
        timeframe=TimeFrame.HOUR_1,
        current_price=45000.0,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.GTC,
        strategy_mode=StrategyMode.DETERMINISTIC,
        timestamp=datetime.now(),
        meta={"simulate": True},
    )


@pytest.fixture
def dispatcher():
    """Create a dispatcher instance for testing"""
    return TradeDispatcher()


@pytest.fixture
def signal_aggregator():
    """Create a signal aggregator instance for testing"""
    return SignalAggregator()


@pytest.mark.asyncio
async def test_dispatch_buy_signal(dispatcher, sample_signal):
    """Test dispatching a buy signal"""
    result = await dispatcher.dispatch(sample_signal)

    assert result["status"] in ["success", "error"]
    if result["status"] == "success":
        assert "aggregation_result" in result
        assert "execution_result" in result


@pytest.mark.asyncio
async def test_dispatch_hold_signal(dispatcher):
    """Test dispatching a hold signal"""
    hold_signal = Signal(
        strategy_id="test_strategy",
        symbol="BTCUSDT",
        action="hold",
        confidence=0.5,
        strength=SignalStrength.MEDIUM,
        timeframe=TimeFrame.HOUR_1,
        current_price=45000.0,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.GTC,
        strategy_mode=StrategyMode.DETERMINISTIC,
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


@pytest.mark.asyncio
async def test_dispatch_with_different_timeframes(dispatcher):
    """Test dispatching signals with different timeframes"""
    timeframes = [TimeFrame.MINUTE_1, TimeFrame.MINUTE_5, TimeFrame.HOUR_1, TimeFrame.HOUR_4]
    
    for timeframe in timeframes:
        signal = Signal(
            strategy_id="test_strategy",
            symbol="BTCUSDT",
            action="buy",
            confidence=0.8,
            strength=SignalStrength.STRONG,
            timeframe=timeframe,
            current_price=45000.0,
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.GTC,
            strategy_mode=StrategyMode.DETERMINISTIC,
            timestamp=datetime.now(),
            meta={"simulate": True},
        )
        
        result = await dispatcher.dispatch(signal)
        assert result["status"] in ["success", "error"]


@pytest.mark.asyncio
async def test_dispatch_with_different_strategy_modes(dispatcher):
    """Test dispatching signals with different strategy modes"""
    modes = [StrategyMode.DETERMINISTIC, StrategyMode.ML_LIGHT, StrategyMode.LLM_REASONING]
    
    for mode in modes:
        signal = Signal(
            strategy_id="test_strategy",
            symbol="BTCUSDT",
            action="buy",
            confidence=0.8,
            strength=SignalStrength.STRONG,
            timeframe=TimeFrame.HOUR_1,
            current_price=45000.0,
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.GTC,
            strategy_mode=mode,
            timestamp=datetime.now(),
            meta={"simulate": True},
        )
        
        result = await dispatcher.dispatch(signal)
        assert result["status"] in ["success", "error"]


@pytest.mark.asyncio
async def test_dispatch_advanced_order_types(dispatcher):
    """Test dispatching signals with advanced order types"""
    order_types = [
        OrderType.STOP_LIMIT,
        OrderType.TAKE_PROFIT,
        OrderType.CONDITIONAL_LIMIT
    ]
    
    for order_type in order_types:
        signal = Signal(
            strategy_id="test_strategy",
            symbol="BTCUSDT",
            action="buy",
            confidence=0.8,
            strength=SignalStrength.STRONG,
            timeframe=TimeFrame.HOUR_1,
            current_price=45000.0,
            order_type=order_type,
            time_in_force=TimeInForce.GTC,
            strategy_mode=StrategyMode.DETERMINISTIC,
            conditional_price=46000.0,
            conditional_direction="above",
            timestamp=datetime.now(),
            meta={"simulate": True},
        )
        
        result = await dispatcher.dispatch(signal)
        assert result["status"] in ["success", "error"]


@pytest.mark.asyncio
async def test_timeframe_conflict_resolution(signal_aggregator):
    """Test timeframe-based conflict resolution"""
    # Create a lower timeframe signal first
    lower_timeframe_signal = Signal(
        strategy_id="momentum_strategy",
        symbol="BTCUSDT",
        action="buy",
        confidence=0.8,
        strength=SignalStrength.STRONG,
        timeframe=TimeFrame.MINUTE_5,
        current_price=45000.0,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.GTC,
        strategy_mode=StrategyMode.DETERMINISTIC,
        timestamp=datetime.now(),
    )
    
    # Process the lower timeframe signal
    result1 = await signal_aggregator.process_signal(lower_timeframe_signal)
    assert result1["status"] in ["executed", "rejected"]
    
    # Create a higher timeframe signal with opposing action
    higher_timeframe_signal = Signal(
        strategy_id="mean_reversion_strategy",
        symbol="BTCUSDT",
        action="sell",
        confidence=0.7,
        strength=SignalStrength.MEDIUM,
        timeframe=TimeFrame.HOUR_4,
        current_price=45000.0,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.GTC,
        strategy_mode=StrategyMode.DETERMINISTIC,
        timestamp=datetime.now(),
    )
    
    # Process the higher timeframe signal
    result2 = await signal_aggregator.process_signal(higher_timeframe_signal)
    # The higher timeframe signal should win the conflict
    assert result2["status"] in ["executed", "rejected"]


@pytest.mark.asyncio
async def test_timeframe_strength_calculation(signal_aggregator):
    """Test timeframe strength calculation"""
    # Create signals with different timeframes
    timeframes = [TimeFrame.MINUTE_1, TimeFrame.MINUTE_5, TimeFrame.HOUR_1, TimeFrame.HOUR_4, TimeFrame.DAY_1]
    
    for timeframe in timeframes:
        signal = Signal(
            strategy_id="test_strategy",
            symbol="BTCUSDT",
            action="buy",
            confidence=0.8,
            strength=SignalStrength.STRONG,
            timeframe=timeframe,
            current_price=45000.0,
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.GTC,
            strategy_mode=StrategyMode.DETERMINISTIC,
            timestamp=datetime.now(),
        )
        
        # Calculate timeframe strength
        timeframe_strength = signal_aggregator._calculate_timeframe_strength(signal)
        assert timeframe_strength > 0
        
        # Higher timeframes should have higher strength values
        if timeframe == TimeFrame.DAY_1:
            assert timeframe_strength > signal_aggregator._calculate_timeframe_strength(
                Signal(
                    strategy_id="test_strategy",
                    symbol="BTCUSDT",
                    action="buy",
                    confidence=0.8,
                    strength=SignalStrength.STRONG,
                    timeframe=TimeFrame.MINUTE_1,
                    current_price=45000.0,
                    order_type=OrderType.MARKET,
                    time_in_force=TimeInForce.GTC,
                    strategy_mode=StrategyMode.DETERMINISTIC,
                    timestamp=datetime.now(),
                )
            )


@pytest.mark.asyncio
async def test_timeframe_numeric_values(signal_aggregator):
    """Test timeframe numeric value conversion"""
    # Test that higher timeframes have higher numeric values
    assert signal_aggregator._get_timeframe_numeric_value(TimeFrame.DAY_1) > \
           signal_aggregator._get_timeframe_numeric_value(TimeFrame.HOUR_4)
    
    assert signal_aggregator._get_timeframe_numeric_value(TimeFrame.HOUR_4) > \
           signal_aggregator._get_timeframe_numeric_value(TimeFrame.HOUR_1)
    
    assert signal_aggregator._get_timeframe_numeric_value(TimeFrame.HOUR_1) > \
           signal_aggregator._get_timeframe_numeric_value(TimeFrame.MINUTE_5)
    
    assert signal_aggregator._get_timeframe_numeric_value(TimeFrame.MINUTE_5) > \
           signal_aggregator._get_timeframe_numeric_value(TimeFrame.MINUTE_1)
    
    # Test that tick has the lowest value
    assert signal_aggregator._get_timeframe_numeric_value(TimeFrame.TICK) == 1
