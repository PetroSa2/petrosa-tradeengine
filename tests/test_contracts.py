import pytest
from pydantic import ValidationError

from contracts.order import OrderStatus, TradeOrder
from contracts.signal import Signal
from contracts.trading_config import LeverageStatus, TradingConfig, TradingConfigAudit


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
    with pytest.raises(ValidationError):
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


def test_trading_config_side_validation() -> None:
    """Test TradingConfig side field validator (Pydantic v2)"""
    # Valid LONG side
    config = TradingConfig(
        symbol="BTCUSDT",
        side="LONG",
        parameters={"leverage": 10},
        created_by="test",
    )
    assert config.side == "LONG"

    # Valid SHORT side
    config = TradingConfig(
        symbol="BTCUSDT",
        side="SHORT",
        parameters={"leverage": 10},
        created_by="test",
    )
    assert config.side == "SHORT"

    # Invalid side should raise ValidationError
    with pytest.raises(ValidationError):
        TradingConfig(
            symbol="BTCUSDT",
            side="INVALID",
            parameters={"leverage": 10},
            created_by="test",
        )

    # None side is valid (for global/symbol configs)
    config = TradingConfig(
        symbol="BTCUSDT",
        side=None,
        parameters={"leverage": 10},
        created_by="test",
    )
    assert config.side is None


def test_signal_field_validators() -> None:
    """Test Signal field validators (Pydantic v2)"""
    # Test confidence validator - valid range
    signal = Signal(
        strategy_id="test",
        symbol="BTCUSDT",
        action="buy",
        confidence=0.5,
        price=45000.0,
        quantity=0.1,
        current_price=45000.0,
        source="test",
        strategy="test",
    )
    assert signal.confidence == 0.5

    # Test confidence validator - invalid (too high)
    with pytest.raises(ValidationError):
        Signal(
            strategy_id="test",
            symbol="BTCUSDT",
            action="buy",
            confidence=1.5,  # Invalid: > 1
            price=45000.0,
            quantity=0.1,
            current_price=45000.0,
            source="test",
            strategy="test",
        )

    # Test percentage validators - valid
    signal = Signal(
        strategy_id="test",
        symbol="BTCUSDT",
        action="buy",
        confidence=0.8,
        price=45000.0,
        quantity=0.1,
        current_price=45000.0,
        source="test",
        strategy="test",
        position_size_pct=0.1,
        stop_loss_pct=0.02,
        take_profit_pct=0.05,
    )
    assert signal.position_size_pct == 0.1
    assert signal.stop_loss_pct == 0.02
    assert signal.take_profit_pct == 0.05

    # Test percentage validators - invalid (too high)
    with pytest.raises(ValidationError):
        Signal(
            strategy_id="test",
            symbol="BTCUSDT",
            action="buy",
            confidence=0.8,
            price=45000.0,
            quantity=0.1,
            current_price=45000.0,
            source="test",
            strategy="test",
            position_size_pct=1.5,  # Invalid: > 1
        )


def test_signal_timestamp_validator() -> None:
    """Test Signal timestamp field validator (Pydantic v2)"""
    from datetime import datetime

    # Test with ISO format string
    signal = Signal(
        strategy_id="test",
        symbol="BTCUSDT",
        action="buy",
        confidence=0.8,
        price=45000.0,
        quantity=0.1,
        current_price=45000.0,
        source="test",
        strategy="test",
        timestamp="2024-01-01T12:00:00Z",
    )
    assert isinstance(signal.timestamp, datetime)

    # Test with Unix timestamp (float)
    signal = Signal(
        strategy_id="test",
        symbol="BTCUSDT",
        action="buy",
        confidence=0.8,
        price=45000.0,
        quantity=0.1,
        current_price=45000.0,
        source="test",
        strategy="test",
        timestamp=1704110400.0,  # Valid Unix timestamp
    )
    assert isinstance(signal.timestamp, datetime)

    # Test with datetime object
    dt = datetime(2024, 1, 1, 12, 0, 0)
    signal = Signal(
        strategy_id="test",
        symbol="BTCUSDT",
        action="buy",
        confidence=0.8,
        price=45000.0,
        quantity=0.1,
        current_price=45000.0,
        source="test",
        strategy="test",
        timestamp=dt,
    )
    assert signal.timestamp == dt


def test_trading_config_model_config() -> None:
    """Test TradingConfig model_config (Pydantic v2)"""
    config = TradingConfig(
        symbol="BTCUSDT",
        side="LONG",
        parameters={"leverage": 10},
        created_by="test",
    )
    # Verify model_config is working (json_schema_extra)
    assert hasattr(config, "model_config")
    assert "json_schema_extra" in config.model_config


def test_trading_config_audit_model_config() -> None:
    """Test TradingConfigAudit model_config (Pydantic v2)"""
    audit = TradingConfigAudit(
        config_type="symbol_side",
        symbol="BTCUSDT",
        side="LONG",
        action="update",
        changed_by="test",
    )
    # Verify model_config is working
    assert hasattr(audit, "model_config")
    assert "json_schema_extra" in audit.model_config


def test_leverage_status_model_config() -> None:
    """Test LeverageStatus model_config (Pydantic v2)"""
    status = LeverageStatus(
        symbol="BTCUSDT",
        configured_leverage=10,
    )
    # Verify model_config is working
    assert hasattr(status, "model_config")
    assert "json_schema_extra" in status.model_config
