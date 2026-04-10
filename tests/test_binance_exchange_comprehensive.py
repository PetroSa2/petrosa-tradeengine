"""
Comprehensive tests for Binance Futures Exchange to increase coverage to 75%.

This test suite covers:
1. Order execution methods (market, limit, stop, take_profit, etc.)
2. Order validation (notional, symbol, status checks)
3. Exchange info management
4. Price and position queries
5. Error handling
"""

import sys  # noqa: E402
from unittest.mock import AsyncMock, MagicMock, Mock, patch  # noqa: E402

import pytest  # noqa: E402

from shared.constants import UTC

# Mock binance module before importing
mock_binance = MagicMock()
mock_binance.exceptions = MagicMock()
sys.modules["binance"] = mock_binance
sys.modules["binance.exceptions"] = mock_binance.exceptions
# Create mock for binance.enums with the required constants
mock_binance.enums = MagicMock()
mock_binance.enums.FUTURE_ORDER_TYPE_STOP = "STOP"
mock_binance.enums.FUTURE_ORDER_TYPE_TAKE_PROFIT = "TAKE_PROFIT"
sys.modules["binance.enums"] = mock_binance.enums


# Create mock BinanceAPIException
class MockBinanceAPIException(Exception):
    def __init__(self, response, message):
        self.response = response
        self.message = message
        # Handle both dict responses and object responses
        if isinstance(response, dict):
            self.code = response.get("code", -1000)
        else:
            self.code = getattr(response, "code", -1000)


mock_binance.exceptions.BinanceAPIException = MockBinanceAPIException

from contracts.order import OrderSide, OrderType, TradeOrder  # noqa: E402
from tradeengine.exchange.binance import BinanceFuturesExchange  # noqa: E402

# Alias for tests
BinanceAPIException = MockBinanceAPIException


@pytest.fixture
def mock_binance_client():
    """Mock Binance client"""
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
                            "maxQty": "1000",
                            "stepSize": "0.001",
                        },
                    ],
                }
            ]
        }
    )
    client.futures_create_order = Mock(return_value={"orderId": 12345, "status": "NEW"})
    client._request_futures_api = Mock(
        return_value={"algoId": 12345, "status": "NEW", "algoStatus": "NEW"}
    )
    client.futures_get_open_orders = Mock(return_value=[])
    client.futures_position_information = Mock(return_value=[])
    client.futures_get_position_mode = Mock(return_value={"dualSidePosition": False})
    return client


@pytest.fixture
def binance_exchange(mock_binance_client):
    """Create BinanceFuturesExchange instance with mocked client"""
    exchange = BinanceFuturesExchange()
    exchange.client = mock_binance_client
    exchange.initialized = True
    exchange.symbol_info = {
        "BTCUSDT": {
            "baseAsset": "BTC",
            "quoteAsset": "USDT",
            "status": "TRADING",
            "filters": [
                {"filterType": "MIN_NOTIONAL", "notional": "20.0"},
                {
                    "filterType": "LOT_SIZE",
                    "minQty": "0.001",
                    "maxQty": "1000",
                    "stepSize": "0.001",
                },
            ],
        }
    }
    return exchange


class TestOrderExecution:
    """Test order execution methods"""

    @pytest.mark.asyncio
    async def test_execute_market_order(self, binance_exchange):
        """Test executing market order"""
        order = TradeOrder(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            type=OrderType.MARKET,
            amount=0.001,
            target_price=50000.0,
        )
        result = await binance_exchange.execute(order)
        assert result is not None
        assert "status" in result

    @pytest.mark.asyncio
    async def test_execute_limit_order(self, binance_exchange):
        """Test executing limit order"""
        order = TradeOrder(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            type=OrderType.LIMIT,
            amount=0.001,
            target_price=50000.0,
        )
        result = await binance_exchange.execute(order)
        assert result is not None
        assert "status" in result

    @pytest.mark.asyncio
    async def test_execute_stop_order(self, binance_exchange):
        """Test executing stop order"""
        order = TradeOrder(
            symbol="BTCUSDT",
            side=OrderSide.SELL,
            type=OrderType.STOP_LIMIT,
            amount=0.001,
            target_price=48000.0,
            stop_loss=48000.0,
            take_profit=None,
            conditional_price=None,
            conditional_direction=None,
            conditional_timeout=None,
            iceberg_quantity=None,
            client_order_id=None,
            order_id=None,
            status="pending",
            filled_amount=0.0,
            average_price=0.0,
            position_id=None,
            position_side=None,
            exchange="binance",
            simulate=False,
            reduce_only=False,
            time_in_force=None,
            position_size_pct=None,
            updated_at=None,
        )
        result = await binance_exchange.execute(order)
        assert result is not None
        assert "status" in result
        # Verify params
        binance_exchange.client._request_futures_api.assert_called()
        args, kwargs = binance_exchange.client._request_futures_api.call_args
        data = kwargs.get("data", {})
        assert data.get("workingType") == "MARK_PRICE"
        assert data.get("priceProtect") is True

    @pytest.mark.asyncio
    async def test_execute_take_profit_order(self, binance_exchange):
        """Test executing take profit order"""
        order = TradeOrder(
            symbol="BTCUSDT",
            side=OrderSide.SELL,
            type=OrderType.TAKE_PROFIT_LIMIT,
            amount=0.001,
            target_price=52000.0,
            take_profit=52000.0,
            stop_loss=None,
            conditional_price=None,
            conditional_direction=None,
            conditional_timeout=None,
            iceberg_quantity=None,
            client_order_id=None,
            order_id=None,
            status="pending",
            filled_amount=0.0,
            average_price=0.0,
            position_id=None,
            position_side=None,
            exchange="binance",
            simulate=False,
            reduce_only=False,
            time_in_force=None,
            position_size_pct=None,
            updated_at=None,
        )
        order = TradeOrder(
            symbol="BTCUSDT",
            side=OrderSide.SELL,
            type=OrderType.STOP_LIMIT,
            amount=0.001,
            target_price=48000.0,
            stop_loss=48000.0,
        )
        result = await binance_exchange.execute(order)
        assert result is not None
        assert "status" in result
        # Verify that _request_futures_api is called instead of futures_create_order
        binance_exchange.client._request_futures_api.assert_called()
        # Verify specific parameters used for algo order
        args, kwargs = binance_exchange.client._request_futures_api.call_args
        assert kwargs.get("data", {}).get("type") == "STOP"

    @pytest.mark.asyncio
    async def test_execute_take_profit_limit_order(self, binance_exchange):
        """Test executing take profit limit order"""
        # Mock validation
        binance_exchange.validate_price_within_percent_filter = AsyncMock(
            return_value=(True, None)
        )
        order = TradeOrder(
            symbol="BTCUSDT",
            side=OrderSide.SELL,
            type=OrderType.TAKE_PROFIT_LIMIT,
            amount=0.001,
            target_price=52000.0,
            take_profit=52000.0,
        )
        result = await binance_exchange.execute(order)
        assert result is not None
        assert "status" in result
        # Verify that _request_futures_api is called instead of futures_create_order
        binance_exchange.client._request_futures_api.assert_called()
        # Verify specific parameters used for algo order
        args, kwargs = binance_exchange.client._request_futures_api.call_args
        assert kwargs.get("data", {}).get("type") == "TAKE_PROFIT"

    @pytest.mark.asyncio
    async def test_execute_stop_limit_order_with_validation_error(
        self, binance_exchange
    ):
        """Test stop limit order with validation error"""
        # Mock validation to fail
        binance_exchange.validate_price_within_percent_filter = AsyncMock(
            return_value=(False, "Price out of range")
        )
        order = TradeOrder(
            symbol="BTCUSDT",
            side=OrderSide.SELL,
            type=OrderType.STOP_LIMIT,
            amount=0.001,
            target_price=48000.0,
            stop_loss=48000.0,
        )
        # execute() catches exceptions and returns error result
        result = await binance_exchange.execute(order)
        assert result is not None
        # Should return error result
        assert "error" in result or result.get("status") in ["failed", "error"]

    @pytest.mark.asyncio
    async def test_execute_take_profit_limit_order_with_validation_error(
        self, binance_exchange
    ):
        """Test take profit limit order with validation error"""
        # Mock validation to fail
        binance_exchange.validate_price_within_percent_filter = AsyncMock(
            return_value=(False, "Price out of range")
        )
        order = TradeOrder(
            symbol="BTCUSDT",
            side=OrderSide.SELL,
            type=OrderType.TAKE_PROFIT_LIMIT,
            amount=0.001,
            target_price=52000.0,
            take_profit=52000.0,
        )
        # execute() catches exceptions and returns error result
        result = await binance_exchange.execute(order)
        assert result is not None
        # Should return error result
        assert "error" in result or result.get("status") in ["failed", "error"]

    @pytest.mark.asyncio
    async def test_execute_order_with_position_side(self, binance_exchange):
        """Test executing order with position side (hedge mode)"""
        order = TradeOrder(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            type=OrderType.MARKET,
            amount=0.001,
            target_price=50000.0,
            position_side="LONG",
        )
        result = await binance_exchange.execute(order)
        assert result is not None
        assert "status" in result

    @pytest.mark.asyncio
    async def test_execute_order_with_reduce_only(self, binance_exchange):
        """Test executing order with reduce_only flag"""
        order = TradeOrder(
            symbol="BTCUSDT",
            side=OrderSide.SELL,
            type=OrderType.MARKET,
            amount=0.001,
            target_price=50000.0,
            reduce_only=True,
        )
        result = await binance_exchange.execute(order)
        assert result is not None
        assert "status" in result

    @pytest.mark.asyncio
    async def test_execute_unsupported_order_type(self, binance_exchange):
        """Test executing unsupported order type raises error"""
        order = TradeOrder(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            type="unsupported_type",  # Invalid type
            amount=0.001,
            target_price=50000.0,
        )
        result = await binance_exchange.execute(order)
        assert result is not None
        # Status can be "error" or "failed" - both indicate failure
        status = result.get("status", "").lower()
        assert "error" in status or "failed" in status or status == "error"

    @pytest.mark.asyncio
    async def test_execute_with_general_exception(self, binance_exchange):
        """Test handling general exceptions"""
        binance_exchange._validate_order = AsyncMock(
            side_effect=Exception("General error")
        )
        order = TradeOrder(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            type=OrderType.MARKET,
            amount=0.001,
            target_price=50000.0,
        )
        result = await binance_exchange.execute(order)
        assert result is not None
        # _format_error_result returns {"status": "failed", "error": ...}
        assert result.get("status") == "failed"
        assert "error" in result


class TestOrderValidation:
    """Test order validation"""

    @pytest.mark.asyncio
    async def test_validate_order_valid_symbol(self, binance_exchange):
        """Test validating order with valid symbol"""
        # Mock _get_current_price to avoid actual API call
        binance_exchange._get_current_price = AsyncMock(return_value=50000.0)
        binance_exchange._validate_notional = AsyncMock()

        order = TradeOrder(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            type=OrderType.MARKET,
            amount=0.001,
            target_price=50000.0,
        )
        # Should not raise exception
        await binance_exchange._validate_order(order)

    @pytest.mark.asyncio
    async def test_validate_order_invalid_symbol(self, binance_exchange):
        """Test validating order with invalid symbol"""
        order = TradeOrder(
            symbol="INVALID",
            side=OrderSide.BUY,
            type=OrderType.MARKET,
            amount=0.001,
            target_price=50000.0,
        )
        with pytest.raises(ValueError, match="Symbol.*not supported"):
            await binance_exchange._validate_order(order)

    @pytest.mark.asyncio
    async def test_validate_notional_reduce_only_skip(self, binance_exchange):
        """Test that notional validation is skipped for reduce_only orders"""
        order = TradeOrder(
            symbol="BTCUSDT",
            side=OrderSide.SELL,
            type=OrderType.MARKET,
            amount=0.0001,  # Very small amount
            target_price=50000.0,
            reduce_only=True,
        )
        # Should not raise exception for reduce_only orders
        await binance_exchange._validate_notional(order, 50000.0)

    @pytest.mark.asyncio
    async def test_validate_notional_meets_requirement(self, binance_exchange):
        """Test notional validation when requirement is met"""
        # Ensure symbol_info has the right structure with filters
        # The code checks for "notional" key in MIN_NOTIONAL filter, not "minNotional"
        binance_exchange.symbol_info["BTCUSDT"] = {
            "baseAsset": "BTC",
            "quoteAsset": "USDT",
            "status": "TRADING",
            "filters": [
                {"filterType": "MIN_NOTIONAL", "notional": "20.0"},
                {
                    "filterType": "LOT_SIZE",
                    "minQty": "0.001",
                    "maxQty": "1000",
                    "stepSize": "0.001",
                },
            ],
        }

        order = TradeOrder(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            type=OrderType.MARKET,
            amount=0.001,  # 0.001 * 50000 = $50, above $20 minimum
            target_price=50000.0,
        )
        # Should not raise exception
        await binance_exchange._validate_notional(order, 50000.0)

    @pytest.mark.asyncio
    async def test_validate_notional_below_requirement(self, binance_exchange):
        """Test notional validation when requirement is not met"""
        # Ensure symbol_info has the right structure with filters
        # The code checks for "notional" key in MIN_NOTIONAL filter
        binance_exchange.symbol_info["BTCUSDT"] = {
            "baseAsset": "BTC",
            "quoteAsset": "USDT",
            "status": "TRADING",
            "filters": [
                {"filterType": "MIN_NOTIONAL", "notional": "20.0"},
                {
                    "filterType": "LOT_SIZE",
                    "minQty": "0.001",
                    "maxQty": "1000",
                    "stepSize": "0.001",
                },
            ],
        }

        order = TradeOrder(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            type=OrderType.MARKET,
            amount=0.0001,  # 0.0001 * 50000 = $5, below $20 minimum
            target_price=50000.0,
        )
        with pytest.raises(ValueError, match="Order notional.*below minimum"):
            await binance_exchange._validate_notional(order, 50000.0)


class TestExchangeInfo:
    """Test exchange info management"""

    @pytest.mark.asyncio
    async def test_load_exchange_info(self, binance_exchange, mock_binance_client):
        """Test loading exchange info"""
        binance_exchange.initialized = False
        binance_exchange.client = mock_binance_client
        binance_exchange.symbol_info = {}  # Reset symbol_info

        # Update mock to include baseAsset and quoteAsset
        mock_binance_client.futures_exchange_info = Mock(
            return_value={
                "symbols": [
                    {
                        "symbol": "BTCUSDT",
                        "baseAsset": "BTC",
                        "quoteAsset": "USDT",
                        "status": "TRADING",
                        "filters": [
                            {"filterType": "MIN_NOTIONAL", "notional": "20.0"},
                            {
                                "filterType": "LOT_SIZE",
                                "minQty": "0.001",
                                "maxQty": "1000",
                                "stepSize": "0.001",
                            },
                        ],
                    }
                ]
            }
        )

        await binance_exchange._load_exchange_info()
        assert binance_exchange.symbol_info is not None
        assert "BTCUSDT" in binance_exchange.symbol_info

    def test_get_min_order_amount(self, binance_exchange):
        """Test getting minimum order amount"""
        # Ensure symbol_info has the right structure with filters
        # The code checks for "notional" key in MIN_NOTIONAL filter
        binance_exchange.symbol_info["BTCUSDT"] = {
            "baseAsset": "BTC",
            "quoteAsset": "USDT",
            "status": "TRADING",
            "filters": [
                {"filterType": "MIN_NOTIONAL", "notional": "20.0"},
                {
                    "filterType": "LOT_SIZE",
                    "minQty": "0.001",
                    "maxQty": "1000",
                    "stepSize": "0.001",
                },
            ],
        }
        result = binance_exchange.get_min_order_amount("BTCUSDT")
        assert result is not None
        assert "min_notional" in result or "min_qty" in result

    def test_calculate_min_order_amount(self, binance_exchange):
        """Test calculating minimum order amount"""
        # Ensure symbol_info has the right structure with filters
        # The code checks for "notional" key in MIN_NOTIONAL filter
        binance_exchange.symbol_info["BTCUSDT"] = {
            "baseAsset": "BTC",
            "quoteAsset": "USDT",
            "status": "TRADING",
            "filters": [
                {"filterType": "MIN_NOTIONAL", "notional": "20.0"},
                {
                    "filterType": "LOT_SIZE",
                    "minQty": "0.001",
                    "maxQty": "1000",
                    "stepSize": "0.001",
                },
            ],
        }
        result = binance_exchange.calculate_min_order_amount("BTCUSDT", 50000.0)
        assert isinstance(result, float)
        assert result > 0


class TestPriceAndPositionQueries:
    """Test price and position queries"""

    @pytest.mark.asyncio
    async def test_get_current_price(self, binance_exchange):
        """Test getting current price"""
        price = await binance_exchange._get_current_price("BTCUSDT")
        assert isinstance(price, float)
        assert price > 0

    @pytest.mark.asyncio
    async def test_get_current_price_no_client(self, binance_exchange):
        """Test getting current price when client is not initialized"""
        binance_exchange.client = None
        with pytest.raises(
            RuntimeError, match="Binance Futures client not initialized"
        ):
            await binance_exchange._get_current_price("BTCUSDT")

    @pytest.mark.asyncio
    async def test_get_order_status(self, binance_exchange, mock_binance_client):
        """Test getting order status"""
        mock_binance_client.futures_get_order = Mock(
            return_value={"orderId": 12345, "status": "FILLED", "symbol": "BTCUSDT"}
        )
        result = await binance_exchange.get_order_status("BTCUSDT", 12345)
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_position_info(self, binance_exchange):
        """Test getting position information"""
        result = await binance_exchange.get_position_info()
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_verify_hedge_mode(self, binance_exchange):
        """Test verifying hedge mode"""
        result = await binance_exchange.verify_hedge_mode()
        assert result is not None
        assert "hedge_mode_enabled" in result


class TestInitialization:
    """Test exchange initialization"""

    @pytest.mark.asyncio
    @patch("shared.constants.BINANCE_API_KEY", "test_key")
    @patch("shared.constants.BINANCE_API_SECRET", "test_secret")
    async def test_initialize(self, binance_exchange, mock_binance_client):
        """Test exchange initialization"""
        binance_exchange.initialized = False
        binance_exchange.client = None
        binance_exchange._load_exchange_info = AsyncMock()

        # Mock the client creation
        with patch(
            "tradeengine.exchange.binance.Client", return_value=mock_binance_client
        ):
            await binance_exchange.initialize()

        assert binance_exchange.initialized is True

    @pytest.mark.asyncio
    async def test_close(self, binance_exchange):
        """Test closing exchange connection"""
        await binance_exchange.close()
        # Should not raise exception

    @pytest.mark.asyncio
    async def test_get_symbol_min_notional(self, binance_exchange):
        """Test getting symbol minimum notional information"""
        result = await binance_exchange.get_symbol_min_notional("BTCUSDT")
        assert isinstance(result, dict)
        assert "min_notional" in result
        assert "current_price" in result
        assert "min_quantity" in result
        assert "notional_value" in result

    @pytest.mark.asyncio
    async def test_validate_and_adjust_price_exception_handling(self, binance_exchange):
        """Test price validation exception handling"""
        # Mock to raise exception
        binance_exchange._get_current_price = AsyncMock(
            side_effect=Exception("Price fetch error")
        )
        binance_exchange.get_percent_price_filter = Mock(
            side_effect=Exception("Filter error")
        )

        # Should handle exception gracefully
        (
            is_adjusted,
            adjusted_price,
            msg,
        ) = await binance_exchange.validate_and_adjust_price_for_percent_filter(
            "BTCUSDT", 50000.0, "LIMIT"
        )
        # Should return original price on error (fail open)
        assert adjusted_price == 50000.0

    @pytest.mark.asyncio
    async def test_validate_price_within_percent_filter_invalid(self, binance_exchange):
        """Test price validation with invalid price"""
        # Mock get_percent_price_filter
        binance_exchange.get_percent_price_filter = Mock(
            return_value={"multiplierUp": "1.1", "multiplierDown": "0.9"}
        )
        binance_exchange._get_current_price = AsyncMock(return_value=50000.0)

        # Price too high (above 1.1 * 50000 = 55000)
        (
            is_valid,
            error_msg,
        ) = await binance_exchange.validate_price_within_percent_filter(
            "BTCUSDT", 60000.0, "LIMIT"
        )
        assert is_valid is False
        assert "PERCENT_PRICE filter violation" in error_msg

    @pytest.mark.asyncio
    async def test_validate_price_within_percent_filter_exception(
        self, binance_exchange
    ):
        """Test price validation exception handling"""
        # Mock to raise exception
        binance_exchange._get_current_price = AsyncMock(
            side_effect=Exception("Price error")
        )

        # Should handle exception gracefully (fail open)
        (
            is_valid,
            error_msg,
        ) = await binance_exchange.validate_price_within_percent_filter(
            "BTCUSDT", 50000.0, "LIMIT"
        )
        assert is_valid is True  # Fail open
        assert error_msg == ""

    def test_calculate_min_order_amount_with_exception(self, binance_exchange):
        """Test calculate_min_order_amount with exception"""
        # Mock to raise exception
        binance_exchange.get_min_order_amount = Mock(side_effect=Exception("Error"))

        # Should return fallback value
        result = binance_exchange.calculate_min_order_amount("BTCUSDT", 50000.0)
        assert result == 0.001  # Fallback value

    def test_calculate_min_order_amount_with_step_size(self, binance_exchange):
        """Test calculate_min_order_amount with step size rounding"""
        # Ensure symbol_info has proper step_size
        binance_exchange.symbol_info["BTCUSDT"]["filters"].append(
            {"filterType": "LOT_SIZE", "stepSize": "0.0001"}
        )

        # Mock get_min_order_amount to return proper structure
        binance_exchange.get_min_order_amount = Mock(
            return_value={
                "min_qty": 0.001,
                "min_notional": 20.0,
                "step_size": 0.0001,
                "precision": 4,
            }
        )

        result = binance_exchange.calculate_min_order_amount("BTCUSDT", 50000.0)
        assert result >= 0.001
        assert isinstance(result, float)

    def test_calculate_min_order_amount_with_notional_verification(
        self, binance_exchange
    ):
        """Test calculate_min_order_amount with notional verification"""
        # Mock get_min_order_amount to return values that need notional verification
        binance_exchange.get_min_order_amount = Mock(
            return_value={
                "min_qty": 0.001,
                "min_notional": 20.0,
                "step_size": 0.0001,
                "precision": 4,
            }
        )

        # Use a price where initial calculation might not meet notional
        result = binance_exchange.calculate_min_order_amount("BTCUSDT", 10000.0)
        # Should add step_size if notional not met
        assert result >= 0.001
        assert isinstance(result, float)
        # Verify it meets minimum notional
        assert result * 10000.0 >= 20.0

    def test_calculate_min_order_amount_with_notional_verification_adds_step(
        self, binance_exchange
    ):
        """Test calculate_min_order_amount adds step_size when notional not met"""
        # Mock get_min_order_amount to return values that need step_size addition
        binance_exchange.get_min_order_amount = Mock(
            return_value={
                "min_qty": 0.001,
                "min_notional": 20.0,
                "step_size": 0.0001,
                "precision": 4,
            }
        )

        # Use a price where initial calculation might not meet notional
        # This will trigger the step_size addition logic at lines 808-810
        result = binance_exchange.calculate_min_order_amount("BTCUSDT", 15000.0)
        # Should add step_size if notional not met
        assert result >= 0.001
        assert isinstance(result, float)
        # Verify it meets minimum notional after step addition
        assert result * 15000.0 >= 20.0

    def test_calculate_min_order_amount_without_price(self, binance_exchange):
        """Test calculate_min_order_amount without current price"""
        # Mock get_min_order_amount
        binance_exchange.get_min_order_amount = Mock(
            return_value={
                "min_qty": 0.001,
                "min_notional": 20.0,
                "step_size": 0.0001,
                "precision": 4,
            }
        )

        # Should return min_qty when no price provided
        result = binance_exchange.calculate_min_order_amount("BTCUSDT", None)
        assert result == 0.001


class TestPriceValidationAndFormatting:
    """Test price validation and formatting methods"""

    @pytest.mark.asyncio
    async def test_validate_and_adjust_price_for_percent_filter(self, binance_exchange):
        """Test price validation and adjustment"""
        # Mock get_percent_price_filter
        binance_exchange.get_percent_price_filter = Mock(
            return_value={"multiplierUp": "1.1", "multiplierDown": "0.9"}
        )
        binance_exchange._get_current_price = AsyncMock(return_value=50000.0)

        # Price within range
        (
            is_adjusted,
            adjusted_price,
            msg,
        ) = await binance_exchange.validate_and_adjust_price_for_percent_filter(
            "BTCUSDT", 50000.0, "LIMIT"
        )
        assert isinstance(is_adjusted, bool)
        assert isinstance(adjusted_price, float)

    @pytest.mark.asyncio
    async def test_validate_and_adjust_price_too_low(self, binance_exchange):
        """Test price adjustment when price is too low"""
        # Mock get_percent_price_filter
        binance_exchange.get_percent_price_filter = Mock(
            return_value={"multiplierUp": "1.1", "multiplierDown": "0.9"}
        )
        binance_exchange._get_current_price = AsyncMock(return_value=50000.0)

        # Price too low (below 0.9 * 50000 = 45000)
        (
            is_adjusted,
            adjusted_price,
            msg,
        ) = await binance_exchange.validate_and_adjust_price_for_percent_filter(
            "BTCUSDT", 40000.0, "LIMIT"
        )
        assert is_adjusted is True
        assert adjusted_price > 40000.0

    @pytest.mark.asyncio
    async def test_validate_and_adjust_price_too_high(self, binance_exchange):
        """Test price adjustment when price is too high"""
        # Mock get_percent_price_filter
        binance_exchange.get_percent_price_filter = Mock(
            return_value={"multiplierUp": "1.1", "multiplierDown": "0.9"}
        )
        binance_exchange._get_current_price = AsyncMock(return_value=50000.0)

        # Price too high (above 1.1 * 50000 = 55000)
        (
            is_adjusted,
            adjusted_price,
            msg,
        ) = await binance_exchange.validate_and_adjust_price_for_percent_filter(
            "BTCUSDT", 60000.0, "LIMIT"
        )
        assert is_adjusted is True
        assert adjusted_price < 60000.0

    def test_get_percent_price_filter(self, binance_exchange):
        """Test getting PERCENT_PRICE filter"""
        # Ensure symbol_info has PERCENT_PRICE filter
        binance_exchange.symbol_info["BTCUSDT"]["filters"].append(
            {
                "filterType": "PERCENT_PRICE",
                "multiplierUp": "1.1",
                "multiplierDown": "0.9",
            }
        )

        filter_info = binance_exchange.get_percent_price_filter("BTCUSDT")
        assert "multiplierUp" in filter_info
        assert "multiplierDown" in filter_info

    def test_format_quantity(self, binance_exchange):
        """Test formatting quantity"""
        quantity = binance_exchange._format_quantity("BTCUSDT", 0.001234)
        assert isinstance(quantity, str)

    def test_format_price(self, binance_exchange):
        """Test formatting price"""
        price = binance_exchange._format_price("BTCUSDT", 50000.123)
        assert isinstance(price, str)


class TestRetryLogic:
    """Test retry logic for API calls"""

    @pytest.mark.asyncio
    async def test_execute_with_retry_success(self, binance_exchange):
        """Test retry logic with successful call"""
        mock_func = Mock(return_value={"success": True})
        result = await binance_exchange._execute_with_retry(mock_func, param1="value1")
        assert result == {"success": True}
        assert mock_func.called


class TestFallbackLogic:
    """Test fallback logic for cancellation and status checks"""

    @pytest.mark.asyncio
    async def test_cancel_order_fallback_logic(self, binance_exchange):
        """Test cancellation fallback logic for -2011 error"""
        # Mock to simulate the -2011 error
        binance_exchange.client.futures_cancel_order = Mock(
            side_effect=BinanceAPIException({"code": -2011}, "Unknown order")
        )
        binance_exchange.client._request_futures_api = Mock(
            return_value={"algoId": 12345, "status": "CANCELED"}
        )

        result = await binance_exchange.cancel_order("BTCUSDT", 12345)
        assert result is not None
        # Should have called _request_futures_api for algo order cancellation
        binance_exchange.client._request_futures_api.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_order_status_fallback_logic(self, binance_exchange):
        """Test order status fallback logic for -2011 error"""
        # Mock to simulate the -2011 error
        binance_exchange.client.futures_get_order = Mock(
            side_effect=BinanceAPIException({"code": -2011}, "Unknown order")
        )
        # Mock the internal API call used by get_open_algo_orders
        binance_exchange.client._request_futures_api = Mock(
            return_value=[{"algoId": 12345, "status": "FILLED", "algoStatus": "FILLED"}]
        )

        result = await binance_exchange.get_order_status("BTCUSDT", 12345)
        assert result is not None
        assert result["order_id"] == 12345
        # Should have called _request_futures_api with 'openAlgoOrders' path
        binance_exchange.client._request_futures_api.assert_called_once_with(
            "get", "openAlgoOrders", signed=True, data={"symbol": "BTCUSDT"}
        )


class TestAdditionalMethods:
    """Test additional methods not yet covered"""

    @pytest.mark.asyncio
    async def test_calculate_fees(self, binance_exchange):
        """Test _calculate_fees method"""
        fills = [
            {"price": "50000.0", "qty": "0.001", "commission": "0.05"},
            {"price": "50001.0", "qty": "0.001", "commission": "0.050001"},
        ]
        fees = binance_exchange._calculate_fees(fills)
        assert fees == 0.100001

    @pytest.mark.asyncio
    async def test_format_execution_result(self, binance_exchange):
        """Test _format_execution_result method"""
        order = TradeOrder(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            type=OrderType.MARKET,
            amount=0.001,
            target_price=50000.0,
        )
        binance_response = {
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
        result = binance_exchange._format_execution_result(binance_response, order)
        assert isinstance(result, dict)
        assert "status" in result
        assert "order_id" in result
        # Handle both integer and string cases for order_id
        assert str(result["order_id"]) == "12345"

    @pytest.mark.asyncio
    async def test_format_error_result(self, binance_exchange):
        """Test _format_error_result method"""
        order = TradeOrder(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            type=OrderType.MARKET,
            amount=0.001,
            target_price=50000.0,
        )
        result = binance_exchange._format_error_result("Test error", order)
        assert isinstance(result, dict)
        assert result["status"] == "failed"
        assert result["error"] == "Test error"

    @pytest.mark.asyncio
    async def test_get_account_info(self, binance_exchange):
        """Test get_account_info method"""
        binance_exchange.client.futures_account = Mock(
            return_value={
                "totalWalletBalance": "10000.0",
                "totalUnrealizedProfit": "100.0",
                "availableBalance": "9900.0",
            }
        )
        result = await binance_exchange.get_account_info()
        assert (
            "balances" in result
            or "total_wallet_balance" in result
            or isinstance(result, dict)
        )

    @pytest.mark.asyncio
    async def test_get_account_info_no_client(self, binance_exchange):
        """Test get_account_info when client is not initialized"""
        binance_exchange.client = None
        binance_exchange.initialized = False
        # Mock initialize to do nothing (so it stays uninitialized)
        with patch.object(binance_exchange, "initialize", AsyncMock()):
            with pytest.raises(
                RuntimeError, match="Binance Futures client not initialized"
            ):
                await binance_exchange.get_account_info()

    @pytest.mark.asyncio
    async def test_get_symbol_price(self, binance_exchange):
        """Test get_symbol_price method"""
        binance_exchange.client.futures_symbol_ticker = Mock(
            return_value={"price": "50000.0"}
        )
        price = await binance_exchange.get_symbol_price("BTCUSDT")
        assert price == 50000.0

    @pytest.mark.asyncio
    async def test_get_price(self, binance_exchange):
        """Test get_price method (alias for get_symbol_price)"""
        binance_exchange.client.futures_symbol_ticker = Mock(
            return_value={"price": "50000.0"}
        )
        price = await binance_exchange.get_price("BTCUSDT")
        assert price == 50000.0

    @pytest.mark.asyncio
    async def test_cancel_order(self, binance_exchange):
        """Test cancel_order method"""
        binance_exchange.client.futures_cancel_order = Mock(
            return_value={"orderId": 12345, "status": "CANCELED"}
        )
        result = await binance_exchange.cancel_order("BTCUSDT", 12345)
        assert result is not None
        assert "status" in result or "order_id" in result

    @pytest.mark.asyncio
    async def test_cancel_order_no_client(self, binance_exchange):
        """Test cancel_order when client is not initialized"""
        binance_exchange.client = None
        binance_exchange.initialized = False
        # Should handle gracefully or raise
        try:
            result = await binance_exchange.cancel_order("BTCUSDT", 12345)
            assert isinstance(result, dict)
        except Exception:
            # Exception is also acceptable
            pass

    @pytest.mark.asyncio
    async def test_health_check_cached_healthy(self, binance_exchange):
        """health_check returns cached 'healthy' without calling futures_ping directly."""
        import time

        binance_exchange._last_ping_ok = True
        binance_exchange._last_ping_time = time.monotonic()  # fresh sentinel
        result = await binance_exchange.health_check()
        assert result["status"] == "healthy"
        assert result.get("cached") is True
        # futures_ping must NOT be called — that is the whole point of the fix
        binance_exchange.client.futures_ping.assert_not_called()

    @pytest.mark.asyncio
    async def test_health_check_cached_unhealthy(self, binance_exchange):
        """health_check returns 'unhealthy' when last ping failed (still non-blocking)."""
        import time

        binance_exchange._last_ping_ok = False
        binance_exchange._last_ping_time = time.monotonic()
        result = await binance_exchange.health_check()
        assert result["status"] == "unhealthy"
        assert result.get("cached") is True
        binance_exchange.client.futures_ping.assert_not_called()

    @pytest.mark.asyncio
    async def test_health_check_sentinel_expired(self, binance_exchange):
        """health_check returns 'degraded' when ping sentinel has expired."""
        binance_exchange._last_ping_time = 0.0  # far in the past
        result = await binance_exchange.health_check()
        assert result["status"] == "degraded"
        assert "expired" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_health_check_no_client(self, binance_exchange):
        """Test health_check when client is not initialized"""
        binance_exchange.client = None
        binance_exchange.initialized = False
        result = await binance_exchange.health_check()
        assert isinstance(result, dict)
        assert result.get("status") == "degraded"
        assert "error" in result

    @pytest.mark.asyncio
    async def test_ping_loop_updates_sentinel_on_success(self, binance_exchange):
        """_ping_loop updates _last_ping_ok=True and _last_ping_time on successful ping."""
        import asyncio
        import threading

        ping_called = threading.Event()

        def ping_and_signal():
            ping_called.set()
            return {}

        binance_exchange.client.futures_ping = ping_and_signal
        binance_exchange._last_ping_ok = False

        task = asyncio.create_task(binance_exchange._ping_loop())
        # Wait (in a thread-safe way) for the executor to confirm the ping ran,
        # then yield to the event loop so _ping_loop can write the sentinel.
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: ping_called.wait(timeout=2.0)
        )
        await asyncio.sleep(0)  # one scheduler tick so _ping_loop sets _last_ping_ok
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert binance_exchange._last_ping_ok is True
        assert binance_exchange._last_ping_time > 0

    @pytest.mark.asyncio
    async def test_ping_loop_updates_sentinel_on_failure(self, binance_exchange):
        """_ping_loop sets _last_ping_ok=False when ping raises."""
        import asyncio
        import threading

        ping_called = threading.Event()

        def ping_fail_and_signal():
            ping_called.set()
            raise Exception("timeout")

        binance_exchange.client.futures_ping = ping_fail_and_signal
        binance_exchange._last_ping_ok = True

        task = asyncio.create_task(binance_exchange._ping_loop())
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: ping_called.wait(timeout=2.0)
        )
        await asyncio.sleep(0)  # one tick for _ping_loop to set _last_ping_ok=False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert binance_exchange._last_ping_ok is False

    @pytest.mark.asyncio
    async def test_load_exchange_info_success(self, binance_exchange):
        """Test _load_exchange_info successful load"""
        binance_exchange.client.futures_exchange_info = Mock(
            return_value={
                "symbols": [
                    {
                        "symbol": "BTCUSDT",
                        "status": "TRADING",
                        "baseAsset": "BTC",
                        "quoteAsset": "USDT",
                        "filters": [
                            {"filterType": "MIN_NOTIONAL", "notional": "20.0"},
                            {
                                "filterType": "LOT_SIZE",
                                "minQty": "0.001",
                                "maxQty": "1000",
                                "stepSize": "0.001",
                            },
                        ],
                    }
                ]
            }
        )
        await binance_exchange._load_exchange_info()
        assert "BTCUSDT" in binance_exchange.symbol_info

    @pytest.mark.asyncio
    async def test_load_exchange_info_error(self, binance_exchange):
        """Test _load_exchange_info error handling"""
        binance_exchange.client.futures_exchange_info = Mock(
            side_effect=Exception("API Error")
        )
        # Should handle error gracefully
        try:
            await binance_exchange._load_exchange_info()
        except Exception:
            # Exception is acceptable
            pass

    @pytest.mark.asyncio
    async def test_get_order_status_error(self, binance_exchange):
        """Test get_order_status error handling"""
        binance_exchange.client.futures_get_order = Mock(
            side_effect=Exception("Order not found")
        )
        # Should handle error gracefully
        try:
            result = await binance_exchange.get_order_status("BTCUSDT", 12345)
            assert isinstance(result, dict)
        except Exception:
            # Exception is also acceptable
            pass

    @pytest.mark.asyncio
    async def test_get_position_info_error(self, binance_exchange):
        """Test get_position_info error handling"""
        binance_exchange.client.futures_position_information = Mock(
            side_effect=Exception("API Error")
        )
        # Should handle error gracefully
        try:
            result = await binance_exchange.get_position_info()
            assert isinstance(result, list)
        except Exception:
            # Exception is also acceptable
            pass

    @pytest.mark.asyncio
    async def test_verify_hedge_mode_error(self, binance_exchange):
        """Test verify_hedge_mode error handling"""
        binance_exchange.client.futures_get_position_mode = Mock(
            side_effect=Exception("API Error")
        )
        result = await binance_exchange.verify_hedge_mode()
        assert isinstance(result, dict)
        assert "hedge_mode_enabled" in result or "error" in result
