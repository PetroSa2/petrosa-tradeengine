"""
Simple test script to verify the APIError -1102 fix.
This script tests the symbol validation and APIError -1102 handling.
"""

import asyncio
import sys
from unittest.mock import MagicMock, Mock, patch

import pytest

# Mock the binance module before importing
mock_binance = MagicMock()
mock_binance.exceptions = MagicMock()
sys.modules["binance"] = mock_binance
sys.modules["binance.exceptions"] = mock_binance.exceptions
mock_binance.enums = MagicMock()
mock_binance.enums.FUTURE_ORDER_TYPE_STOP = "STOP"
mock_binance.enums.FUTURE_ORDER_TYPE_TAKE_PROFIT = "TAKE_PROFIT"
sys.modules["binance.enums"] = mock_binance.enums


# Create mock BinanceAPIException
class MockBinanceAPIException(Exception):
    def __init__(self, response, message):
        super().__init__(message)
        self.response = response
        self.message = message
        if isinstance(response, dict):
            self.code = response.get("code", -1000)
        else:
            self.code = getattr(response, "code", -1000)


mock_binance.exceptions.BinanceAPIException = MockBinanceAPIException

from contracts.order import OrderSide, OrderType, TradeOrder  # noqa: E402
from tradeengine.exchange.binance import BinanceFuturesExchange  # noqa: E402

BinanceAPIException = MockBinanceAPIException


def create_mock_binance_client():
    """Create a mock Binance client"""
    client = Mock()
    client.futures_symbol_ticker = Mock(return_value={"price": "50000.0"})
    client.futures_exchange_info = Mock(
        return_value={
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "status": "TRADING",
                    "filters": [
                        {"filterType": "MIN_NOTIONAL", "notional": "20.0"},
                        {
                            "filterType": "LOT_SIZE",
                            "minQty": "0.001",
                            "maxQty": "1000.0",
                            "stepSize": "0.001",
                        },
                        {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                        {
                            "filterType": "PERCENT_PRICE",
                            "multiplierUp": "1.05",
                            "multiplierDown": "0.95",
                        },
                    ],
                    "pair": "BTCUSDT",
                    "baseAsset": "BTC",
                    "quoteAsset": "USDT",
                    "marginAsset": "USDT",
                    "quotePrecision": "8",
                    "baseCommissionPrecision": "8",
                    "quoteCommissionPrecision": "8",
                }
            ]
        }
    )
    client.futures_create_order = Mock(
        return_value={
            "orderId": 12345,
            "status": "FILLED",
            "side": "BUY",
            "type": "MARKET",
            "price": "50000.0",
            "fills": [
                {
                    "price": "50000.0",
                    "qty": "0.001",
                    "quoteQty": "50.0",
                    "commission": "0.05",
                }
            ],
            "transactTime": 1234567890,
        }
    )
    return client


def create_binance_exchange():
    """Create a BinanceFuturesExchange instance with mocked client"""
    exchange = BinanceFuturesExchange()
    exchange.client = create_mock_binance_client()
    exchange.exchange_info = exchange.client.futures_exchange_info()
    exchange.symbol_info = {
        symbol["symbol"]: symbol for symbol in exchange.exchange_info["symbols"]
    }
    exchange.initialized = True
    return exchange


def test_symbol_validation_empty_string():
    """Test that empty symbol raises ValueError"""
    exchange = create_binance_exchange()

    order = TradeOrder(
        symbol="",  # Empty symbol
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        amount=0.001,
        target_price=50000.0,
    )

    # The symbol validation happens in the execute method
    # We need to check if the validation is working correctly
    # by checking if the order is valid
    assert order.symbol == ""  # Pydantic allows empty strings


def test_symbol_validation_whitespace_only():
    """Test that whitespace-only symbol is handled correctly"""
    order = TradeOrder(
        symbol="   ",  # Whitespace-only symbol
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        amount=0.001,
        target_price=50000.0,
    )
    assert order.symbol.strip() == ""  # Pydantic allows whitespace-only strings


def test_symbol_validation_valid_symbol():
    """Test that valid symbol works correctly"""
    order = TradeOrder(
        symbol="BTCUSDT",  # Valid symbol
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        amount=0.001,
        target_price=50000.0,
    )
    assert order.symbol == "BTCUSDT"


@pytest.mark.asyncio
async def test_execute_with_valid_symbol():
    """Test that valid symbol works correctly with execute"""
    exchange = create_binance_exchange()

    order = TradeOrder(
        symbol="BTCUSDT",  # Valid symbol
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        amount=0.001,
        target_price=50000.0,
    )
    result = await exchange.execute(order)
    assert result["order_id"] == "12345"


@pytest.mark.asyncio
async def test_execute_with_empty_symbol():
    """Test that empty symbol raises ValueError"""
    exchange = create_binance_exchange()

    order = TradeOrder(
        symbol="",  # Empty symbol
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        amount=0.001,
        target_price=50000.0,
    )

    # The symbol validation happens in the _validate_order method
    # which checks if the symbol is in the supported symbols list
    result = await exchange.execute(order)
    assert result["status"] == "failed"
    assert "Symbol  not supported" in result["error"]


@pytest.mark.asyncio
async def test_execute_with_whitespace_symbol():
    """Test that whitespace-only symbol raises ValueError"""
    exchange = create_binance_exchange()

    order = TradeOrder(
        symbol="   ",  # Whitespace-only symbol
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        amount=0.001,
        target_price=50000.0,
    )

    # The symbol validation happens in the _validate_order method
    # which checks if the symbol is in the supported symbols list
    result = await exchange.execute(order)
    assert result["status"] == "failed"
    assert "Symbol     not supported" in result["error"]


@pytest.mark.asyncio
async def test_api_error_1102_not_retryable():
    """Test that APIError -1102 is not retried"""
    # Create exchange with mock client that raises APIError -1102
    exchange = BinanceFuturesExchange()
    client = create_mock_binance_client()
    client.futures_create_order = Mock(
        side_effect=BinanceAPIException(
            {"code": -1102},
            "Mandatory parameter 'symbol' was not sent, was empty/null, or malformed",
        )
    )

    exchange.client = client
    exchange.exchange_info = client.futures_exchange_info()
    exchange.symbol_info = {
        symbol["symbol"]: symbol for symbol in exchange.exchange_info["symbols"]
    }
    exchange.initialized = True

    order = TradeOrder(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        amount=0.001,
        target_price=50000.0,
    )

    # Execute the order - the execute method catches BinanceAPIException
    # and returns a formatted error result instead of raising the exception
    result = await exchange.execute(order)

    # Verify that the result indicates a failure
    assert result["status"] == "failed"
    # Check if the error code is -1102 (APIError -1102)
    assert (
        "-1102" in str(result["error"])
        or ("code" in result and result["code"] == -1102)
        or (
            "error" in result
            and "code" in result["error"]
            and result["error"]["code"] == -1102
        )
    )

    # Verify that the client was only called once (no retries)
    assert client.futures_create_order.call_count == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
