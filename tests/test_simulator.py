"""
Comprehensive tests for tradeengine/exchange/simulator.py to increase coverage
"""

from unittest.mock import patch

import pytest

from contracts.order import TradeOrder
from tradeengine.exchange.simulator import SimulatorExchange, TradeSimulator


class TestSimulatorExchange:
    """Test SimulatorExchange class"""

    @pytest.mark.asyncio
    async def test_initialize(self):
        """Test simulator exchange initialization"""
        exchange = SimulatorExchange()
        await exchange.initialize()
        # Should complete without error

    @pytest.mark.asyncio
    async def test_close(self):
        """Test simulator exchange close"""
        exchange = SimulatorExchange()
        await exchange.close()
        # Should complete without error

    @pytest.mark.asyncio
    async def test_health_check(self):
        """Test simulator health check"""
        exchange = SimulatorExchange()
        health = await exchange.health_check()
        assert health["status"] == "healthy"
        assert health["type"] == "simulator"

    @pytest.mark.asyncio
    async def test_get_account_info(self):
        """Test getting simulated account info"""
        exchange = SimulatorExchange()
        account_info = await exchange.get_account_info()
        assert "balances" in account_info
        assert "positions" in account_info
        assert "pnl" in account_info
        assert "risk_metrics" in account_info
        assert "BTC" in account_info["balances"]
        assert "USDT" in account_info["balances"]

    @pytest.mark.asyncio
    async def test_get_price_btc(self):
        """Test getting simulated BTC price"""
        exchange = SimulatorExchange()
        price = await exchange.get_price("BTCUSDT")
        # Should be around 45000 with ±2% variation
        assert 44100 < price < 45900

    @pytest.mark.asyncio
    async def test_get_price_eth(self):
        """Test getting simulated ETH price"""
        exchange = SimulatorExchange()
        price = await exchange.get_price("ETHUSDT")
        # Should be around 3000 with ±2% variation
        assert 2940 < price < 3060

    @pytest.mark.asyncio
    async def test_get_price_ada(self):
        """Test getting simulated ADA price"""
        exchange = SimulatorExchange()
        price = await exchange.get_price("ADAUSDT")
        # Should be around 0.5 with ±2% variation
        assert 0.49 < price < 0.51

    @pytest.mark.asyncio
    async def test_get_price_dot(self):
        """Test getting simulated DOT price"""
        exchange = SimulatorExchange()
        price = await exchange.get_price("DOTUSDT")
        # Should be around 7.0 with ±2% variation
        assert 6.86 < price < 7.14

    @pytest.mark.asyncio
    async def test_get_price_unknown_symbol(self):
        """Test getting simulated price for unknown symbol"""
        exchange = SimulatorExchange()
        price = await exchange.get_price("UNKNOWNUSDT")
        # Should default to 100.0 with ±2% variation
        assert 98 < price < 102

    @pytest.mark.asyncio
    async def test_execute_order(self):
        """Test executing order through simulator"""
        exchange = SimulatorExchange()
        order = TradeOrder(
            exchange="binance",
            strategy_id="test_strategy",
            symbol="BTCUSDT",
            side="buy",
            type="market",
            amount=0.1,
            target_price=45000.0,
        )
        result = await exchange.execute_order(order)
        assert "order_id" in result
        assert "status" in result

    @pytest.mark.asyncio
    async def test_cancel_order(self):
        """Test cancelling order in simulator"""
        exchange = SimulatorExchange()
        result = await exchange.cancel_order("BTCUSDT", "test_order_id")
        assert result["success"] is True
        assert result["order_id"] == "test_order_id"
        assert result["symbol"] == "BTCUSDT"
        assert result["status"] == "cancelled"
        assert result["simulated"] is True

    @pytest.mark.asyncio
    async def test_get_order_status(self):
        """Test getting order status from simulator"""
        exchange = SimulatorExchange()
        result = await exchange.get_order_status("BTCUSDT", "test_order_id")
        assert result["order_id"] == "test_order_id"
        assert result["symbol"] == "BTCUSDT"
        assert result["status"] == "filled"
        assert result["simulated"] is True

    @pytest.mark.asyncio
    async def test_get_metrics(self):
        """Test getting simulator metrics"""
        exchange = SimulatorExchange()
        metrics = await exchange.get_metrics()
        assert "orders_executed" in metrics
        assert "total_volume" in metrics
        assert "success_rate" in metrics
        assert "average_execution_time" in metrics


class TestTradeSimulator:
    """Test TradeSimulator class"""

    @pytest.mark.asyncio
    async def test_execute_successful_buy_market_order(self):
        """Test successful execution of buy market order"""
        simulator = TradeSimulator()
        order = TradeOrder(
            exchange="binance",
            strategy_id="test_strategy",
            symbol="BTCUSDT",
            side="buy",
            type="market",
            amount=0.1,
            target_price=45000.0,
        )

        with patch("random.random", return_value=0.5):  # Ensure success
            result = await simulator.execute(order)
            assert result["status"] == "filled"
            assert result["side"] == "buy"
            assert result["type"] == "market"
            assert result["amount"] == 0.1
            assert "fill_price" in result
            assert "total_value" in result
            assert "fees" in result
            assert result["simulated"] is True

    @pytest.mark.asyncio
    async def test_execute_successful_sell_market_order(self):
        """Test successful execution of sell market order"""
        simulator = TradeSimulator()
        order = TradeOrder(
            exchange="binance",
            strategy_id="test_strategy",
            symbol="BTCUSDT",
            side="sell",
            type="market",
            amount=0.1,
            target_price=45000.0,
        )

        with patch("random.random", return_value=0.5):  # Ensure success
            result = await simulator.execute(order)
            assert result["status"] == "filled"
            assert result["side"] == "sell"

    @pytest.mark.asyncio
    async def test_execute_failed_order(self):
        """Test simulated order failure"""
        simulator = TradeSimulator()
        order = TradeOrder(
            exchange="binance",
            strategy_id="test_strategy",
            symbol="BTCUSDT",
            side="buy",
            type="market",
            amount=0.1,
            target_price=45000.0,
        )

        with patch("random.random", return_value=0.99):  # Force failure
            result = await simulator.execute(order)
            assert result["status"] == "failed"
            assert "error" in result
            assert result["error"] == "Simulated execution failure"
            assert result["simulated"] is True

    @pytest.mark.asyncio
    async def test_execute_stop_order(self):
        """Test execution of stop order"""
        simulator = TradeSimulator()
        order = TradeOrder(
            exchange="binance",
            strategy_id="test_strategy",
            symbol="BTCUSDT",
            side="sell",
            type="stop",
            amount=0.1,
            target_price=45000.0,
            stop_loss=44000.0,
        )

        with patch("random.random", return_value=0.5):  # Ensure success
            result = await simulator.execute(order)
            assert result["status"] == "filled"
            # Fill price should be around stop loss price
            assert result["fill_price"] == 44000.0

    @pytest.mark.asyncio
    async def test_execute_stop_limit_order(self):
        """Test execution of stop limit order"""
        simulator = TradeSimulator()
        order = TradeOrder(
            exchange="binance",
            strategy_id="test_strategy",
            symbol="BTCUSDT",
            side="sell",
            type="stop_limit",
            amount=0.1,
            target_price=45000.0,
            stop_loss=44000.0,
        )

        with patch("random.random", return_value=0.5):  # Ensure success
            result = await simulator.execute(order)
            assert result["status"] == "filled"

    @pytest.mark.asyncio
    async def test_execute_take_profit_order(self):
        """Test execution of take profit order"""
        simulator = TradeSimulator()
        order = TradeOrder(
            exchange="binance",
            strategy_id="test_strategy",
            symbol="BTCUSDT",
            side="sell",
            type="take_profit",
            amount=0.1,
            target_price=45000.0,
            take_profit=46000.0,
        )

        with patch("random.random", return_value=0.5):  # Ensure success
            result = await simulator.execute(order)
            assert result["status"] == "filled"
            # Fill price should be around take profit price
            assert result["fill_price"] == 46000.0

    @pytest.mark.asyncio
    async def test_execute_take_profit_limit_order(self):
        """Test execution of take profit limit order"""
        simulator = TradeSimulator()
        order = TradeOrder(
            exchange="binance",
            strategy_id="test_strategy",
            symbol="BTCUSDT",
            side="sell",
            type="take_profit_limit",
            amount=0.1,
            target_price=45000.0,
            take_profit=46000.0,
        )

        with patch("random.random", return_value=0.5):  # Ensure success
            result = await simulator.execute(order)
            assert result["status"] == "filled"

    @pytest.mark.asyncio
    async def test_calculate_fill_price_buy_with_slippage(self):
        """Test fill price calculation for buy order with slippage"""
        simulator = TradeSimulator()
        order = TradeOrder(
            exchange="binance",
            strategy_id="test_strategy",
            symbol="BTCUSDT",
            side="buy",
            type="market",
            amount=0.1,
            target_price=45000.0,
        )
        fill_price = simulator._calculate_fill_price(order)
        # Should be target_price * (1 + slippage)
        expected = 45000.0 * (1 + simulator.simulated_slippage)
        assert fill_price == expected

    @pytest.mark.asyncio
    async def test_calculate_fill_price_sell_with_slippage(self):
        """Test fill price calculation for sell order with slippage"""
        simulator = TradeSimulator()
        order = TradeOrder(
            exchange="binance",
            strategy_id="test_strategy",
            symbol="BTCUSDT",
            side="sell",
            type="market",
            amount=0.1,
            target_price=45000.0,
        )
        fill_price = simulator._calculate_fill_price(order)
        # Should be target_price * (1 - slippage)
        expected = 45000.0 * (1 - simulator.simulated_slippage)
        assert fill_price == expected

    @pytest.mark.asyncio
    async def test_generate_fills(self):
        """Test fill data generation"""
        simulator = TradeSimulator()
        order = TradeOrder(
            exchange="binance",
            strategy_id="test_strategy",
            symbol="BTCUSDT",
            side="buy",
            type="market",
            amount=0.1,
            target_price=45000.0,
        )
        fills = simulator._generate_fills(order, 45000.0)
        assert len(fills) == 1
        assert "price" in fills[0]
        assert "qty" in fills[0]
        assert "commission" in fills[0]
        assert "commissionAsset" in fills[0]
        assert "tradeId" in fills[0]
        assert fills[0]["commissionAsset"] == "USDT"

    @pytest.mark.asyncio
    async def test_simulator_initialization(self):
        """Test simulator initialization with default values"""
        simulator = TradeSimulator()
        assert simulator.simulated_slippage is not None
        assert simulator.success_rate is not None
        assert simulator.delay_ms is not None
