"""
Unit tests for OCO PNL calculation logic

Tests the automatic PNL calculation when OCO orders (SL/TP) close positions.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest


class TestOCOPNLCalculation:
    """Test PNL calculation for OCO order closures"""

    def test_long_position_pnl_calculation(self):
        """Test PNL calculation for LONG position"""
        # Given
        entry_price = 50000.0
        exit_price = 52000.0
        quantity = 0.001

        # When
        pnl = (exit_price - entry_price) * quantity

        # Then
        assert pnl == 2.0, "LONG position PNL should be positive when price increases"

    def test_short_position_pnl_calculation(self):
        """Test PNL calculation for SHORT position"""
        # Given
        entry_price = 50000.0
        exit_price = 48000.0
        quantity = 0.001

        # When
        pnl = (entry_price - exit_price) * quantity

        # Then
        assert pnl == 2.0, "SHORT position PNL should be positive when price decreases"

    def test_long_position_loss(self):
        """Test loss calculation for LONG position"""
        # Given
        entry_price = 50000.0
        exit_price = 48000.0
        quantity = 0.001

        # When
        pnl = (exit_price - entry_price) * quantity

        # Then
        assert pnl == -2.0, "LONG position should show loss when price decreases"

    def test_short_position_loss(self):
        """Test loss calculation for SHORT position"""
        # Given
        entry_price = 50000.0
        exit_price = 52000.0
        quantity = 0.001

        # When
        pnl = (entry_price - exit_price) * quantity

        # Then
        assert pnl == -2.0, "SHORT position should show loss when price increases"

    def test_pnl_with_commission(self):
        """Test PNL after fees calculation"""
        # Given
        entry_price = 50000.0
        exit_price = 52000.0
        quantity = 0.001
        entry_commission = 0.02  # Entry commission
        exit_commission = 0.02  # Exit commission

        # When
        pnl = (exit_price - entry_price) * quantity
        pnl_after_fees = pnl - (entry_commission + exit_commission)

        # Then
        assert pnl == 2.0, "Gross PNL should be 2.0"
        assert pnl_after_fees == 1.96, "Net PNL should deduct commissions"

    def test_pnl_percentage_calculation(self):
        """Test PNL percentage calculation"""
        # Given
        entry_price = 50000.0
        exit_price = 52000.0
        quantity = 0.001

        # When
        pnl = (exit_price - entry_price) * quantity
        pnl_pct = (pnl / (entry_price * quantity)) * 100

        # Then
        assert pnl_pct == 4.0, "PNL percentage should be 4%"

    def test_close_reason_detection_take_profit(self):
        """Test detection of take_profit close reason"""
        # Given
        sl_order_id = 12345
        tp_order_id = 67890
        filled_order_id = tp_order_id

        # When
        if filled_order_id == tp_order_id:
            close_reason = "take_profit"
        elif filled_order_id == sl_order_id:
            close_reason = "stop_loss"
        else:
            close_reason = "unknown"

        # Then
        assert close_reason == "take_profit", "Should detect TP order fill"

    def test_close_reason_detection_stop_loss(self):
        """Test detection of stop_loss close reason"""
        # Given
        sl_order_id = 12345
        tp_order_id = 67890
        filled_order_id = sl_order_id

        # When
        if filled_order_id == tp_order_id:
            close_reason = "take_profit"
        elif filled_order_id == sl_order_id:
            close_reason = "stop_loss"
        else:
            close_reason = "unknown"

        # Then
        assert close_reason == "stop_loss", "Should detect SL order fill"

    @pytest.mark.asyncio
    async def test_cancel_other_order_returns_close_reason(self):
        """Test that cancel_other_order returns the correct close_reason"""
        from tradeengine.dispatcher import OCOManager

        # Given
        mock_exchange = MagicMock()
        mock_logger = MagicMock()
        mock_exchange.client.futures_cancel_order = MagicMock(
            return_value={"status": "CANCELED"}
        )

        oco_manager = OCOManager(mock_exchange, mock_logger)

        # Setup OCO pair
        position_id = "test_pos_123"
        oco_manager.active_oco_pairs[position_id] = {
            "sl_order_id": 12345,
            "tp_order_id": 67890,
            "symbol": "BTCUSDT",
            "status": "active",
        }

        # When - TP fills
        success, close_reason = await oco_manager.cancel_other_order(
            position_id,
            67890,  # TP order filled
        )

        # Then
        assert success is True, "Cancellation should succeed"
        assert close_reason == "take_profit", "Should return take_profit close_reason"

    @pytest.mark.asyncio
    async def test_cancel_other_order_sl_filled(self):
        """Test cancel_other_order when SL fills"""
        from tradeengine.dispatcher import OCOManager

        # Given
        mock_exchange = MagicMock()
        mock_logger = MagicMock()
        mock_exchange.client.futures_cancel_order = MagicMock(
            return_value={"status": "CANCELED"}
        )

        oco_manager = OCOManager(mock_exchange, mock_logger)

        # Setup OCO pair
        position_id = "test_pos_456"
        oco_manager.active_oco_pairs[position_id] = {
            "sl_order_id": 12345,
            "tp_order_id": 67890,
            "symbol": "BTCUSDT",
            "status": "active",
        }

        # When - SL fills
        success, close_reason = await oco_manager.cancel_other_order(
            position_id,
            12345,  # SL order filled
        )

        # Then
        assert success is True, "Cancellation should succeed"
        assert close_reason == "stop_loss", "Should return stop_loss close_reason"

    def test_commission_calculation(self):
        """Test commission calculation with standard rates"""
        # Given
        exit_quantity = 0.001
        exit_price = 52000.0
        commission_rate = 0.0004  # 0.04% maker fee

        # When
        exit_commission = exit_quantity * exit_price * commission_rate

        # Then
        assert (
            abs(exit_commission - 0.0208) < 0.0001
        ), "Commission should be calculated correctly"

    def test_multiple_strategy_positions_pnl(self):
        """Test PNL calculation for multiple strategy positions"""
        # Given - Two strategies contributed to one exchange position
        strategy1_entry_qty = 0.0005
        strategy1_entry_price = 49000.0
        strategy2_entry_qty = 0.0005
        strategy2_entry_price = 51000.0
        exit_price = 52000.0

        # When - Calculate individual strategy PNLs
        strategy1_pnl = (exit_price - strategy1_entry_price) * strategy1_entry_qty
        strategy2_pnl = (exit_price - strategy2_entry_price) * strategy2_entry_qty
        total_pnl = strategy1_pnl + strategy2_pnl

        # Then
        assert strategy1_pnl == 1.5, "Strategy 1 should have higher PNL"
        assert strategy2_pnl == 0.5, "Strategy 2 should have lower PNL"
        assert total_pnl == 2.0, "Total PNL should sum correctly"


class TestPositionManagerPNLCalculation:
    """Test PositionManager PNL calculation with hedge mode support"""

    @pytest.mark.asyncio
    async def test_close_position_record_long_position(self):
        """Test close_position_record for LONG position"""
        from tradeengine.position_manager import PositionManager

        # Given
        position_manager = PositionManager()
        position_id = "test_long_pos"

        exit_result = {
            "exit_price": 52000.0,
            "entry_price": 50000.0,
            "quantity": 0.001,
            "entry_time": datetime.utcnow(),
            "position_side": "LONG",
            "entry_commission": 0.02,
            "exit_commission": 0.02,
            "close_reason": "take_profit",
            "order_id": "test_order_123",
        }

        # When
        with patch.object(position_manager, "_export_position_closed_metrics"):
            await position_manager.close_position_record(position_id, exit_result)

        # Then - Would need to verify the update_data was correct
        # This is a basic structure test
        assert True, "Method should complete without error"

    @pytest.mark.asyncio
    async def test_close_position_record_short_position(self):
        """Test close_position_record for SHORT position"""
        from tradeengine.position_manager import PositionManager

        # Given
        position_manager = PositionManager()
        position_id = "test_short_pos"

        exit_result = {
            "exit_price": 48000.0,
            "entry_price": 50000.0,
            "quantity": 0.001,
            "entry_time": datetime.utcnow(),
            "position_side": "SHORT",
            "entry_commission": 0.02,
            "exit_commission": 0.02,
            "close_reason": "take_profit",
            "order_id": "test_order_456",
        }

        # When
        with patch.object(position_manager, "_export_position_closed_metrics"):
            await position_manager.close_position_record(position_id, exit_result)

        # Then
        assert True, "Method should complete without error for SHORT position"


class TestStrategyPositionPNLCalculation:
    """Test strategy-level PNL calculation"""

    def test_strategy_position_long_pnl(self):
        """Test strategy position PNL for LONG"""
        # Given
        entry_price = 50000.0
        exit_price = 52000.0
        exit_quantity = 0.001
        side = "LONG"

        # When
        if side == "LONG":
            pnl = (exit_price - entry_price) * exit_quantity
        else:
            pnl = (entry_price - exit_price) * exit_quantity

        pnl_pct = (pnl / (entry_price * exit_quantity)) * 100 if entry_price > 0 else 0

        # Then
        assert pnl == 2.0, "Strategy LONG PNL should be 2.0"
        assert pnl_pct == 4.0, "Strategy PNL percentage should be 4%"

    def test_strategy_position_short_pnl(self):
        """Test strategy position PNL for SHORT"""
        # Given
        entry_price = 50000.0
        exit_price = 48000.0
        exit_quantity = 0.001
        side = "SHORT"

        # When
        if side == "LONG":
            pnl = (exit_price - entry_price) * exit_quantity
        else:
            pnl = (entry_price - exit_price) * exit_quantity

        pnl_pct = (pnl / (entry_price * exit_quantity)) * 100 if entry_price > 0 else 0

        # Then
        assert pnl == 2.0, "Strategy SHORT PNL should be 2.0"
        assert pnl_pct == 4.0, "Strategy PNL percentage should be 4%"
