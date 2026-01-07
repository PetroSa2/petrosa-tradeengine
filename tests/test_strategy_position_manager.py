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
        price=50000.0,
        current_price=50000.0,
        quantity=0.001,
        confidence=0.85,
        timeframe="1h",
        take_profit=52000.0,
        stop_loss=48000.0,
        source="test",
        strategy="test-strategy-1",
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

    def test_get_contributions_empty(self, strategy_position_manager):
        """Test getting contributions for non-existent exchange key"""
        contributions = strategy_position_manager.get_contributions("NONEXISTENT_LONG")
        assert contributions == []

    @pytest.mark.asyncio
    async def test_close_strategy_position_not_found(self, strategy_position_manager):
        """Test closing a non-existent strategy position"""
        result = await strategy_position_manager.close_strategy_position(
            strategy_position_id="nonexistent-id",
            exit_price=51000.0,
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_close_strategy_position_partial(self, strategy_position_manager, sample_signal, sample_order, sample_execution_result):
        """Test closing a strategy position partially"""
        with patch('shared.mysql_client.position_client') as mock_client:
            mock_client.create_position = AsyncMock()
            mock_client.update_position = AsyncMock()
            
            position_id = await strategy_position_manager.create_strategy_position(
                signal=sample_signal,
                order=sample_order,
                execution_result=sample_execution_result,
            )
            
            # Close partially
            result = await strategy_position_manager.close_strategy_position(
                strategy_position_id=position_id,
                exit_price=51000.0,
                exit_quantity=0.0005,  # Half of 0.001
            )
            
            assert result is not None
            assert "realized_pnl" in result
            position = strategy_position_manager.get_strategy_position(position_id)
            assert position["status"] == "partial"

    @pytest.mark.asyncio
    async def test_close_strategy_position_long_pnl(self, strategy_position_manager, sample_signal, sample_order, sample_execution_result):
        """Test closing LONG position PnL calculation"""
        with patch('shared.mysql_client.position_client') as mock_client:
            mock_client.create_position = AsyncMock()
            mock_client.update_position = AsyncMock()
            
            position_id = await strategy_position_manager.create_strategy_position(
                signal=sample_signal,
                order=sample_order,
                execution_result=sample_execution_result,
            )
            
            # Close at higher price (profit for LONG)
            result = await strategy_position_manager.close_strategy_position(
                strategy_position_id=position_id,
                exit_price=51000.0,  # Higher than entry 50000.0
                close_reason="take_profit",
            )
            
            assert result["realized_pnl"] > 0  # Profit for LONG

    @pytest.mark.asyncio
    async def test_close_strategy_position_short_pnl(self, strategy_position_manager):
        """Test closing SHORT position PnL calculation"""
        from contracts.signal import Signal, TimeInForce
        from contracts.order import TradeOrder, OrderSide, OrderType
        
        short_signal = Signal(
            signal_id="test_signal_short",
            strategy_id="test-strategy-1",
            symbol="BTCUSDT",
            action="sell",
            price=50000.0,
            current_price=50000.0,
            quantity=0.001,
            confidence=0.85,
            timeframe="1h",
            source="test",
            strategy="test-strategy-1",
            order_type="market",
            time_in_force=TimeInForce.GTC,
        )
        
        short_order = TradeOrder(
            symbol="BTCUSDT",
            side=OrderSide.SELL,
            type=OrderType.MARKET,
            amount=0.001,
            target_price=50000.0,
        )
        
        execution_result = {
            "status": "filled",
            "order_id": "test_order_short",
            "fill_price": 50000.0,
            "amount": 0.001,
        }
        
        with patch('shared.mysql_client.position_client') as mock_client:
            mock_client.create_position = AsyncMock()
            mock_client.update_position = AsyncMock()
            
            position_id = await strategy_position_manager.create_strategy_position(
                signal=short_signal,
                order=short_order,
                execution_result=execution_result,
            )
            
            # Close at lower price (profit for SHORT)
            result = await strategy_position_manager.close_strategy_position(
                strategy_position_id=position_id,
                exit_price=49000.0,  # Lower than entry 50000.0
                close_reason="take_profit",
            )
            
            assert result["realized_pnl"] > 0  # Profit for SHORT

    @pytest.mark.asyncio
    async def test_update_exchange_position_new(self, strategy_position_manager):
        """Test _update_exchange_position creating new position"""
        with patch('shared.mysql_client.position_client') as mock_client:
            mock_client.create_position = AsyncMock()
            
            await strategy_position_manager._update_exchange_position(
                exchange_position_key="BTCUSDT_LONG",
                symbol="BTCUSDT",
                side="LONG",
                quantity=0.001,
                price=50000.0,
                strategy_id="test-strategy",
            )
            
            assert "BTCUSDT_LONG" in strategy_position_manager.exchange_positions
            position = strategy_position_manager.exchange_positions["BTCUSDT_LONG"]
            assert position["current_quantity"] == 0.001
            assert position["weighted_avg_price"] == 50000.0
            assert "test-strategy" in position["contributing_strategies"]

    @pytest.mark.asyncio
    async def test_update_exchange_position_existing(self, strategy_position_manager):
        """Test _update_exchange_position updating existing position"""
        with patch('shared.mysql_client.position_client') as mock_client:
            mock_client.create_position = AsyncMock()
            
            # Create initial position
            strategy_position_manager.exchange_positions["BTCUSDT_LONG"] = {
                "exchange_position_key": "BTCUSDT_LONG",
                "symbol": "BTCUSDT",
                "side": "LONG",
                "current_quantity": 0.001,
                "weighted_avg_price": 50000.0,
                "contributing_strategies": ["strategy-1"],
                "total_contributions": 1,
            }
            
            # Add more to position
            await strategy_position_manager._update_exchange_position(
                exchange_position_key="BTCUSDT_LONG",
                symbol="BTCUSDT",
                side="LONG",
                quantity=0.002,
                price=51000.0,
                strategy_id="strategy-2",
            )
            
            position = strategy_position_manager.exchange_positions["BTCUSDT_LONG"]
            assert position["current_quantity"] == 0.003  # 0.001 + 0.002
            assert position["total_contributions"] == 2
            assert "strategy-2" in position["contributing_strategies"]
            # Weighted avg: (0.001*50000 + 0.002*51000) / 0.003 = 50666.67
            assert position["weighted_avg_price"] == pytest.approx(50666.67, rel=0.01)

    @pytest.mark.asyncio
    async def test_reduce_exchange_position(self, strategy_position_manager):
        """Test _reduce_exchange_position reducing quantity"""
        with patch('shared.mysql_client.position_client') as mock_client:
            mock_client.create_position = AsyncMock()
            
            # Create position
            strategy_position_manager.exchange_positions["BTCUSDT_LONG"] = {
                "exchange_position_key": "BTCUSDT_LONG",
                "symbol": "BTCUSDT",
                "side": "LONG",
                "current_quantity": 0.003,
                "weighted_avg_price": 50000.0,
                "status": "open",
            }
            
            # Reduce position
            await strategy_position_manager._reduce_exchange_position(
                exchange_position_key="BTCUSDT_LONG",
                quantity=0.001,
                price=51000.0,
            )
            
            position = strategy_position_manager.exchange_positions["BTCUSDT_LONG"]
            assert position["current_quantity"] == 0.002  # 0.003 - 0.001
            assert position["status"] == "open"

    @pytest.mark.asyncio
    async def test_reduce_exchange_position_to_zero(self, strategy_position_manager):
        """Test _reduce_exchange_position closing position"""
        with patch('shared.mysql_client.position_client') as mock_client:
            mock_client.create_position = AsyncMock()
            
            # Create position
            strategy_position_manager.exchange_positions["BTCUSDT_LONG"] = {
                "exchange_position_key": "BTCUSDT_LONG",
                "symbol": "BTCUSDT",
                "side": "LONG",
                "current_quantity": 0.001,
                "weighted_avg_price": 50000.0,
                "status": "open",
            }
            
            # Close position
            await strategy_position_manager._reduce_exchange_position(
                exchange_position_key="BTCUSDT_LONG",
                quantity=0.001,
                price=51000.0,
            )
            
            position = strategy_position_manager.exchange_positions["BTCUSDT_LONG"]
            assert position["current_quantity"] == 0.0
            assert position["status"] == "closed"

    @pytest.mark.asyncio
    async def test_reduce_exchange_position_not_found(self, strategy_position_manager):
        """Test _reduce_exchange_position when position not found"""
        # Should not raise error
        await strategy_position_manager._reduce_exchange_position(
            exchange_position_key="NONEXISTENT_LONG",
            quantity=0.001,
            price=50000.0,
        )
        # Should complete without error

    @pytest.mark.asyncio
    async def test_create_contribution(self, strategy_position_manager):
        """Test _create_contribution creating contribution record"""
        with patch('shared.mysql_client.position_client') as mock_client:
            mock_client.create_position = AsyncMock()
            
            # Set up exchange position
            strategy_position_manager.exchange_positions["BTCUSDT_LONG"] = {
                "exchange_position_key": "BTCUSDT_LONG",
                "current_quantity": 0.001,
                "total_contributions": 1,
            }
            
            await strategy_position_manager._create_contribution(
                strategy_position_id="strat_pos_123",
                exchange_position_key="BTCUSDT_LONG",
                strategy_id="test-strategy",
                symbol="BTCUSDT",
                position_side="LONG",
                quantity=0.001,
                price=50000.0,
            )
            
            assert "BTCUSDT_LONG" in strategy_position_manager.contributions
            contributions = strategy_position_manager.contributions["BTCUSDT_LONG"]
            assert len(contributions) == 1
            assert contributions[0]["strategy_position_id"] == "strat_pos_123"

    @pytest.mark.asyncio
    async def test_create_contribution_new_exchange_position(self, strategy_position_manager):
        """Test _create_contribution for new exchange position"""
        with patch('shared.mysql_client.position_client') as mock_client:
            mock_client.create_position = AsyncMock()
            
            # No exchange position exists yet
            await strategy_position_manager._create_contribution(
                strategy_position_id="strat_pos_456",
                exchange_position_key="ETHUSDT_LONG",
                strategy_id="test-strategy",
                symbol="ETHUSDT",
                position_side="LONG",
                quantity=0.01,
                price=3000.0,
            )
            
            assert "ETHUSDT_LONG" in strategy_position_manager.contributions
            contributions = strategy_position_manager.contributions["ETHUSDT_LONG"]
            assert len(contributions) == 1
            assert contributions[0]["position_sequence"] == 1  # First contribution

    @pytest.mark.asyncio
    async def test_close_contribution(self, strategy_position_manager):
        """Test _close_contribution closing contribution record"""
        with patch('shared.mysql_client.position_client') as mock_client:
            mock_client.update_position = AsyncMock()
            
            await strategy_position_manager._close_contribution(
                strategy_position_id="strat_pos_123",
                exit_price=51000.0,
                pnl=10.0,
                pnl_pct=2.0,
                close_reason="take_profit",
            )
            
            # Should complete without error
            mock_client.update_position.assert_called_once()

    @pytest.mark.asyncio
    async def test_persist_exchange_position(self, strategy_position_manager):
        """Test _persist_exchange_position persisting to Data Manager"""
        with patch('shared.mysql_client.position_client') as mock_client:
            mock_client.create_position = AsyncMock()
            
            # Set up exchange position
            strategy_position_manager.exchange_positions["BTCUSDT_LONG"] = {
                "exchange_position_key": "BTCUSDT_LONG",
                "symbol": "BTCUSDT",
                "side": "LONG",
            }
            
            await strategy_position_manager._persist_exchange_position("BTCUSDT_LONG")
            
            mock_client.create_position.assert_called_once()

    @pytest.mark.asyncio
    async def test_persist_strategy_position_error(self, strategy_position_manager):
        """Test _persist_strategy_position error handling"""
        with patch('shared.mysql_client.position_client') as mock_client:
            mock_client.create_position = AsyncMock(side_effect=Exception("DB error"))
            
            position = {
                "strategy_position_id": "test_123",
                "symbol": "BTCUSDT",
            }
            
            # Should not raise, just log error
            await strategy_position_manager._persist_strategy_position(position)

    @pytest.mark.asyncio
    async def test_update_strategy_position_closure_error(self, strategy_position_manager):
        """Test _update_strategy_position_closure error handling"""
        with patch('tradeengine.strategy_position_manager.position_client') as mock_client:
            mock_client.update_position = AsyncMock(side_effect=Exception("DB error"))
            
            position = {
                "strategy_position_id": "test_123",
                "status": "closed",
            }
            
            # Should not raise, just log error
            await strategy_position_manager._update_strategy_position_closure("test_123", position)

    @pytest.mark.asyncio
    async def test_update_exchange_position_new(self, strategy_position_manager):
        """Test _update_exchange_position creating new position"""
        with patch('shared.mysql_client.position_client') as mock_client:
            mock_client.create_position = AsyncMock()
            
            await strategy_position_manager._update_exchange_position(
                exchange_position_key="BTCUSDT_LONG",
                symbol="BTCUSDT",
                side="LONG",
                quantity=0.001,
                price=50000.0,
                strategy_id="test-strategy",
            )
            
            assert "BTCUSDT_LONG" in strategy_position_manager.exchange_positions
            position = strategy_position_manager.exchange_positions["BTCUSDT_LONG"]
            assert position["current_quantity"] == 0.001
            assert position["weighted_avg_price"] == 50000.0
            assert "test-strategy" in position["contributing_strategies"]

    @pytest.mark.asyncio
    async def test_update_exchange_position_existing(self, strategy_position_manager):
        """Test _update_exchange_position updating existing position"""
        with patch('shared.mysql_client.position_client') as mock_client:
            mock_client.create_position = AsyncMock()
            
            # Create initial position
            await strategy_position_manager._update_exchange_position(
                exchange_position_key="BTCUSDT_LONG",
                symbol="BTCUSDT",
                side="LONG",
                quantity=0.001,
                price=50000.0,
                strategy_id="test-strategy-1",
            )
            
            # Update with additional quantity
            await strategy_position_manager._update_exchange_position(
                exchange_position_key="BTCUSDT_LONG",
                symbol="BTCUSDT",
                side="LONG",
                quantity=0.002,
                price=51000.0,
                strategy_id="test-strategy-2",
            )
            
            position = strategy_position_manager.exchange_positions["BTCUSDT_LONG"]
            assert position["current_quantity"] == 0.003  # 0.001 + 0.002
            # Weighted avg: (0.001*50000 + 0.002*51000) / 0.003 = 50666.67
            assert position["weighted_avg_price"] == pytest.approx(50666.67, rel=0.01)
            assert position["total_contributions"] == 2
            assert "test-strategy-1" in position["contributing_strategies"]
            assert "test-strategy-2" in position["contributing_strategies"]

    @pytest.mark.asyncio
    async def test_reduce_exchange_position(self, strategy_position_manager):
        """Test _reduce_exchange_position"""
        with patch('shared.mysql_client.position_client') as mock_client:
            mock_client.create_position = AsyncMock()
            
            # Create position
            await strategy_position_manager._update_exchange_position(
                exchange_position_key="BTCUSDT_LONG",
                symbol="BTCUSDT",
                side="LONG",
                quantity=0.001,
                price=50000.0,
                strategy_id="test-strategy",
            )
            
            # Reduce position
            await strategy_position_manager._reduce_exchange_position(
                exchange_position_key="BTCUSDT_LONG",
                quantity=0.0005,
                price=51000.0,
            )
            
            position = strategy_position_manager.exchange_positions["BTCUSDT_LONG"]
            assert position["current_quantity"] == 0.0005  # 0.001 - 0.0005

    @pytest.mark.asyncio
    async def test_reduce_exchange_position_to_zero(self, strategy_position_manager):
        """Test _reduce_exchange_position closing position"""
        with patch('shared.mysql_client.position_client') as mock_client:
            mock_client.create_position = AsyncMock()
            
            # Create position
            await strategy_position_manager._update_exchange_position(
                exchange_position_key="BTCUSDT_LONG",
                symbol="BTCUSDT",
                side="LONG",
                quantity=0.001,
                price=50000.0,
                strategy_id="test-strategy",
            )
            
            # Close position completely
            await strategy_position_manager._reduce_exchange_position(
                exchange_position_key="BTCUSDT_LONG",
                quantity=0.001,
                price=51000.0,
            )
            
            position = strategy_position_manager.exchange_positions["BTCUSDT_LONG"]
            assert position["current_quantity"] == 0.0
            assert position["status"] == "closed"

    @pytest.mark.asyncio
    async def test_reduce_exchange_position_not_found(self, strategy_position_manager):
        """Test _reduce_exchange_position when position not found"""
        # Should not raise error, just log warning
        await strategy_position_manager._reduce_exchange_position(
            exchange_position_key="NONEXISTENT_LONG",
            quantity=0.001,
            price=50000.0,
        )
        # Should complete without error

    @pytest.mark.asyncio
    async def test_create_contribution(self, strategy_position_manager):
        """Test _create_contribution"""
        with patch('shared.mysql_client.position_client') as mock_client:
            mock_client.create_position = AsyncMock()
            
            # Create exchange position first
            await strategy_position_manager._update_exchange_position(
                exchange_position_key="BTCUSDT_LONG",
                symbol="BTCUSDT",
                side="LONG",
                quantity=0.001,
                price=50000.0,
                strategy_id="test-strategy",
            )
            
            # Create contribution
            await strategy_position_manager._create_contribution(
                strategy_position_id="strat_pos_123",
                exchange_position_key="BTCUSDT_LONG",
                strategy_id="test-strategy",
                symbol="BTCUSDT",
                position_side="LONG",
                quantity=0.001,
                price=50000.0,
            )
            
            assert "BTCUSDT_LONG" in strategy_position_manager.contributions
            contributions = strategy_position_manager.contributions["BTCUSDT_LONG"]
            assert len(contributions) == 1
            assert contributions[0]["strategy_position_id"] == "strat_pos_123"

    @pytest.mark.asyncio
    async def test_close_contribution(self, strategy_position_manager):
        """Test _close_contribution"""
        with patch('shared.mysql_client.position_client') as mock_client:
            mock_client.update_position = AsyncMock()
            
            await strategy_position_manager._close_contribution(
                strategy_position_id="strat_pos_123",
                exit_price=51000.0,
                pnl=10.0,
                pnl_pct=2.0,
                close_reason="take_profit",
            )
            
            # Should complete without error
            mock_client.update_position.assert_called_once()

    @pytest.mark.asyncio
    async def test_persist_strategy_position(self, strategy_position_manager):
        """Test _persist_strategy_position"""
        with patch('shared.mysql_client.position_client') as mock_client:
            mock_client.create_position = AsyncMock()
            
            position = {
                "strategy_position_id": "test_pos_123",
                "strategy_id": "test-strategy",
                "symbol": "BTCUSDT",
            }
            
            await strategy_position_manager._persist_strategy_position(position)
            mock_client.create_position.assert_called_once_with(position)

    @pytest.mark.asyncio
    async def test_update_strategy_position_closure(self, strategy_position_manager):
        """Test _update_strategy_position_closure"""
        with patch('shared.mysql_client.position_client') as mock_client:
            mock_client.update_position = AsyncMock()
            
            position = {
                "strategy_position_id": "test_pos_123",
                "status": "closed",
            }
            
            await strategy_position_manager._update_strategy_position_closure("test_pos_123", position)
            mock_client.update_position.assert_called_once_with("test_pos_123", position)

    @pytest.mark.asyncio
    async def test_persist_exchange_position(self, strategy_position_manager):
        """Test _persist_exchange_position"""
        with patch('shared.mysql_client.position_client') as mock_client:
            mock_client.create_position = AsyncMock()
            
            # Create exchange position
            strategy_position_manager.exchange_positions["BTCUSDT_LONG"] = {
                "exchange_position_key": "BTCUSDT_LONG",
                "symbol": "BTCUSDT",
                "side": "LONG",
            }
            
            await strategy_position_manager._persist_exchange_position("BTCUSDT_LONG")
            mock_client.create_position.assert_called_once()

