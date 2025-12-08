"""
Comprehensive tests for contracts/signal.py to increase coverage
"""

from datetime import datetime

import pytest
from pydantic import ValidationError

from contracts.signal import (
    OrderType,
    Signal,
    SignalStrength,
    SignalType,
    StrategyMode,
    TimeFrame,
    TimeInForce,
)


class TestEnums:
    """Test all enum definitions"""

    def test_signal_type_enum(self):
        """Test SignalType enum values"""
        assert SignalType.BUY == "buy"
        assert SignalType.SELL == "sell"
        assert SignalType.HOLD == "hold"
        assert SignalType.CLOSE == "close"

    def test_signal_strength_enum(self):
        """Test SignalStrength enum values"""
        assert SignalStrength.WEAK == "weak"
        assert SignalStrength.MEDIUM == "medium"
        assert SignalStrength.STRONG == "strong"
        assert SignalStrength.EXTREME == "extreme"

    def test_timeframe_enum(self):
        """Test TimeFrame enum values"""
        assert TimeFrame.TICK == "tick"
        assert TimeFrame.MINUTE_1 == "1m"
        assert TimeFrame.MINUTE_5 == "5m"
        assert TimeFrame.HOUR_1 == "1h"
        assert TimeFrame.DAY_1 == "1d"

    def test_order_type_enum(self):
        """Test OrderType enum values"""
        assert OrderType.MARKET == "market"
        assert OrderType.LIMIT == "limit"
        assert OrderType.STOP == "stop"
        assert OrderType.STOP_LIMIT == "stop_limit"

    def test_time_in_force_enum(self):
        """Test TimeInForce enum values"""
        assert TimeInForce.GTC == "GTC"
        assert TimeInForce.IOC == "IOC"
        assert TimeInForce.FOK == "FOK"
        assert TimeInForce.GTX == "GTX"

    def test_strategy_mode_enum(self):
        """Test StrategyMode enum values"""
        assert StrategyMode.DETERMINISTIC == "deterministic"
        assert StrategyMode.ML_LIGHT == "ml_light"
        assert StrategyMode.LLM_REASONING == "llm_reasoning"


class TestSignalValidation:
    """Test Signal validation"""

    def test_valid_signal_creation(self):
        """Test creating a valid signal"""
        signal = Signal(
            strategy_id="test_strategy",
            symbol="BTCUSDT",
            action="buy",
            confidence=0.8,
            price=50000.0,
            quantity=0.1,
            current_price=50000.0,
            source="test",
            strategy="momentum",
        )
        assert signal.symbol == "BTCUSDT"
        assert signal.confidence == 0.8

    def test_confidence_validation_above_one(self):
        """Test confidence validation fails for values > 1"""
        with pytest.raises(ValidationError) as exc_info:
            Signal(
                strategy_id="test_strategy",
                symbol="BTCUSDT",
                action="buy",
                confidence=1.5,  # Invalid
                price=50000.0,
                quantity=0.1,
                current_price=50000.0,
                source="test",
                strategy="momentum",
            )
        assert exc_info.value is not None

    def test_confidence_validation_below_zero(self):
        """Test confidence validation fails for values < 0"""
        with pytest.raises(ValidationError) as exc_info:
            Signal(
                strategy_id="test_strategy",
                symbol="BTCUSDT",
                action="buy",
                confidence=-0.1,  # Invalid
                price=50000.0,
                quantity=0.1,
                current_price=50000.0,
                source="test",
                strategy="momentum",
            )
        assert exc_info.value is not None

    def test_timestamp_validation_string_iso(self):
        """Test timestamp validation with ISO format string"""
        signal = Signal(
            strategy_id="test_strategy",
            symbol="BTCUSDT",
            action="buy",
            confidence=0.8,
            price=50000.0,
            quantity=0.1,
            current_price=50000.0,
            source="test",
            strategy="momentum",
            timestamp="2025-01-15T10:00:00Z",
        )
        assert isinstance(signal.timestamp, datetime)

    def test_timestamp_validation_unix_timestamp(self):
        """Test timestamp validation with Unix timestamp"""
        signal = Signal(
            strategy_id="test_strategy",
            symbol="BTCUSDT",
            action="buy",
            confidence=0.8,
            price=50000.0,
            quantity=0.1,
            current_price=50000.0,
            source="test",
            strategy="momentum",
            timestamp=1705315200,  # Unix timestamp
        )
        assert isinstance(signal.timestamp, datetime)

    def test_timestamp_validation_invalid_unix_timestamp(self):
        """Test timestamp validation with invalid Unix timestamp"""
        signal = Signal(
            strategy_id="test_strategy",
            symbol="BTCUSDT",
            action="buy",
            confidence=0.8,
            price=50000.0,
            quantity=0.1,
            current_price=50000.0,
            source="test",
            strategy="momentum",
            timestamp=999999999999999,  # Invalid Unix timestamp
        )
        # Should fall back to current time
        assert isinstance(signal.timestamp, datetime)

    def test_timestamp_validation_invalid_string(self):
        """Test timestamp validation with invalid string"""
        signal = Signal(
            strategy_id="test_strategy",
            symbol="BTCUSDT",
            action="buy",
            confidence=0.8,
            price=50000.0,
            quantity=0.1,
            current_price=50000.0,
            source="test",
            strategy="momentum",
            timestamp="invalid_timestamp",
        )
        # Should fall back to current time
        assert isinstance(signal.timestamp, datetime)

    def test_timestamp_validation_invalid_type(self):
        """Test timestamp validation with invalid type"""
        signal = Signal(
            strategy_id="test_strategy",
            symbol="BTCUSDT",
            action="buy",
            confidence=0.8,
            price=50000.0,
            quantity=0.1,
            current_price=50000.0,
            source="test",
            strategy="momentum",
            timestamp=[],  # Invalid type
        )
        # Should fall back to current time
        assert isinstance(signal.timestamp, datetime)

    def test_model_confidence_validation(self):
        """Test model confidence validation"""
        signal = Signal(
            strategy_id="test_strategy",
            symbol="BTCUSDT",
            action="buy",
            confidence=0.8,
            price=50000.0,
            quantity=0.1,
            current_price=50000.0,
            source="test",
            strategy="momentum",
            model_confidence=0.95,
        )
        assert signal.model_confidence == 0.95

    def test_model_confidence_validation_invalid(self):
        """Test model confidence validation fails for invalid values"""
        with pytest.raises(ValidationError) as exc_info:
            Signal(
                strategy_id="test_strategy",
                symbol="BTCUSDT",
                action="buy",
                confidence=0.8,
                price=50000.0,
                quantity=0.1,
                current_price=50000.0,
                source="test",
                strategy="momentum",
                model_confidence=1.5,  # Invalid
            )
        assert exc_info.value is not None

    def test_position_size_pct_validation(self):
        """Test position_size_pct validation"""
        signal = Signal(
            strategy_id="test_strategy",
            symbol="BTCUSDT",
            action="buy",
            confidence=0.8,
            price=50000.0,
            quantity=0.1,
            current_price=50000.0,
            source="test",
            strategy="momentum",
            position_size_pct=0.1,
        )
        assert signal.position_size_pct == 0.1

    def test_position_size_pct_validation_invalid(self):
        """Test position_size_pct validation fails for invalid values"""
        with pytest.raises(ValidationError) as exc_info:
            Signal(
                strategy_id="test_strategy",
                symbol="BTCUSDT",
                action="buy",
                confidence=0.8,
                price=50000.0,
                quantity=0.1,
                current_price=50000.0,
                source="test",
                strategy="momentum",
                position_size_pct=1.5,  # Invalid
            )
        assert exc_info.value is not None

    def test_stop_loss_pct_validation(self):
        """Test stop_loss_pct validation"""
        signal = Signal(
            strategy_id="test_strategy",
            symbol="BTCUSDT",
            action="buy",
            confidence=0.8,
            price=50000.0,
            quantity=0.1,
            current_price=50000.0,
            source="test",
            strategy="momentum",
            stop_loss_pct=0.02,
        )
        assert signal.stop_loss_pct == 0.02

    def test_stop_loss_pct_validation_invalid(self):
        """Test stop_loss_pct validation fails for invalid values"""
        with pytest.raises(ValidationError) as exc_info:
            Signal(
                strategy_id="test_strategy",
                symbol="BTCUSDT",
                action="buy",
                confidence=0.8,
                price=50000.0,
                quantity=0.1,
                current_price=50000.0,
                source="test",
                strategy="momentum",
                stop_loss_pct=1.5,  # Invalid
            )
        assert exc_info.value is not None

    def test_take_profit_pct_validation(self):
        """Test take_profit_pct validation"""
        signal = Signal(
            strategy_id="test_strategy",
            symbol="BTCUSDT",
            action="buy",
            confidence=0.8,
            price=50000.0,
            quantity=0.1,
            current_price=50000.0,
            source="test",
            strategy="momentum",
            take_profit_pct=0.05,
        )
        assert signal.take_profit_pct == 0.05

    def test_take_profit_pct_validation_invalid(self):
        """Test take_profit_pct validation fails for invalid values"""
        with pytest.raises(ValidationError) as exc_info:
            Signal(
                strategy_id="test_strategy",
                symbol="BTCUSDT",
                action="buy",
                confidence=0.8,
                price=50000.0,
                quantity=0.1,
                current_price=50000.0,
                source="test",
                strategy="momentum",
                take_profit_pct=-0.1,  # Invalid
            )
        assert exc_info.value is not None

    def test_signal_with_all_fields(self):
        """Test signal with all optional fields populated"""
        signal = Signal(
            id="sig123",
            strategy_id="test_strategy",
            signal_id="sig456",
            strategy_mode=StrategyMode.ML_LIGHT,
            symbol="BTCUSDT",
            signal_type=SignalType.BUY,
            action="buy",
            confidence=0.8,
            strength=SignalStrength.STRONG,
            price=50000.0,
            quantity=0.1,
            current_price=50000.0,
            target_price=51000.0,
            source="test",
            strategy="momentum",
            metadata={"test": "data"},
            timeframe="1h",
            order_type=OrderType.LIMIT,
            time_in_force=TimeInForce.GTC,
            position_size_pct=0.1,
            quote_quantity=5000.0,
            stop_loss=49000.0,
            stop_loss_pct=0.02,
            take_profit=52000.0,
            take_profit_pct=0.04,
            conditional_price=50500.0,
            conditional_direction="above",
            conditional_timeout=3600,
            iceberg_quantity=0.01,
            client_order_id="client123",
            model_confidence=0.95,
            model_features={"feature1": 1.0},
            llm_reasoning="Strong upward momentum",
            llm_alternatives=[{"action": "hold", "confidence": 0.2}],
            indicators={"rsi": 65},
            rationale="Technical analysis shows strong buy signal",
            meta={"additional": "info"},
        )
        assert signal.id == "sig123"
        assert signal.symbol == "BTCUSDT"
        assert signal.strategy_mode == StrategyMode.ML_LIGHT


class TestSignalJSONSerialization:
    """Test Signal JSON serialization"""

    def test_signal_serialization(self):
        """Test signal can be serialized to JSON"""
        signal = Signal(
            strategy_id="test_strategy",
            symbol="BTCUSDT",
            action="buy",
            confidence=0.8,
            price=50000.0,
            quantity=0.1,
            current_price=50000.0,
            source="test",
            strategy="momentum",
        )
        json_data = signal.model_dump()
        assert json_data["symbol"] == "BTCUSDT"
        assert json_data["confidence"] == 0.8

    def test_signal_json_with_datetime(self):
        """Test signal serialization includes ISO formatted timestamp"""
        signal = Signal(
            strategy_id="test_strategy",
            symbol="BTCUSDT",
            action="buy",
            confidence=0.8,
            price=50000.0,
            quantity=0.1,
            current_price=50000.0,
            source="test",
            strategy="momentum",
        )
        json_str = signal.model_dump_json()
        assert isinstance(json_str, str)
        assert "BTCUSDT" in json_str
