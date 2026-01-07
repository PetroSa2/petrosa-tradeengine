"""Tests for strategy_position_manager module"""
import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime, timedelta
import uuid

from tradeengine.strategy_position_manager import StrategyPositionManager


@pytest.fixture
def strategy_position_manager():
    """Create a StrategyPositionManager instance for testing"""
    manager = StrategyPositionManager()
    return manager


@pytest.fixture
def sample_strategy_position():
    """Create a sample strategy position for testing"""
    return {
        "strategy_position_id": str(uuid.uuid4()),
        "strategy_id": "test-strategy-1",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "quantity": 0.001,
        "entry_price": 50000.0,
        "current_price": 51000.0,
        "unrealized_pnl": 10.0,
        "realized_pnl": 0.0,
        "status": "open",
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }


class TestStrategyPositionManagerBasic:
    """Test basic StrategyPositionManager functionality"""

    def test_initialization(self, strategy_position_manager):
        """Test StrategyPositionManager initialization"""
        assert strategy_position_manager is not None
        assert hasattr(strategy_position_manager, 'positions')

    def test_create_strategy_position(self, strategy_position_manager, sample_strategy_position):
        """Test creating a strategy position"""
        position_id = strategy_position_manager.create_strategy_position(
            strategy_id=sample_strategy_position["strategy_id"],
            symbol=sample_strategy_position["symbol"],
            side=sample_strategy_position["side"],
            quantity=sample_strategy_position["quantity"],
            entry_price=sample_strategy_position["entry_price"],
        )
        
        assert position_id is not None
        assert isinstance(position_id, str)
        
        # Verify position was created
        position = strategy_position_manager.get_strategy_position(position_id)
        assert position is not None
        assert position["strategy_id"] == sample_strategy_position["strategy_id"]
        assert position["symbol"] == sample_strategy_position["symbol"]
        assert position["side"] == sample_strategy_position["side"]
        assert position["quantity"] == sample_strategy_position["quantity"]
        assert position["entry_price"] == sample_strategy_position["entry_price"]

    def test_get_strategy_position(self, strategy_position_manager, sample_strategy_position):
        """Test getting a strategy position"""
        position_id = strategy_position_manager.create_strategy_position(
            strategy_id=sample_strategy_position["strategy_id"],
            symbol=sample_strategy_position["symbol"],
            side=sample_strategy_position["side"],
            quantity=sample_strategy_position["quantity"],
            entry_price=sample_strategy_position["entry_price"],
        )
        
        position = strategy_position_manager.get_strategy_position(position_id)
        assert position is not None
        assert position["strategy_position_id"] == position_id

    def test_get_strategy_position_not_found(self, strategy_position_manager):
        """Test getting a non-existent strategy position"""
        position = strategy_position_manager.get_strategy_position("nonexistent-id")
        assert position is None

    def test_get_strategy_positions_by_strategy(self, strategy_position_manager, sample_strategy_position):
        """Test getting positions by strategy ID"""
        # Create multiple positions for the same strategy
        position_id_1 = strategy_position_manager.create_strategy_position(
            strategy_id=sample_strategy_position["strategy_id"],
            symbol="BTCUSDT",
            side="LONG",
            quantity=0.001,
            entry_price=50000.0,
        )
        
        position_id_2 = strategy_position_manager.create_strategy_position(
            strategy_id=sample_strategy_position["strategy_id"],
            symbol="ETHUSDT",
            side="LONG",
            quantity=0.01,
            entry_price=3000.0,
        )
        
        # Create position for different strategy
        position_id_3 = strategy_position_manager.create_strategy_position(
            strategy_id="other-strategy",
            symbol="BTCUSDT",
            side="LONG",
            quantity=0.002,
            entry_price=51000.0,
        )
        
        positions = strategy_position_manager.get_strategy_positions_by_strategy(
            sample_strategy_position["strategy_id"]
        )
        
        assert len(positions) == 2
        position_ids = [p["strategy_position_id"] for p in positions]
        assert position_id_1 in position_ids
        assert position_id_2 in position_ids
        assert position_id_3 not in position_ids

    def test_get_strategy_positions_by_exchange_position(self, strategy_position_manager):
        """Test getting positions by exchange position"""
        # Create positions for the same exchange position
        position_id_1 = strategy_position_manager.create_strategy_position(
            strategy_id="strategy-1",
            symbol="BTCUSDT",
            side="LONG",
            quantity=0.001,
            entry_price=50000.0,
        )
        
        position_id_2 = strategy_position_manager.create_strategy_position(
            strategy_id="strategy-2",
            symbol="BTCUSDT",
            side="LONG",
            quantity=0.002,
            entry_price=51000.0,
        )
        
        # Create position for different exchange position
        position_id_3 = strategy_position_manager.create_strategy_position(
            strategy_id="strategy-1",
            symbol="ETHUSDT",
            side="LONG",
            quantity=0.01,
            entry_price=3000.0,
        )
        
        positions = strategy_position_manager.get_strategy_positions_by_exchange_position(
            symbol="BTCUSDT",
            position_side="LONG"
        )
        
        assert len(positions) == 2
        position_ids = [p["strategy_position_id"] for p in positions]
        assert position_id_1 in position_ids
        assert position_id_2 in position_ids
        assert position_id_3 not in position_ids

    def test_update_strategy_position(self, strategy_position_manager, sample_strategy_position):
        """Test updating a strategy position"""
        position_id = strategy_position_manager.create_strategy_position(
            strategy_id=sample_strategy_position["strategy_id"],
            symbol=sample_strategy_position["symbol"],
            side=sample_strategy_position["side"],
            quantity=sample_strategy_position["quantity"],
            entry_price=sample_strategy_position["entry_price"],
        )
        
        # Update position
        strategy_position_manager.update_strategy_position(
            position_id=position_id,
            quantity=0.002,
            current_price=52000.0,
        )
        
        position = strategy_position_manager.get_strategy_position(position_id)
        assert position["quantity"] == 0.002
        assert position["current_price"] == 52000.0

    def test_close_strategy_position(self, strategy_position_manager, sample_strategy_position):
        """Test closing a strategy position"""
        position_id = strategy_position_manager.create_strategy_position(
            strategy_id=sample_strategy_position["strategy_id"],
            symbol=sample_strategy_position["symbol"],
            side=sample_strategy_position["side"],
            quantity=sample_strategy_position["quantity"],
            entry_price=sample_strategy_position["entry_price"],
        )
        
        # Close position
        strategy_position_manager.close_strategy_position(
            position_id=position_id,
            exit_price=51000.0,
            realized_pnl=10.0,
        )
        
        position = strategy_position_manager.get_strategy_position(position_id)
        assert position["status"] == "closed"
        assert position["exit_price"] == 51000.0
        assert position["realized_pnl"] == 10.0

    def test_get_all_strategy_positions(self, strategy_position_manager):
        """Test getting all strategy positions"""
        # Create multiple positions
        position_id_1 = strategy_position_manager.create_strategy_position(
            strategy_id="strategy-1",
            symbol="BTCUSDT",
            side="LONG",
            quantity=0.001,
            entry_price=50000.0,
        )
        
        position_id_2 = strategy_position_manager.create_strategy_position(
            strategy_id="strategy-2",
            symbol="ETHUSDT",
            side="SHORT",
            quantity=0.01,
            entry_price=3000.0,
        )
        
        all_positions = strategy_position_manager.get_all_strategy_positions()
        
        assert len(all_positions) >= 2
        position_ids = [p["strategy_position_id"] for p in all_positions]
        assert position_id_1 in position_ids
        assert position_id_2 in position_ids

    def test_get_strategy_positions_by_symbol(self, strategy_position_manager):
        """Test getting positions by symbol"""
        # Create positions for BTCUSDT
        position_id_1 = strategy_position_manager.create_strategy_position(
            strategy_id="strategy-1",
            symbol="BTCUSDT",
            side="LONG",
            quantity=0.001,
            entry_price=50000.0,
        )
        
        position_id_2 = strategy_position_manager.create_strategy_position(
            strategy_id="strategy-2",
            symbol="BTCUSDT",
            side="SHORT",
            quantity=0.002,
            entry_price=51000.0,
        )
        
        # Create position for different symbol
        position_id_3 = strategy_position_manager.create_strategy_position(
            strategy_id="strategy-1",
            symbol="ETHUSDT",
            side="LONG",
            quantity=0.01,
            entry_price=3000.0,
        )
        
        positions = strategy_position_manager.get_strategy_positions_by_symbol("BTCUSDT")
        
        assert len(positions) == 2
        position_ids = [p["strategy_position_id"] for p in positions]
        assert position_id_1 in position_ids
        assert position_id_2 in position_ids
        assert position_id_3 not in position_ids

