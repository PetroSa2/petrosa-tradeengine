"""
Tests for hedge mode conflict resolution

This module tests the critical fixes for hedge mode support:
1. Opposite directions (BUY/SELL) are not conflicts in hedge mode
2. Same-direction signals handled according to configuration
3. Position tracking by (symbol, position_side) tuple
4. Proper position separation for LONG and SHORT
"""

from datetime import datetime

import pytest

from contracts.order import TradeOrder
from contracts.signal import OrderType, Signal, SignalStrength, TimeInForce
from tradeengine.position_manager import PositionManager
from tradeengine.signal_aggregator import (
    DeterministicProcessor,
    LLMProcessor,
    MLProcessor,
)

# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def btc_buy_signal():
    """Create a BTC buy signal"""
    return Signal(
        strategy_id="momentum_v1",
        symbol="BTCUSDT",
        action="buy",
        confidence=0.8,
        strength=SignalStrength.STRONG,
        price=45000.0,
        quantity=0.001,
        current_price=45000.0,
        target_price=48000.0,
        source="test",
        strategy="momentum",
        timeframe="1h",
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.GTC,
        timestamp=datetime.utcnow(),
    )


@pytest.fixture
def btc_sell_signal():
    """Create a BTC sell signal"""
    return Signal(
        strategy_id="mean_reversion_v1",
        symbol="BTCUSDT",
        action="sell",
        confidence=0.75,
        strength=SignalStrength.MEDIUM,
        price=45000.0,
        quantity=0.001,
        current_price=45000.0,
        target_price=42000.0,
        source="test",
        strategy="mean_reversion",
        timeframe="1h",
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.GTC,
        timestamp=datetime.utcnow(),
    )


@pytest.fixture
def btc_buy_signal_2():
    """Create a second BTC buy signal from different strategy"""
    return Signal(
        strategy_id="breakout_v1",
        symbol="BTCUSDT",
        action="buy",
        confidence=0.7,
        strength=SignalStrength.MEDIUM,
        price=45000.0,
        quantity=0.002,
        current_price=45000.0,
        target_price=49000.0,
        source="test",
        strategy="breakout",
        timeframe="1h",
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.GTC,
        timestamp=datetime.utcnow(),
    )


# =============================================================================
# Hedge Mode Conflict Tests - DeterministicProcessor
# =============================================================================


class TestHedgeModeConflicts:
    """Test hedge mode conflict resolution"""

    @pytest.mark.asyncio
    async def test_opposite_directions_not_conflict_hedge_mode(
        self, btc_buy_signal, btc_sell_signal, monkeypatch
    ):
        """In hedge mode, BUY and SELL on same symbol should NOT conflict"""
        monkeypatch.setattr("shared.constants.POSITION_MODE", "hedge")
        monkeypatch.setattr("shared.constants.POSITION_MODE_AWARE_CONFLICTS", True)

        processor = DeterministicProcessor()

        # First signal (BUY)
        active_signals = {}
        result1 = await processor.process(btc_buy_signal, active_signals)
        assert result1["status"] == "executed"

        # Add to active signals
        active_signals[btc_buy_signal.strategy_id] = btc_buy_signal

        # Second signal (SELL) - should NOT conflict in hedge mode
        result2 = await processor.process(btc_sell_signal, active_signals)
        assert result2["status"] == "executed"
        assert "conflict" not in result2.get("reason", "").lower()

    @pytest.mark.asyncio
    async def test_opposite_directions_conflict_oneway_mode(
        self, btc_buy_signal, btc_sell_signal, monkeypatch
    ):
        """In one-way mode, BUY and SELL on same symbol SHOULD conflict"""
        monkeypatch.setattr("shared.constants.POSITION_MODE", "one-way")
        monkeypatch.setattr("shared.constants.POSITION_MODE_AWARE_CONFLICTS", True)

        processor = DeterministicProcessor()

        # First signal (BUY)
        active_signals = {}
        result1 = await processor.process(btc_buy_signal, active_signals)
        assert result1["status"] == "executed"

        # Add to active signals
        active_signals[btc_buy_signal.strategy_id] = btc_buy_signal

        # Second signal (SELL) - should conflict in one-way mode
        result2 = await processor.process(btc_sell_signal, active_signals)
        assert result2["status"] == "rejected"
        assert "conflict" in result2.get("reason", "").lower()

    @pytest.mark.asyncio
    async def test_position_mode_aware_disabled(
        self, btc_buy_signal, btc_sell_signal, monkeypatch
    ):
        """When position_mode_aware_conflicts is False, treat as conflicts even in hedge mode"""
        monkeypatch.setattr("shared.constants.POSITION_MODE", "hedge")
        monkeypatch.setattr("shared.constants.POSITION_MODE_AWARE_CONFLICTS", False)

        processor = DeterministicProcessor()

        # First signal (BUY)
        active_signals = {}
        result1 = await processor.process(btc_buy_signal, active_signals)
        assert result1["status"] == "executed"

        # Add to active signals
        active_signals[btc_buy_signal.strategy_id] = btc_buy_signal

        # Second signal (SELL) - should conflict even in hedge mode
        result2 = await processor.process(btc_sell_signal, active_signals)
        assert result2["status"] == "rejected"


# =============================================================================
# Same-Direction Signal Tests
# =============================================================================


class TestSameDirectionSignals:
    """Test same-direction signal handling"""

    @pytest.mark.asyncio
    async def test_same_direction_accumulate(
        self, btc_buy_signal, btc_buy_signal_2, monkeypatch
    ):
        """In accumulate mode, multiple BUY signals should be allowed"""
        monkeypatch.setattr(
            "shared.constants.SAME_DIRECTION_CONFLICT_RESOLUTION", "accumulate"
        )

        processor = DeterministicProcessor()

        # First BUY signal
        active_signals = {}
        result1 = await processor.process(btc_buy_signal, active_signals)
        assert result1["status"] == "executed"

        # Add to active signals
        active_signals[btc_buy_signal.strategy_id] = btc_buy_signal

        # Second BUY signal from different strategy - should be allowed
        result2 = await processor.process(btc_buy_signal_2, active_signals)
        assert result2["status"] == "executed"
        assert "accumulating" in result2.get("reason", "").lower()

    @pytest.mark.asyncio
    async def test_same_direction_strongest_wins(
        self, btc_buy_signal, btc_buy_signal_2, monkeypatch
    ):
        """In strongest_wins mode, only highest confidence signal executes"""
        monkeypatch.setattr(
            "shared.constants.SAME_DIRECTION_CONFLICT_RESOLUTION", "strongest_wins"
        )

        processor = DeterministicProcessor()

        # First BUY signal (confidence 0.8)
        active_signals = {}
        result1 = await processor.process(btc_buy_signal, active_signals)
        assert result1["status"] == "executed"

        # Add to active signals
        active_signals[btc_buy_signal.strategy_id] = btc_buy_signal

        # Second BUY signal (confidence 0.7) - should be rejected
        result2 = await processor.process(btc_buy_signal_2, active_signals)
        assert result2["status"] == "rejected"
        assert "weaker" in result2.get("reason", "").lower()

    @pytest.mark.asyncio
    async def test_same_direction_reject_duplicates(
        self, btc_buy_signal, btc_buy_signal_2, monkeypatch
    ):
        """In reject_duplicates mode, second same-direction signal is rejected"""
        monkeypatch.setattr(
            "shared.constants.SAME_DIRECTION_CONFLICT_RESOLUTION", "reject_duplicates"
        )

        processor = DeterministicProcessor()

        # First BUY signal
        active_signals = {}
        result1 = await processor.process(btc_buy_signal, active_signals)
        assert result1["status"] == "executed"

        # Add to active signals
        active_signals[btc_buy_signal.strategy_id] = btc_buy_signal

        # Second BUY signal - should be rejected
        result2 = await processor.process(btc_buy_signal_2, active_signals)
        assert result2["status"] == "rejected"
        assert "already exists" in result2.get("reason", "").lower()


# =============================================================================
# Position Manager Hedge Mode Tests
# =============================================================================


class TestPositionManagerHedgeMode:
    """Test position manager with hedge mode support"""

    @pytest.mark.asyncio
    async def test_separate_long_short_positions(self):
        """Position manager should track LONG and SHORT separately"""
        position_manager = PositionManager()
        position_manager.mongodb_db = None  # Disable MongoDB for unit test

        # Create LONG position order
        long_order = TradeOrder(
            symbol="BTCUSDT",
            type="market",
            side="buy",
            amount=0.001,
            target_price=45000.0,
            position_side="LONG",
        )

        # Create SHORT position order
        short_order = TradeOrder(
            symbol="BTCUSDT",
            type="market",
            side="sell",
            amount=0.002,
            target_price=45000.0,
            position_side="SHORT",
        )

        # Update positions
        await position_manager.update_position(
            long_order, {"fill_price": 45000.0, "amount": 0.001}
        )
        await position_manager.update_position(
            short_order, {"fill_price": 45000.0, "amount": 0.002}
        )

        # Should have two separate positions
        assert len(position_manager.positions) == 2

        # Get LONG position
        long_pos = position_manager.get_position("BTCUSDT", "LONG")
        assert long_pos is not None
        assert long_pos["position_side"] == "LONG"
        assert long_pos["quantity"] == 0.001

        # Get SHORT position
        short_pos = position_manager.get_position("BTCUSDT", "SHORT")
        assert short_pos is not None
        assert short_pos["position_side"] == "SHORT"
        assert short_pos["quantity"] == 0.002

        # Get all positions for symbol
        btc_positions = position_manager.get_positions_by_symbol("BTCUSDT")
        assert len(btc_positions) == 2

    @pytest.mark.asyncio
    async def test_position_key_tuple(self):
        """Position keys should be (symbol, position_side) tuples"""
        position_manager = PositionManager()
        position_manager.mongodb_db = None

        order = TradeOrder(
            symbol="ETHUSDT",
            type="market",
            side="buy",
            amount=0.1,
            target_price=3000.0,
            position_side="LONG",
        )

        await position_manager.update_position(
            order, {"fill_price": 3000.0, "amount": 0.1}
        )

        # Check that position key is a tuple
        position_keys = list(position_manager.positions.keys())
        assert len(position_keys) == 1
        assert isinstance(position_keys[0], tuple)
        assert position_keys[0] == ("ETHUSDT", "LONG")

    @pytest.mark.asyncio
    async def test_backward_compatibility_get_position(self):
        """get_position without position_side should still work"""
        position_manager = PositionManager()
        position_manager.mongodb_db = None

        order = TradeOrder(
            symbol="BNBUSDT",
            type="market",
            side="buy",
            amount=0.5,
            target_price=400.0,
            position_side="LONG",
        )

        await position_manager.update_position(
            order, {"fill_price": 400.0, "amount": 0.5}
        )

        # Should be able to get position without specifying side
        position = position_manager.get_position("BNBUSDT")
        assert position is not None
        assert position["symbol"] == "BNBUSDT"


# =============================================================================
# MLProcessor and LLMProcessor Tests
# =============================================================================


class TestMLProcessorHedgeMode:
    """Test MLProcessor with hedge mode support"""

    @pytest.mark.asyncio
    async def test_ml_processor_hedge_mode(
        self, btc_buy_signal, btc_sell_signal, monkeypatch
    ):
        """MLProcessor should respect hedge mode"""
        monkeypatch.setattr("shared.constants.POSITION_MODE", "hedge")
        monkeypatch.setattr("shared.constants.POSITION_MODE_AWARE_CONFLICTS", True)

        processor = MLProcessor()
        processor.model_loaded = True

        # First signal
        active_signals = {}
        result1 = await processor.process(btc_buy_signal, active_signals)
        assert result1["status"] == "executed"

        # Add to active signals
        active_signals[btc_buy_signal.strategy_id] = btc_buy_signal

        # Opposite direction signal - should not conflict
        result2 = await processor.process(btc_sell_signal, active_signals)
        assert result2["status"] == "executed"


class TestLLMProcessorHedgeMode:
    """Test LLMProcessor with hedge mode support"""

    @pytest.mark.asyncio
    async def test_llm_processor_hedge_mode(
        self, btc_buy_signal, btc_sell_signal, monkeypatch
    ):
        """LLMProcessor should respect hedge mode"""
        monkeypatch.setattr("shared.constants.POSITION_MODE", "hedge")
        monkeypatch.setattr("shared.constants.POSITION_MODE_AWARE_CONFLICTS", True)

        processor = LLMProcessor()
        processor.llm_available = True

        # Mock LLM reasoning to approve signals
        async def mock_llm_reasoning(context):
            return {"approved": True, "confidence": 0.8}

        processor._get_llm_reasoning = mock_llm_reasoning

        # First signal
        active_signals = {}
        result1 = await processor.process(btc_buy_signal, active_signals)
        assert result1["status"] == "executed"

        # Add to active signals
        active_signals[btc_buy_signal.strategy_id] = btc_buy_signal

        # Opposite direction signal - should not conflict
        result2 = await processor.process(btc_sell_signal, active_signals)
        assert result2["status"] == "executed"

    @pytest.mark.asyncio
    async def test_llm_context_includes_position_mode(
        self, btc_buy_signal, monkeypatch
    ):
        """LLM context should include position mode information"""
        monkeypatch.setattr("shared.constants.POSITION_MODE", "hedge")

        processor = LLMProcessor()

        context = processor._prepare_llm_context(btc_buy_signal, {}, [])

        assert "market_context" in context
        assert "position_mode" in context["market_context"]
        assert context["market_context"]["position_mode"] == "hedge"
        assert context["market_context"]["hedge_mode_enabled"] is True


# =============================================================================
# Integration Tests
# =============================================================================


class TestHedgeModeIntegration:
    """Integration tests for hedge mode"""

    @pytest.mark.asyncio
    async def test_complex_hedge_scenario(
        self, btc_buy_signal, btc_sell_signal, btc_buy_signal_2, monkeypatch
    ):
        """Test complex scenario with multiple signals in hedge mode"""
        monkeypatch.setattr("shared.constants.POSITION_MODE", "hedge")
        monkeypatch.setattr("shared.constants.POSITION_MODE_AWARE_CONFLICTS", True)
        monkeypatch.setattr(
            "shared.constants.SAME_DIRECTION_CONFLICT_RESOLUTION", "accumulate"
        )

        processor = DeterministicProcessor()
        position_manager = PositionManager()
        position_manager.mongodb_db = None

        active_signals = {}

        # Signal 1: BUY (should execute)
        result1 = await processor.process(btc_buy_signal, active_signals)
        assert result1["status"] == "executed"
        active_signals[btc_buy_signal.strategy_id] = btc_buy_signal

        # Signal 2: SELL (should execute - different direction, hedge mode)
        result2 = await processor.process(btc_sell_signal, active_signals)
        assert result2["status"] == "executed"
        active_signals[btc_sell_signal.strategy_id] = btc_sell_signal

        # Signal 3: BUY (should execute - accumulate mode)
        result3 = await processor.process(btc_buy_signal_2, active_signals)
        assert result3["status"] == "executed"
        active_signals[btc_buy_signal_2.strategy_id] = btc_buy_signal_2

        # All three signals should be executed
        assert len(active_signals) == 3

        # Create orders and update positions
        buy_order_1 = TradeOrder(
            symbol="BTCUSDT",
            type="market",
            side="buy",
            amount=0.001,
            target_price=45000.0,
            position_side="LONG",
        )

        sell_order = TradeOrder(
            symbol="BTCUSDT",
            type="market",
            side="sell",
            amount=0.001,
            target_price=45000.0,
            position_side="SHORT",
        )

        buy_order_2 = TradeOrder(
            symbol="BTCUSDT",
            type="market",
            side="buy",
            amount=0.002,
            target_price=45000.0,
            position_side="LONG",
        )

        await position_manager.update_position(
            buy_order_1, {"fill_price": 45000.0, "amount": 0.001}
        )
        await position_manager.update_position(
            sell_order, {"fill_price": 45000.0, "amount": 0.001}
        )
        await position_manager.update_position(
            buy_order_2, {"fill_price": 46000.0, "amount": 0.002}
        )

        # Should have 2 positions: LONG (accumulated) and SHORT
        assert len(position_manager.positions) == 2

        # LONG position should have accumulated quantity
        long_pos = position_manager.get_position("BTCUSDT", "LONG")
        assert long_pos["quantity"] == 0.003  # 0.001 + 0.002

        # SHORT position should be separate
        short_pos = position_manager.get_position("BTCUSDT", "SHORT")
        assert short_pos["quantity"] == 0.001


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
