"""Tests for strategy_position_manager module"""
import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime, timedelta
import uuid

from tradeengine.strategy_position_manager import StrategyPositionManager
from contracts.signal import Signal, TimeInForce
from contracts.order import TradeOrder, OrderSide, OrderType


@pytest.fixture
def strategy_position_manager():
    """Create a StrategyPositionManager instance for testing"""
    manager = StrategyPositionManager()
    return manager


@pytest.fixture
def sample_signal():
    """Create a sample signal for testing"""
    return Signal(
        signal_id="test_signal_123",
        strategy_id="test-strategy-1",
        symbol="BTCUSDT",
        action="buy",
        current_price=50000.0,
        quantity=0.001,
        confidence=0.85,
        timeframe="1h",
        take_profit=52000.0,
        stop_loss=48000.0,
        source="test",
        order_type="market",
        time_in_force=TimeInForce.GTC,
    )


@pytest.fixture
def sample_order():
    """Create a sample order for testing"""
    return TradeOrder(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        amount=0.001,
        target_price=50000.0,
    )


@pytest.fixture
def sample_execution_result():
    """Create a sample execution result for testing"""
    return {
        "status": "filled",
        "order_id": "test_order_123",
        "fill_price": 50000.0,
        "amount": 0.001,
    }


class TestStrategyPositionManagerBasic:
    """Test basic StrategyPositionManager functionality"""

    def test_initialization(self, strategy_position_manager):
        """Test StrategyPositionManager initialization"""
        assert strategy_position_manager is not None
        assert hasattr(strategy_position_manager, 'strategy_positions')
        assert hasattr(strategy_position_manager, 'exchange_positions')
        assert hasattr(strategy_position_manager, 'contributions')

    @pytest.mark.asyncio
    async def test_create_strategy_position(self, strategy_position_manager, sample_signal, sample_order, sample_execution_result):
        """Test creating a strategy position"""
        with patch('shared.mysql_client.position_client') as mock_client:
            mock_client.create_position = AsyncMock()
            
            position_id = await strategy_position_manager.create_strategy_position(
                signal=sample_signal,
                order=sample_order,
                execution_result=sample_execution_result,
            )
            
            assert position_id is not None
            assert isinstance(position_id, str)
            
            # Verify position was created
            position = strategy_position_manager.get_strategy_position(position_id)
            assert position is not None
            assert position["strategy_id"] == sample_signal.strategy_id
            assert position["symbol"] == sample_signal.symbol
            assert position["side"] == "LONG"
            assert position["entry_quantity"] == sample_execution_result["amount"]
            assert position["entry_price"] == sample_execution_result["fill_price"]

    def test_get_strategy_position(self, strategy_position_manager):
        """Test getting a strategy position"""
        # Manually add a position to test getter
        position_id = str(uuid.uuid4())
        strategy_position_manager.strategy_positions[position_id] = {
            "strategy_position_id": position_id,
            "strategy_id": "test-strategy",
            "symbol": "BTCUSDT",
            "side": "LONG",
        }
        
        position = strategy_position_manager.get_strategy_position(position_id)
        assert position is not None
        assert position["strategy_position_id"] == position_id

    def test_get_strategy_position_not_found(self, strategy_position_manager):
        """Test getting a non-existent strategy position"""
        position = strategy_position_manager.get_strategy_position("nonexistent-id")
        assert position is None

    def test_get_strategy_positions_by_strategy(self, strategy_position_manager):
        """Test getting positions by strategy ID"""
        # Manually add positions
        position_id_1 = str(uuid.uuid4())
        position_id_2 = str(uuid.uuid4())
        position_id_3 = str(uuid.uuid4())
        
        strategy_position_manager.strategy_positions[position_id_1] = {
            "strategy_position_id": position_id_1,
            "strategy_id": "test-strategy",
            "symbol": "BTCUSDT",
        }
        strategy_position_manager.strategy_positions[position_id_2] = {
            "strategy_position_id": position_id_2,
            "strategy_id": "test-strategy",
            "symbol": "ETHUSDT",
        }
        strategy_position_manager.strategy_positions[position_id_3] = {
            "strategy_position_id": position_id_3,
            "strategy_id": "other-strategy",
            "symbol": "BTCUSDT",
        }
        
        positions = strategy_position_manager.get_strategy_positions_by_strategy("test-strategy")
        
        assert len(positions) == 2
        position_ids = [p["strategy_position_id"] for p in positions]
        assert position_id_1 in position_ids
        assert position_id_2 in position_ids
        assert position_id_3 not in position_ids

    @pytest.mark.asyncio
    async def test_get_open_strategy_positions_by_exchange_key(self, strategy_position_manager):
        """Test getting open positions by exchange key"""
        # Manually add positions
        position_id_1 = str(uuid.uuid4())
        position_id_2 = str(uuid.uuid4())
        
        strategy_position_manager.strategy_positions[position_id_1] = {
            "strategy_position_id": position_id_1,
            "strategy_id": "test-strategy",
            "symbol": "BTCUSDT",
            "side": "LONG",
            "exchange_position_key": "BTCUSDT_LONG",
            "status": "open",
        }
        strategy_position_manager.strategy_positions[position_id_2] = {
            "strategy_position_id": position_id_2,
            "strategy_id": "test-strategy",
            "symbol": "BTCUSDT",
            "side": "LONG",
            "exchange_position_key": "BTCUSDT_LONG",
            "status": "closed",
        }
        
        positions = await strategy_position_manager.get_open_strategy_positions_by_exchange_key("BTCUSDT_LONG")
        
        assert len(positions) == 1
        assert positions[0]["strategy_position_id"] == position_id_1

    def test_get_exchange_position(self, strategy_position_manager):
        """Test getting exchange position"""
        # Manually add exchange position
        exchange_key = "BTCUSDT_LONG"
        strategy_position_manager.exchange_positions[exchange_key] = {
            "exchange_position_key": exchange_key,
            "symbol": "BTCUSDT",
            "side": "LONG",
        }
        
        position = strategy_position_manager.get_exchange_position(exchange_key)
        assert position is not None
        assert position["exchange_position_key"] == exchange_key

    def test_get_contributions(self, strategy_position_manager):
        """Test getting contributions"""
        # Manually add contributions
        exchange_key = "BTCUSDT_LONG"
        contribution = {
            "contribution_id": str(uuid.uuid4()),
            "strategy_position_id": str(uuid.uuid4()),
            "exchange_position_key": exchange_key,
        }
        strategy_position_manager.contributions[exchange_key] = [contribution]
        
        contributions = strategy_position_manager.get_contributions(exchange_key)
        assert len(contributions) == 1
        assert contributions[0]["contribution_id"] == contribution["contribution_id"]

