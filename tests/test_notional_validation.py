"""
Test suite for Binance Futures MIN_NOTIONAL validation

Tests the implementation of MIN_NOTIONAL validation to prevent API error -4164.
"""

from unittest.mock import MagicMock

import pytest

from contracts.order import OrderStatus, TradeOrder
from tradeengine.exchange.binance import BinanceFuturesExchange


@pytest.fixture
def mock_client():
    """Create a mock Binance client"""
    client = MagicMock()
    client.futures_ping = MagicMock()
    client.futures_exchange_info = MagicMock(
        return_value={
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "baseAsset": "BTC",
                    "quoteAsset": "USDT",
                    "status": "TRADING",
                    "filters": [
                        {
                            "filterType": "LOT_SIZE",
                            "minQty": "0.001",
                            "maxQty": "1000",
                            "stepSize": "0.001",
                        },
                        {
                            "filterType": "MIN_NOTIONAL",
                            "notional": "100.0",
                        },
                        {
                            "filterType": "PRICE_FILTER",
                            "minPrice": "0.01",
                            "maxPrice": "1000000",
                            "tickSize": "0.01",
                        },
                    ],
                }
            ]
        }
    )
    client.futures_symbol_ticker = MagicMock(return_value={"price": "50000.00"})
    return client


@pytest.fixture
def exchange(mock_client):
    """Create and initialize a BinanceFuturesExchange instance"""
    exchange = BinanceFuturesExchange()
    exchange.client = mock_client
    exchange.initialized = True
    exchange.symbol_info = {
        "BTCUSDT": {
            "baseAsset": "BTC",
            "quoteAsset": "USDT",
            "status": "TRADING",
            "filters": [
                {
                    "filterType": "LOT_SIZE",
                    "minQty": "0.001",
                    "maxQty": "1000",
                    "stepSize": "0.001",
                },
                {
                    "filterType": "MIN_NOTIONAL",
                    "notional": "100.0",
                },
                {
                    "filterType": "PRICE_FILTER",
                    "minPrice": "0.01",
                    "maxPrice": "1000000",
                    "tickSize": "0.01",
                },
            ],
        }
    }
    return exchange


class TestNotionalValidation:
    """Test MIN_NOTIONAL validation logic"""

    @pytest.mark.asyncio
    async def test_validate_notional_above_minimum(self, exchange):
        """Test order with notional value above minimum passes validation"""
        order = TradeOrder(
            symbol="BTCUSDT",
            type="limit",
            side="buy",
            amount=0.003,  # 0.003 BTC * $50,000 = $150 > $100 min
            target_price=50000.0,
            order_id="test-order-1",
            status=OrderStatus.PENDING,
            time_in_force="GTC",
            reduce_only=False,
        )

        # Should not raise an exception
        await exchange._validate_notional(order, 50000.0)

    @pytest.mark.asyncio
    async def test_validate_notional_below_minimum_fails(self, exchange):
        """Test order with notional value below minimum fails validation"""
        order = TradeOrder(
            symbol="BTCUSDT",
            type="limit",
            side="buy",
            amount=0.001,  # 0.001 BTC * $50,000 = $50 < $100 min
            target_price=50000.0,
            order_id="test-order-2",
            status=OrderStatus.PENDING,
            time_in_force="GTC",
            reduce_only=False,
        )

        # Should raise ValueError with helpful message
        with pytest.raises(
            ValueError,
            match=r"Order notional \$50\.00 is below minimum \$100\.00",
        ):
            await exchange._validate_notional(order, 50000.0)

    @pytest.mark.asyncio
    async def test_reduce_only_exempt_from_notional(self, exchange):
        """Test reduce-only orders are exempt from MIN_NOTIONAL validation"""
        order = TradeOrder(
            symbol="BTCUSDT",
            type="market",
            side="sell",
            amount=0.0001,  # Very small amount: 0.0001 BTC * $50,000 = $5 < $100
            order_id="test-order-3",
            status=OrderStatus.PENDING,
            reduce_only=True,  # Reduce-only flag set
        )

        # Should not raise an exception even though notional is below minimum
        await exchange._validate_notional(order, 50000.0)

    @pytest.mark.asyncio
    async def test_get_current_price(self, exchange):
        """Test getting current price for market orders"""
        price = await exchange._get_current_price("BTCUSDT")
        assert price == 50000.0

    @pytest.mark.asyncio
    async def test_validate_order_limit_with_notional_check(self, exchange):
        """Test limit order validation includes notional check"""
        order = TradeOrder(
            symbol="BTCUSDT",
            type="limit",
            side="buy",
            amount=0.003,
            target_price=50000.0,
            order_id="test-order-4",
            status=OrderStatus.PENDING,
            time_in_force="GTC",
            reduce_only=False,
        )

        # Should pass validation (notional = $150 > $100 min)
        await exchange._validate_order(order)

    @pytest.mark.asyncio
    async def test_validate_order_limit_below_notional_fails(self, exchange):
        """Test limit order below MIN_NOTIONAL fails validation"""
        order = TradeOrder(
            symbol="BTCUSDT",
            type="limit",
            side="buy",
            amount=0.001,
            target_price=50000.0,
            order_id="test-order-5",
            status=OrderStatus.PENDING,
            time_in_force="GTC",
            reduce_only=False,
        )

        # Should fail validation (notional = $50 < $100 min)
        with pytest.raises(ValueError, match=r"Order notional.*is below minimum"):
            await exchange._validate_order(order)

    @pytest.mark.asyncio
    async def test_validate_order_market_fetches_price(self, exchange):
        """Test market order validation fetches current price"""
        order = TradeOrder(
            symbol="BTCUSDT",
            type="market",
            side="buy",
            amount=0.003,  # 0.003 * $50,000 = $150 > $100 min
            order_id="test-order-6",
            status=OrderStatus.PENDING,
            reduce_only=False,
        )

        # Should pass validation after fetching current price
        await exchange._validate_order(order)

    @pytest.mark.asyncio
    async def test_get_min_order_amount_returns_correct_values(self, exchange):
        """Test get_min_order_amount returns correct filter values"""
        min_info = exchange.get_min_order_amount("BTCUSDT")

        assert min_info["symbol"] == "BTCUSDT"
        assert min_info["min_qty"] == 0.001
        assert min_info["min_notional"] == 100.0
        assert min_info["step_size"] == 0.001
        assert min_info["base_asset"] == "BTC"
        assert min_info["quote_asset"] == "USDT"

    @pytest.mark.asyncio
    async def test_default_min_notional_when_filter_missing(self):
        """Test default MIN_NOTIONAL of $20 is used when filter is missing"""
        # Create exchange with symbol info missing MIN_NOTIONAL filter
        exchange = BinanceFuturesExchange()
        exchange.symbol_info = {
            "TESTUSDT": {
                "baseAsset": "TEST",
                "quoteAsset": "USDT",
                "status": "TRADING",
                "filters": [
                    {
                        "filterType": "LOT_SIZE",
                        "minQty": "0.001",
                        "maxQty": "1000",
                        "stepSize": "0.001",
                    },
                    # No MIN_NOTIONAL filter
                ],
            }
        }

        min_info = exchange.get_min_order_amount("TESTUSDT")
        assert min_info["min_notional"] == 20.0  # Default value (Binance standard)

    @pytest.mark.asyncio
    async def test_execute_market_order_with_reduce_only(self, exchange, mock_client):
        """Test market order execution includes reduceOnly parameter"""
        mock_client.futures_create_order = MagicMock(
            return_value={
                "orderId": 12345,
                "status": "FILLED",
                "side": "BUY",
                "type": "MARKET",
                "fills": [],
            }
        )

        order = TradeOrder(
            symbol="BTCUSDT",
            type="market",
            side="buy",
            amount=0.003,
            order_id="test-order-7",
            status=OrderStatus.PENDING,
            reduce_only=True,
        )

        result = await exchange._execute_market_order(order)

        # Verify reduceOnly was passed to API
        call_kwargs = mock_client.futures_create_order.call_args[1]
        assert "reduceOnly" in call_kwargs
        assert call_kwargs["reduceOnly"] is True
        assert result["orderId"] == 12345

    @pytest.mark.asyncio
    async def test_execute_limit_order_with_reduce_only(self, exchange, mock_client):
        """Test limit order execution includes reduceOnly parameter"""
        mock_client.futures_create_order = MagicMock(
            return_value={
                "orderId": 12346,
                "status": "NEW",
                "side": "SELL",
                "type": "LIMIT",
                "fills": [],
            }
        )

        order = TradeOrder(
            symbol="BTCUSDT",
            type="limit",
            side="sell",
            amount=0.003,
            target_price=51000.0,
            order_id="test-order-8",
            status=OrderStatus.PENDING,
            time_in_force="GTC",
            reduce_only=True,
        )

        result = await exchange._execute_limit_order(order)

        # Verify reduceOnly was passed to API
        call_kwargs = mock_client.futures_create_order.call_args[1]
        assert "reduceOnly" in call_kwargs
        assert call_kwargs["reduceOnly"] is True
        assert result["orderId"] == 12346

    @pytest.mark.asyncio
    async def test_notional_validation_error_message(self, exchange):
        """Test error message provides helpful guidance"""
        order = TradeOrder(
            symbol="BTCUSDT",
            type="limit",
            side="buy",
            amount=0.0005,  # $25 notional
            target_price=50000.0,
            order_id="test-order-9",
            status=OrderStatus.PENDING,
            time_in_force="GTC",
            reduce_only=False,
        )

        with pytest.raises(ValueError) as exc_info:
            await exchange._validate_notional(order, 50000.0)

        error_message = str(exc_info.value)
        assert "$25.00" in error_message
        assert "$100.00" in error_message
        assert "BTCUSDT" in error_message
        assert "reduce_only" in error_message


class TestTradeOrderContract:
    """Test TradeOrder contract with reduce_only field"""

    def test_trade_order_default_reduce_only(self):
        """Test reduce_only defaults to False"""
        order = TradeOrder(
            symbol="BTCUSDT",
            type="market",
            side="buy",
            amount=0.01,
            order_id="test-order-10",
            status=OrderStatus.PENDING,
        )

        assert order.reduce_only is False

    def test_trade_order_reduce_only_set_true(self):
        """Test reduce_only can be set to True"""
        order = TradeOrder(
            symbol="BTCUSDT",
            type="market",
            side="sell",
            amount=0.01,
            order_id="test-order-11",
            status=OrderStatus.PENDING,
            reduce_only=True,
        )

        assert order.reduce_only is True

    def test_trade_order_serialization_includes_reduce_only(self):
        """Test order serialization includes reduce_only field"""
        order = TradeOrder(
            symbol="BTCUSDT",
            type="market",
            side="buy",
            amount=0.01,
            order_id="test-order-12",
            status=OrderStatus.PENDING,
            reduce_only=True,
        )

        order_dict = order.model_dump()
        assert "reduce_only" in order_dict
        assert order_dict["reduce_only"] is True
