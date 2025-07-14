from unittest.mock import patch

import pytest

from contracts.order import OrderSide, OrderStatus, OrderType, TradeOrder
from contracts.signal import Signal
from tradeengine.dispatcher import Dispatcher


@pytest.fixture
def dispatcher() -> Dispatcher:
    return Dispatcher()


@pytest.fixture
def sample_signal() -> Signal:
    return Signal(
        strategy_id="test-strategy-1",
        symbol="BTCUSDT",
        action="buy",
        confidence=0.8,
        strength="medium",
        timeframe="1h",
        current_price=45000.0,
    )


@pytest.fixture
def sample_order() -> TradeOrder:
    return TradeOrder(
        symbol="BTCUSDT",
        order_type=OrderType.MARKET,
        side=OrderSide.BUY,
        quantity=0.1,
        price=45000.0,
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
    assert hasattr(dispatcher, "process_signals")


@pytest.mark.asyncio
async def test_process_signal_success(
    dispatcher: Dispatcher, sample_signal: Signal
) -> None:
    """Test successful signal processing"""
    with patch.object(dispatcher, "order_manager") as mock_order_manager:
        mock_order_manager.place_order.return_value = {
            "status": "executed",
            "order_id": "test-order-1",
        }

        result = await dispatcher.process_signal(sample_signal)
        assert result["status"] == "executed"
        assert result["order_id"] == "test-order-1"


@pytest.mark.asyncio
async def test_process_signal_error(
    dispatcher: Dispatcher, sample_signal: Signal
) -> None:
    """Test signal processing with error"""
    with patch.object(dispatcher, "order_manager") as mock_order_manager:
        mock_order_manager.place_order.side_effect = Exception("Test error")

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
            action="buy",
            confidence=0.8,
            strength="medium",
            timeframe="1h",
            current_price=45000.0,
        ),
        Signal(
            strategy_id="test-strategy-2",
            symbol="ETHUSDT",
            action="sell",
            confidence=0.7,
            strength="medium",
            timeframe="1h",
            current_price=3000.0,
        ),
    ]

    with patch.object(dispatcher, "order_manager") as mock_order_manager:
        mock_order_manager.place_order.side_effect = [
            {"status": "executed", "order_id": "test-order-1"},
            {"status": "rejected", "order_id": "test-order-2"},
        ]

        results = await dispatcher.process_signals(signals)
        assert len(results) == 2
        assert results[0]["status"] == "executed"
        assert results[1]["status"] == "rejected"


@pytest.mark.asyncio
async def test_validate_signal_valid(
    dispatcher: Dispatcher, sample_signal: Signal
) -> None:
    """Test signal validation with valid signal"""
    is_valid = dispatcher.validate_signal(sample_signal)
    assert is_valid is True


@pytest.mark.asyncio
async def test_validate_signal_invalid_confidence(dispatcher: Dispatcher) -> None:
    """Test signal validation with invalid confidence"""
    invalid_signal = Signal(
        strategy_id="test-strategy-1",
        symbol="BTCUSDT",
        action="buy",
        confidence=1.5,  # Invalid confidence > 1
        strength="medium",
        timeframe="1h",
        current_price=45000.0,
    )

    is_valid = dispatcher.validate_signal(invalid_signal)
    assert is_valid is False


@pytest.mark.asyncio
async def test_validate_signal_invalid_price(dispatcher: Dispatcher) -> None:
    """Test signal validation with invalid price"""
    invalid_signal = Signal(
        strategy_id="test-strategy-1",
        symbol="BTCUSDT",
        action="buy",
        confidence=0.8,
        strength="medium",
        timeframe="1h",
        current_price=-100.0,  # Invalid negative price
    )

    is_valid = dispatcher.validate_signal(invalid_signal)
    assert is_valid is False


@pytest.mark.asyncio
async def test_validate_signal_invalid_quantity(dispatcher: Dispatcher) -> None:
    """Test signal validation with invalid quantity"""
    invalid_signal = Signal(
        strategy_id="test-strategy-1",
        symbol="BTCUSDT",
        action="buy",
        confidence=0.8,
        strength="medium",
        timeframe="1h",
        current_price=45000.0,
        # Note: quantity is not a required field in the new model,
        # so this test may need to be rethought
    )

    is_valid = dispatcher.validate_signal(invalid_signal)
    assert is_valid is False


@pytest.mark.asyncio
async def test_create_order_from_signal(
    dispatcher: Dispatcher, sample_signal: Signal
) -> None:
    """Test order creation from signal"""
    order = dispatcher.create_order_from_signal(sample_signal)
    assert order.symbol == "BTCUSDT"
    assert order.order_type == OrderType.MARKET
    assert order.side == OrderSide.BUY
    assert order.quantity == 0.1
    assert order.price == 45000.0


@pytest.mark.asyncio
async def test_get_metrics(dispatcher: Dispatcher) -> None:
    """Test metrics retrieval"""
    metrics = dispatcher.get_metrics()
    assert isinstance(metrics, dict)
    assert "orders" in metrics
    assert "positions" in metrics
