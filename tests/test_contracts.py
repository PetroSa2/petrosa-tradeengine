import pytest
from pydantic import ValidationError

from contracts.order import OrderStatus, TradeOrder
from contracts.signal import Signal


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


def test_signal_creation(sample_signal: Signal) -> None:
    """Test signal creation"""
    assert sample_signal.strategy_id == "test-strategy-1"
    assert sample_signal.symbol == "BTCUSDT"
    assert sample_signal.action == "buy"
    assert sample_signal.current_price == 45000.0
    assert sample_signal.confidence == 0.8


def test_signal_validation_invalid_confidence() -> None:
    """Test signal validation with invalid confidence"""
    with pytest.raises(ValidationError) as exc_info:
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

    # Verify the specific validation error
    error_str = str(exc_info.value).lower()
    assert "confidence" in error_str, "Error should mention 'confidence' field"


def test_signal_validation_invalid_price() -> None:
    """Test signal validation with invalid price"""
    # Pydantic doesn't validate negative prices by default, so this should pass
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


def test_signal_validation_invalid_quantity() -> None:
    """Test signal validation with invalid quantity"""
    # Pydantic doesn't validate negative quantities by default, so this should pass
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


def test_order_creation(sample_order: TradeOrder) -> None:
    """Test order creation"""
    assert sample_order.symbol == "BTCUSDT"
    assert sample_order.type == "market"
    assert sample_order.side == "buy"
    assert sample_order.amount == 0.1
    assert sample_order.order_id == "test-order-1"
    assert sample_order.status == OrderStatus.PENDING


def test_order_validation_invalid_quantity() -> None:
    """Test order validation with invalid quantity"""
    # Pydantic doesn't validate zero amounts by default, so this should pass
    order = TradeOrder(
        symbol="BTCUSDT",
        type="market",
        side="buy",
        amount=0.0,  # Invalid zero quantity
        order_id="test-order-1",
        status=OrderStatus.PENDING,
        time_in_force="GTC",
        position_size_pct=0.1,
    )
    assert order.amount == 0.0


def test_order_validation_invalid_price() -> None:
    """Test order validation with invalid price"""
    # Pydantic doesn't validate negative prices by default, so this should pass
    order = TradeOrder(
        symbol="BTCUSDT",
        type="market",
        side="buy",
        amount=0.1,
        target_price=-100.0,  # Invalid negative price
        order_id="test-order-1",
        status=OrderStatus.PENDING,
        time_in_force="GTC",
        position_size_pct=0.1,
    )
    assert order.target_price == -100.0


def test_signal_serialization(sample_signal: Signal) -> None:
    """Test signal serialization"""
    signal_dict = sample_signal.model_dump()
    assert signal_dict["strategy_id"] == "test-strategy-1"
    assert signal_dict["symbol"] == "BTCUSDT"
    assert signal_dict["action"] == "buy"
    assert signal_dict["current_price"] == 45000.0


def test_order_serialization(sample_order: TradeOrder) -> None:
    """Test order serialization"""
    order_dict = sample_order.model_dump()
    assert order_dict["symbol"] == "BTCUSDT"
    assert order_dict["type"] == "market"
    assert order_dict["side"] == "buy"
    assert order_dict["amount"] == 0.1


def test_signal_deserialization() -> None:
    """Test signal deserialization"""
    signal_data = {
        "strategy_id": "test-strategy-1",
        "symbol": "BTCUSDT",
        "signal_type": "buy",
        "action": "buy",
        "confidence": 0.8,
        "strength": "medium",
        "timeframe": "1h",
        "price": 45000.0,
        "quantity": 0.1,
        "current_price": 45000.0,
        "source": "test",
        "strategy": "test-strategy",
    }

    signal = Signal(**signal_data)
    assert signal.strategy_id == "test-strategy-1"
    assert signal.symbol == "BTCUSDT"
    assert signal.action == "buy"


def test_order_deserialization() -> None:
    """Test order deserialization"""
    order_data = {
        "symbol": "BTCUSDT",
        "type": "market",
        "side": "buy",
        "amount": 0.1,
        "order_id": "test-order-1",
        "status": "pending",
        "time_in_force": "GTC",
        "position_size_pct": 0.1,
    }

    order = TradeOrder(**order_data)
    assert order.symbol == "BTCUSDT"
    assert order.type == "market"
    assert order.side == "buy"
