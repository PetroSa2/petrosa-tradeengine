from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contracts.order import TradeOrder
from tradeengine.position_manager import PositionManager


@pytest.fixture
def mock_exchange():
    exchange = MagicMock()
    # Mock account info for _refresh_portfolio_value
    exchange.get_account_info = AsyncMock(return_value={"available_balance": 10000.0})
    # Mock the new get_open_algo_orders method
    exchange.get_open_algo_orders = AsyncMock()
    return exchange


@pytest.fixture
def position_manager(mock_exchange):
    pm = PositionManager(exchange=mock_exchange)
    pm.total_portfolio_value = 10000.0
    return pm


@pytest.mark.asyncio
async def test_check_algo_order_limits_within_limit(position_manager, mock_exchange):
    """Test when algo order counts are below limits"""
    # 0 orders for symbol, 0 orders total
    mock_exchange.get_open_algo_orders.side_effect = [
        [],  # Symbol check
        [],  # Account check
    ]

    order = TradeOrder(
        symbol="BTCUSDT", side="buy", type="market", amount=0.001, position_side="LONG"
    )

    result = await position_manager.check_algo_order_limits(order)
    assert result is True
    assert mock_exchange.get_open_algo_orders.call_count == 2


@pytest.mark.asyncio
async def test_check_algo_order_limits_symbol_exceeded(position_manager, mock_exchange):
    """Test when symbol-specific limit is reached"""
    # 9 orders for symbol (limit is 10, but we need 2 for OCO)
    mock_exchange.get_open_algo_orders.side_effect = [
        [{"algoId": i} for i in range(9)],  # Symbol check
    ]

    order = TradeOrder(
        symbol="BTCUSDT", side="buy", type="market", amount=0.001, position_side="LONG"
    )

    result = await position_manager.check_algo_order_limits(order)
    assert result is False
    # Account check should NOT be called if symbol check fails
    assert mock_exchange.get_open_algo_orders.call_count == 1


@pytest.mark.asyncio
async def test_check_algo_order_limits_account_exceeded(
    position_manager, mock_exchange
):
    """Test when account-wide limit is reached"""
    # 0 orders for symbol, but 98 orders total (limit 100)
    mock_exchange.get_open_algo_orders.side_effect = [
        [],  # Symbol check
        [{"algoId": i} for i in range(98)],  # Account check
    ]

    order = TradeOrder(
        symbol="BTCUSDT", side="buy", type="market", amount=0.001, position_side="LONG"
    )

    result = await position_manager.check_algo_order_limits(order)
    assert result is False
    assert mock_exchange.get_open_algo_orders.call_count == 2


@pytest.mark.asyncio
async def test_check_algo_order_limits_api_error_failsafe(
    position_manager, mock_exchange
):
    """Test failsafe behavior when API call fails"""
    mock_exchange.get_open_algo_orders.side_effect = Exception("API Error")

    order = TradeOrder(
        symbol="BTCUSDT", side="buy", type="market", amount=0.001, position_side="LONG"
    )

    # Should return True (allow trade) even if API fails
    result = await position_manager.check_algo_order_limits(order)
    assert result is True


@pytest.mark.asyncio
async def test_check_position_limits_integrates_algo_check(
    position_manager, mock_exchange
):
    """Test that check_position_limits actually calls check_algo_order_limits"""
    order = TradeOrder(
        symbol="BTCUSDT",
        side="buy",
        type="market",
        amount=0.001,
        position_side="LONG",
        position_size_pct=0.01,
    )

    # Bypass mandatory external fetches to avoid complex mocking
    position_manager._refresh_portfolio_value = AsyncMock(return_value=True)
    position_manager._refresh_positions_from_data_manager = AsyncMock()
    position_manager._calculate_portfolio_exposure = MagicMock(return_value=0.1)

    with patch.object(
        position_manager, "check_algo_order_limits", new_callable=AsyncMock
    ) as mock_algo_check:
        mock_algo_check.return_value = False  # Force algo check to fail

        result = await position_manager.check_position_limits(order)
        # Should be False because algo check failed
        assert result is False
        mock_algo_check.assert_called_once_with(order)
