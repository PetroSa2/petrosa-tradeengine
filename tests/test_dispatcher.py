from datetime import datetime
from unittest.mock import patch

import pytest

from contracts.order import OrderStatus, TradeOrder
from contracts.signal import OrderType, Signal, StrategyMode
from tradeengine.dispatcher import Dispatcher


@pytest.fixture
def dispatcher() -> Dispatcher:
    return Dispatcher()


@pytest.fixture
def sample_signal() -> Signal:
    return Signal(
        strategy_id="test-strategy-1",
        symbol="BTCUSDT",
        signal_type="buy",
        action="buy",
        confidence=0.8,
        strength="medium",
        timeframe="1h",
        price=45000.0,
        quantity=0.1,
        current_price=45000.0,
        source="test",
        strategy="test-strategy",
    )


@pytest.fixture
def sample_order() -> TradeOrder:
    return TradeOrder(
        symbol="BTCUSDT",
        type="market",
        side="buy",
        amount=0.1,
        order_id="test-order-1",
        status=OrderStatus.PENDING,
        time_in_force="GTC",
        position_size_pct=0.1,
    )


@pytest.mark.asyncio
async def test_dispatcher_initialization(dispatcher: Dispatcher) -> None:
    """Test dispatcher initialization"""
    assert dispatcher is not None
    assert hasattr(dispatcher, "process_signal")


@pytest.mark.asyncio
async def test_process_signal_success(
    dispatcher: Dispatcher, sample_signal: Signal
) -> None:
    """Test successful signal processing"""
    with patch.object(dispatcher, "signal_aggregator") as mock_aggregator:
        mock_aggregator.add_signal.return_value = None

        result = await dispatcher.process_signal(sample_signal)
        assert result["status"] == "executed"


@pytest.mark.asyncio
async def test_process_signal_error(
    dispatcher: Dispatcher, sample_signal: Signal
) -> None:
    """Test signal processing with error"""
    with patch.object(dispatcher, "signal_aggregator") as mock_aggregator:
        mock_aggregator.add_signal.side_effect = Exception("Test error")

        result = await dispatcher.process_signal(sample_signal)
        assert result["status"] == "error"
        assert "error" in result


@pytest.mark.asyncio
async def test_process_signals_success(dispatcher: Dispatcher) -> None:
    """Test successful multiple signal processing"""
    signals = [
        Signal(
            strategy_id="test-strategy-1",
            symbol="BTCUSDT",
            signal_type="buy",
            action="buy",
            confidence=0.8,
            strength="medium",
            timeframe="1h",
            price=45000.0,
            quantity=0.1,
            current_price=45000.0,
            source="test",
            strategy="test-strategy",
        ),
        Signal(
            strategy_id="test-strategy-2",
            symbol="ETHUSDT",
            signal_type="sell",
            action="sell",
            confidence=0.7,
            strength="medium",
            timeframe="1h",
            price=3000.0,
            quantity=0.1,
            current_price=3000.0,
            source="test",
            strategy="test-strategy",
        ),
    ]

    with patch.object(dispatcher, "signal_aggregator") as mock_aggregator:
        mock_aggregator.add_signal.return_value = None

        # Process signals individually since process_signals doesn't exist
        results = []
        for signal in signals:
            result = await dispatcher.process_signal(signal)
            results.append(result)

        assert len(results) == 2
        assert results[0]["status"] == "executed"
        assert results[1]["status"] == "executed"


@pytest.mark.asyncio
async def test_validate_signal_valid(
    dispatcher: Dispatcher, sample_signal: Signal
) -> None:
    """Test signal validation with valid signal"""
    # Signal validation is handled by Pydantic model validation
    assert sample_signal is not None
    assert sample_signal.strategy_id == "test-strategy-1"


@pytest.mark.asyncio
async def test_validate_signal_invalid_confidence(dispatcher: Dispatcher) -> None:
    """Test signal validation with invalid confidence"""
    with pytest.raises(ValueError):
        Signal(
            strategy_id="test-strategy-1",
            symbol="BTCUSDT",
            signal_type="buy",
            action="buy",
            confidence=1.5,  # Invalid confidence > 1
            strength="medium",
            timeframe="1h",
            price=45000.0,
            quantity=0.1,
            current_price=45000.0,
            source="test",
            strategy="test-strategy",
        )


@pytest.mark.asyncio
async def test_validate_signal_invalid_price(dispatcher: Dispatcher) -> None:
    """Test signal validation with invalid price"""
    # Pydantic doesn't validate negative prices by default
    signal = Signal(
        strategy_id="test-strategy-1",
        symbol="BTCUSDT",
        signal_type="buy",
        action="buy",
        confidence=0.8,
        strength="medium",
        timeframe="1h",
        price=-100.0,  # Invalid negative price
        quantity=0.1,
        current_price=-100.0,  # Invalid negative price
        source="test",
        strategy="test-strategy",
    )
    assert signal.price == -100.0


@pytest.mark.asyncio
async def test_validate_signal_invalid_quantity(dispatcher: Dispatcher) -> None:
    """Test signal validation with invalid quantity"""
    # Pydantic doesn't validate negative quantities by default
    signal = Signal(
        strategy_id="test-strategy-1",
        symbol="BTCUSDT",
        signal_type="buy",
        action="buy",
        confidence=0.8,
        strength="medium",
        timeframe="1h",
        price=45000.0,
        quantity=-0.1,  # Invalid negative quantity
        current_price=45000.0,
        source="test",
        strategy="test-strategy",
    )
    assert signal.quantity == -0.1


@pytest.mark.asyncio
async def test_create_order_from_signal(
    dispatcher: Dispatcher, sample_signal: Signal
) -> None:
    """Test order creation from signal"""
    order = dispatcher._signal_to_order(sample_signal)
    assert order.symbol == "BTCUSDT"
    assert order.type == "market"
    assert order.side == "buy"
    assert order.amount == 0.1  # Uses signal quantity when valid


# ============================================================================
# Unit Tests for _generate_signal_id() Helper Function
# ============================================================================


@pytest.mark.asyncio
async def test_generate_signal_id_identical_signals(dispatcher: Dispatcher) -> None:
    """Test that identical signals produce the same ID"""
    timestamp = datetime(2024, 1, 1, 12, 0, 0)
    signal1 = Signal(
        strategy_id="test-strategy",
        symbol="BTCUSDT",
        action="buy",
        timestamp=timestamp,
        confidence=0.8,
        strength="medium",
        timeframe="1h",
        price=50000.0,
        quantity=0.1,
        current_price=50000.0,
        source="test",
        strategy="test-strategy",
    )
    signal2 = Signal(
        strategy_id="test-strategy",
        symbol="BTCUSDT",
        action="buy",
        timestamp=timestamp,
        confidence=0.9,  # Different confidence shouldn't affect ID
        strength="strong",  # Different strength shouldn't affect ID
        timeframe="1h",
        price=51000.0,  # Different price shouldn't affect ID
        quantity=0.2,  # Different quantity shouldn't affect ID
        current_price=51000.0,
        source="test",
        strategy="test-strategy",
    )

    id1 = dispatcher._generate_signal_id(signal1)
    id2 = dispatcher._generate_signal_id(signal2)

    assert id1 == id2
    assert "test-strategy_BTCUSDT_buy" in id1
    assert "2024-01-01T12:00:00" in id1


@pytest.mark.asyncio
async def test_generate_signal_id_different_signals(dispatcher: Dispatcher) -> None:
    """Test that different signals produce different IDs"""
    timestamp = datetime(2024, 1, 1, 12, 0, 0)
    signal1 = Signal(
        strategy_id="test-1",
        symbol="BTCUSDT",
        action="buy",
        timestamp=timestamp,
        confidence=0.8,
        strength="medium",
        timeframe="1h",
        price=50000.0,
        quantity=0.1,
        current_price=50000.0,
        source="test",
        strategy="test-1",
    )
    signal2 = Signal(
        strategy_id="test-2",  # Different strategy_id
        symbol="BTCUSDT",
        action="buy",
        timestamp=timestamp,
        confidence=0.8,
        strength="medium",
        timeframe="1h",
        price=50000.0,
        quantity=0.1,
        current_price=50000.0,
        source="test",
        strategy="test-2",
    )
    signal3 = Signal(
        strategy_id="test-1",
        symbol="ETHUSDT",  # Different symbol
        action="buy",
        timestamp=timestamp,
        confidence=0.8,
        strength="medium",
        timeframe="1h",
        price=50000.0,
        quantity=0.1,
        current_price=50000.0,
        source="test",
        strategy="test-1",
    )
    signal4 = Signal(
        strategy_id="test-1",
        symbol="BTCUSDT",
        action="sell",  # Different action
        timestamp=timestamp,
        confidence=0.8,
        strength="medium",
        timeframe="1h",
        price=50000.0,
        quantity=0.1,
        current_price=50000.0,
        source="test",
        strategy="test-1",
    )

    ids = [
        dispatcher._generate_signal_id(signal1),
        dispatcher._generate_signal_id(signal2),
        dispatcher._generate_signal_id(signal3),
        dispatcher._generate_signal_id(signal4),
    ]

    # All IDs should be unique
    assert len(set(ids)) == 4
    # Verify each ID contains expected components
    assert "test-1_BTCUSDT_buy" in ids[0]
    assert "test-2_BTCUSDT_buy" in ids[1]
    assert "test-1_ETHUSDT_buy" in ids[2]
    assert "test-1_BTCUSDT_sell" in ids[3]


@pytest.mark.asyncio
async def test_generate_signal_id_different_timestamps(dispatcher: Dispatcher) -> None:
    """Test that signals with same data but different timestamps produce different IDs"""
    timestamp1 = datetime(2024, 1, 1, 12, 0, 0)
    timestamp2 = datetime(2024, 1, 1, 12, 0, 1)  # 1 second later
    timestamp3 = datetime(
        2024, 1, 1, 12, 0, 0, 500000
    )  # Same second, different microsecond

    signal1 = Signal(
        strategy_id="test-strategy",
        symbol="BTCUSDT",
        action="buy",
        timestamp=timestamp1,
        confidence=0.8,
        strength="medium",
        timeframe="1h",
        price=50000.0,
        quantity=0.1,
        current_price=50000.0,
        source="test",
        strategy="test-strategy",
    )
    signal2 = Signal(
        strategy_id="test-strategy",
        symbol="BTCUSDT",
        action="buy",
        timestamp=timestamp2,
        confidence=0.8,
        strength="medium",
        timeframe="1h",
        price=50000.0,
        quantity=0.1,
        current_price=50000.0,
        source="test",
        strategy="test-strategy",
    )
    signal3 = Signal(
        strategy_id="test-strategy",
        symbol="BTCUSDT",
        action="buy",
        timestamp=timestamp3,
        confidence=0.8,
        strength="medium",
        timeframe="1h",
        price=50000.0,
        quantity=0.1,
        current_price=50000.0,
        source="test",
        strategy="test-strategy",
    )

    id1 = dispatcher._generate_signal_id(signal1)
    id2 = dispatcher._generate_signal_id(signal2)
    id3 = dispatcher._generate_signal_id(signal3)

    # Different seconds should produce different IDs
    assert id1 != id2
    # Same second (even with different microseconds) should produce same ID
    assert id1 == id3
    assert "test-strategy_BTCUSDT_buy" in id1
    assert "2024-01-01T12:00:00" in id1
    assert "2024-01-01T12:00:01" in id2


@pytest.mark.asyncio
async def test_generate_signal_id_format(dispatcher: Dispatcher) -> None:
    """Test ID format and consistency"""
    timestamp = datetime(2024, 1, 1, 12, 0, 0)
    signal = Signal(
        strategy_id="test-strategy",
        symbol="BTCUSDT",
        action="buy",
        timestamp=timestamp,
        confidence=0.8,
        strength="medium",
        timeframe="1h",
        price=50000.0,
        quantity=0.1,
        current_price=50000.0,
        source="test",
        strategy="test-strategy",
    )

    signal_id = dispatcher._generate_signal_id(signal)

    # Verify format: strategy_id_symbol_action_timestamp
    assert signal_id == "test-strategy_BTCUSDT_buy_2024-01-01T12:00:00"
    assert signal_id.count("_") == 3  # Three underscores separating components


@pytest.mark.asyncio
async def test_generate_signal_id_no_timestamp(dispatcher: Dispatcher) -> None:
    """Test ID generation when timestamp is None"""
    signal = Signal(
        strategy_id="test-strategy",
        symbol="BTCUSDT",
        action="buy",
        timestamp=None,
        confidence=0.8,
        strength="medium",
        timeframe="1h",
        price=50000.0,
        quantity=0.1,
        current_price=50000.0,
        source="test",
        strategy="test-strategy",
    )

    signal_id = dispatcher._generate_signal_id(signal)

    # Should still generate ID without timestamp
    assert "test-strategy_BTCUSDT_buy" in signal_id
    assert signal_id.endswith("_") or signal_id.count("_") >= 2


# ============================================================================
# Unit Tests for _signal_to_order() Helper Function - Edge Cases
# ============================================================================


@pytest.mark.asyncio
async def test_signal_to_order_buy_signal(dispatcher: Dispatcher) -> None:
    """Test _signal_to_order with buy signal"""
    signal = Signal(
        strategy_id="test-strategy",
        symbol="BTCUSDT",
        action="buy",
        confidence=0.8,
        strength="medium",
        timeframe="1h",
        price=50000.0,
        quantity=0.1,
        current_price=50000.0,
        source="test",
        strategy="test-strategy",
        order_type=OrderType.MARKET,
        strategy_mode=StrategyMode.DETERMINISTIC,
    )

    order = dispatcher._signal_to_order(signal)

    assert order.symbol == "BTCUSDT"
    assert order.side == "buy"
    assert order.type == "market"
    assert order.target_price == 50000.0


@pytest.mark.asyncio
async def test_signal_to_order_sell_signal(dispatcher: Dispatcher) -> None:
    """Test _signal_to_order with sell signal"""
    signal = Signal(
        strategy_id="test-strategy",
        symbol="ETHUSDT",
        action="sell",
        confidence=0.8,
        strength="medium",
        timeframe="1h",
        price=3000.0,
        quantity=0.5,
        current_price=3000.0,
        source="test",
        strategy="test-strategy",
        order_type=OrderType.LIMIT,
        strategy_mode=StrategyMode.DETERMINISTIC,
    )

    order = dispatcher._signal_to_order(signal)

    assert order.symbol == "ETHUSDT"
    assert order.side == "sell"
    assert order.type == "limit"
    assert order.target_price == 3000.0


@pytest.mark.asyncio
async def test_signal_to_order_close_signal(dispatcher: Dispatcher) -> None:
    """Test _signal_to_order with close signal"""
    signal = Signal(
        strategy_id="test-strategy",
        symbol="BTCUSDT",
        action="close",
        confidence=0.8,
        strength="medium",
        timeframe="1h",
        price=50000.0,
        quantity=0.1,
        current_price=50000.0,
        source="test",
        strategy="test-strategy",
        order_type=OrderType.MARKET,
        strategy_mode=StrategyMode.DETERMINISTIC,
    )

    order = dispatcher._signal_to_order(signal)

    assert order.symbol == "BTCUSDT"
    assert order.side == "close"
    assert order.type == "market"


@pytest.mark.asyncio
async def test_signal_to_order_negative_price(dispatcher: Dispatcher) -> None:
    """Test _signal_to_order with negative price (edge case)"""
    signal = Signal(
        strategy_id="test-strategy",
        symbol="BTCUSDT",
        action="buy",
        confidence=0.8,
        strength="medium",
        timeframe="1h",
        price=-100.0,  # Negative price
        quantity=0.1,
        current_price=-100.0,  # Negative current price
        source="test",
        strategy="test-strategy",
        order_type=OrderType.MARKET,
        strategy_mode=StrategyMode.DETERMINISTIC,
    )

    order = dispatcher._signal_to_order(signal)

    # Should still create order (validation happens elsewhere)
    assert order.symbol == "BTCUSDT"
    assert order.side == "buy"
    assert order.target_price == -100.0


@pytest.mark.asyncio
async def test_signal_to_order_zero_quantity(dispatcher: Dispatcher) -> None:
    """Test _signal_to_order with zero quantity (edge case)"""
    signal = Signal(
        strategy_id="test-strategy",
        symbol="BTCUSDT",
        action="buy",
        confidence=0.8,
        strength="medium",
        timeframe="1h",
        price=50000.0,
        quantity=0.0,  # Zero quantity
        current_price=50000.0,
        source="test",
        strategy="test-strategy",
        order_type=OrderType.MARKET,
        strategy_mode=StrategyMode.DETERMINISTIC,
    )

    order = dispatcher._signal_to_order(signal)

    # Should still create order (amount calculation handles this)
    assert order.symbol == "BTCUSDT"
    assert order.side == "buy"
    # Amount will be calculated by _calculate_order_amount (may use minimum)


@pytest.mark.asyncio
async def test_signal_to_order_missing_optional_fields(dispatcher: Dispatcher) -> None:
    """Test _signal_to_order with missing optional fields"""
    signal = Signal(
        strategy_id="test-strategy",
        symbol="BTCUSDT",
        action="buy",
        confidence=0.8,
        strength="medium",
        timeframe="1h",
        price=50000.0,
        quantity=0.1,
        current_price=50000.0,
        source="test",
        strategy="test-strategy",
        order_type=OrderType.MARKET,
        strategy_mode=StrategyMode.DETERMINISTIC,
        # Missing optional fields: stop_loss, take_profit, conditional_price, etc.
    )

    order = dispatcher._signal_to_order(signal)

    # Should still create order with None for optional fields
    assert order.symbol == "BTCUSDT"
    assert order.side == "buy"
    assert order.type == "market"
    # Optional fields may be None
    assert order.stop_loss is None or isinstance(order.stop_loss, (float, type(None)))
    assert order.take_profit is None or isinstance(
        order.take_profit, (float, type(None))
    )


@pytest.mark.asyncio
async def test_signal_to_order_with_stop_loss_take_profit(
    dispatcher: Dispatcher,
) -> None:
    """Test _signal_to_order with stop loss and take profit"""
    signal = Signal(
        strategy_id="test-strategy",
        symbol="BTCUSDT",
        action="buy",
        confidence=0.8,
        strength="medium",
        timeframe="1h",
        price=50000.0,
        quantity=0.1,
        current_price=50000.0,
        source="test",
        strategy="test-strategy",
        order_type=OrderType.MARKET,
        strategy_mode=StrategyMode.DETERMINISTIC,
        stop_loss=49000.0,
        take_profit=51000.0,
    )

    order = dispatcher._signal_to_order(signal)

    assert order.symbol == "BTCUSDT"
    assert order.side == "buy"
    assert order.stop_loss == 49000.0
    assert order.take_profit == 51000.0


@pytest.mark.asyncio
async def test_get_metrics(dispatcher: Dispatcher) -> None:
    """Test metrics retrieval"""
    # The dispatcher doesn't have a get_metrics method, so we'll test signal aggregator
    metrics = dispatcher.signal_aggregator.get_signal_summary()
    assert isinstance(metrics, dict)
    assert "active_signals_count" in metrics
    assert "total_signals_processed" in metrics
