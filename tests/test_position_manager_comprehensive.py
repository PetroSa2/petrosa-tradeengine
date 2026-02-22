"""
Comprehensive tests for PositionManager to achieve 75% coverage.

Tests cover:
- Position tracking (LONG/SHORT hedge mode)
- PnL calculation (realized and unrealized)
- Risk limit checks (position size, daily loss, portfolio exposure)
- Position updates and accumulation
- Position closing and cleanup
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contracts.order import TradeOrder
from tradeengine.position_manager import PositionManager


@pytest.fixture
def position_manager():
    """Create a PositionManager instance for testing"""
    pm = PositionManager()
    pm.mongodb_db = None  # Disable MongoDB for unit tests
    pm.total_portfolio_value = 10000.0  # Set portfolio value for risk calculations
    return pm


@pytest.fixture
def sample_long_order():
    """Create a sample LONG position order"""
    return TradeOrder(
        position_id="test-long-pos-1",
        symbol="BTCUSDT",
        side="buy",
        type="market",
        amount=0.001,
        target_price=50000.0,
        position_side="LONG",
        exchange="binance",
        strategy_metadata={"strategy_id": "test_strategy"},
    )


@pytest.fixture
def sample_short_order():
    """Create a sample SHORT position order"""
    return TradeOrder(
        position_id="test-short-pos-1",
        symbol="BTCUSDT",
        side="sell",
        type="market",
        amount=0.002,
        target_price=50000.0,
        position_side="SHORT",
        exchange="binance",
        strategy_metadata={"strategy_id": "test_strategy"},
    )


@pytest.fixture
def sample_fill_result():
    """Create a sample order fill result"""
    return {
        "status": "filled",
        "order_id": "binance-order-123",
        "fill_price": 50000.0,
        "amount": 0.001,
        "commission": 0.05,
        "commission_asset": "USDT",
    }


# ============================================================================
# Position Creation and Tracking Tests
# ============================================================================


@pytest.mark.asyncio
async def test_create_position_record_long(
    position_manager, sample_long_order, sample_fill_result
):
    """Test creating a LONG position record"""
    with patch(
        "shared.mysql_client.position_client.create_position", new_callable=AsyncMock
    ) as mock_create:
        await position_manager.create_position_record(
            sample_long_order, sample_fill_result
        )

        # Verify position was created
        assert mock_create.called
        call_args = mock_create.call_args[0][0]
        assert call_args["position_id"] == "test-long-pos-1"
        assert call_args["symbol"] == "BTCUSDT"
        assert call_args["position_side"] == "LONG"
        assert call_args["entry_price"] == 50000.0
        assert call_args["quantity"] == 0.001


@pytest.mark.asyncio
async def test_create_position_record_short(
    position_manager, sample_short_order, sample_fill_result
):
    """Test creating a SHORT position record"""
    with patch(
        "shared.mysql_client.position_client.create_position", new_callable=AsyncMock
    ) as mock_create:
        await position_manager.create_position_record(
            sample_short_order, sample_fill_result
        )

        # Verify position was created
        assert mock_create.called
        call_args = mock_create.call_args[0][0]
        assert call_args["position_side"] == "SHORT"


@pytest.mark.asyncio
async def test_create_position_record_with_sl_tp(
    position_manager, sample_long_order, sample_fill_result
):
    """Test creating position record with stop loss and take profit"""
    sample_long_order.stop_loss = 48000.0
    sample_long_order.take_profit = 52000.0

    with patch(
        "shared.mysql_client.position_client.create_position", new_callable=AsyncMock
    ) as mock_create:
        await position_manager.create_position_record(
            sample_long_order, sample_fill_result
        )

        call_args = mock_create.call_args[0][0]
        assert call_args["stop_loss"] == 48000.0
        assert call_args["take_profit"] == 52000.0


# ============================================================================
# Position Update and PnL Calculation Tests
# ============================================================================


@pytest.mark.asyncio
async def test_update_position_long_opening(
    position_manager, sample_long_order, sample_fill_result
):
    """Test updating position when opening a LONG position"""
    await position_manager.update_position(sample_long_order, sample_fill_result)

    position = position_manager.get_position("BTCUSDT", "LONG")
    assert position is not None
    assert position["quantity"] == 0.001
    assert position["avg_price"] == 50000.0
    assert position["position_side"] == "LONG"


@pytest.mark.asyncio
async def test_update_position_short_opening(
    position_manager, sample_short_order, sample_fill_result
):
    """Test updating position when opening a SHORT position"""
    short_result = sample_fill_result.copy()
    short_result["amount"] = 0.002
    await position_manager.update_position(sample_short_order, short_result)

    position = position_manager.get_position("BTCUSDT", "SHORT")
    assert position is not None
    assert position["quantity"] == pytest.approx(0.002, rel=0.01)
    assert position["avg_price"] == pytest.approx(50000.0, rel=0.01)
    assert position["position_side"] == "SHORT"


@pytest.mark.asyncio
async def test_update_position_long_accumulation(
    position_manager, sample_long_order, sample_fill_result
):
    """Test accumulating to an existing LONG position"""
    # Open initial position
    await position_manager.update_position(sample_long_order, sample_fill_result)

    # Add more to the position
    accumulate_order = TradeOrder(
        position_id="test-long-pos-1",
        symbol="BTCUSDT",
        side="buy",
        type="market",
        amount=0.001,
        target_price=51000.0,
        position_side="LONG",
        exchange="binance",
    )
    accumulate_result = {
        "status": "filled",
        "fill_price": 51000.0,
        "amount": 0.001,
    }

    await position_manager.update_position(accumulate_order, accumulate_result)

    position = position_manager.get_position("BTCUSDT", "LONG")
    assert position["quantity"] == 0.002  # 0.001 + 0.001
    # Average price should be weighted: (50000 * 0.001 + 51000 * 0.001) / 0.002 = 50500
    assert position["avg_price"] == 50500.0
    assert position["accumulation_count"] == 1


@pytest.mark.asyncio
async def test_update_position_long_partial_close(
    position_manager, sample_long_order, sample_fill_result
):
    """Test partially closing a LONG position"""
    # Open position
    await position_manager.update_position(sample_long_order, sample_fill_result)

    # Partially close (sell half)
    close_order = TradeOrder(
        position_id="test-long-pos-1",
        symbol="BTCUSDT",
        side="sell",
        type="market",
        amount=0.0005,
        target_price=52000.0,
        position_side="LONG",
        exchange="binance",
    )
    close_result = {
        "status": "filled",
        "fill_price": 52000.0,
        "amount": 0.0005,
    }

    initial_daily_pnl = position_manager.daily_pnl
    await position_manager.update_position(close_order, close_result)

    position = position_manager.get_position("BTCUSDT", "LONG")
    assert position is not None
    assert position["quantity"] == pytest.approx(0.0005, rel=0.01)  # 0.001 - 0.0005

    # Realized PnL: (52000 - 50000) * 0.0005 = 2000 * 0.0005 = 1.0
    assert position["realized_pnl"] == pytest.approx(1.0, rel=0.01)
    assert position_manager.daily_pnl == pytest.approx(
        initial_daily_pnl + 1.0, rel=0.01
    )


@pytest.mark.asyncio
async def test_update_position_long_full_close(
    position_manager, sample_long_order, sample_fill_result
):
    """Test fully closing a LONG position"""
    # Open position
    await position_manager.update_position(sample_long_order, sample_fill_result)

    # Fully close
    close_order = TradeOrder(
        position_id="test-long-pos-1",
        symbol="BTCUSDT",
        side="sell",
        type="market",
        amount=0.001,
        target_price=52000.0,
        position_side="LONG",
        exchange="binance",
    )
    close_result = {
        "status": "filled",
        "fill_price": 52000.0,
        "amount": 0.001,
    }

    with patch(
        "shared.mysql_client.position_client.close_position", new_callable=AsyncMock
    ) as mock_close:
        await position_manager.update_position(close_order, close_result)

        # Position should be removed
        position = position_manager.get_position("BTCUSDT", "LONG")
        assert position is None

        # Verify close was called in Data Manager
        assert mock_close.called


@pytest.mark.asyncio
async def test_update_position_short_profit_calculation(
    position_manager, sample_short_order, sample_fill_result
):
    """Test PnL calculation for SHORT position (profit when price goes down)"""
    # Open SHORT position at 50000
    short_result = sample_fill_result.copy()
    short_result["amount"] = 0.002
    await position_manager.update_position(sample_short_order, short_result)

    # Close SHORT position at 48000 (profit for SHORT)
    close_order = TradeOrder(
        position_id="test-short-pos-1",
        symbol="BTCUSDT",
        side="buy",
        type="market",
        amount=0.002,
        target_price=48000.0,
        position_side="SHORT",
        exchange="binance",
    )
    close_result = {
        "status": "filled",
        "fill_price": 48000.0,
        "amount": 0.002,
    }

    with patch(
        "shared.mysql_client.position_client.close_position", new_callable=AsyncMock
    ):
        await position_manager.update_position(close_order, close_result)

    # For SHORT: profit = (entry_price - exit_price) * quantity
    # (50000 - 48000) * 0.002 = 2000 * 0.002 = 4.0
    # Position should be closed, so check daily_pnl
    assert position_manager.daily_pnl == pytest.approx(4.0, rel=0.01)


@pytest.mark.asyncio
async def test_unrealized_pnl_long_profit(
    position_manager, sample_long_order, sample_fill_result
):
    """Test unrealized PnL calculation for LONG position in profit"""
    # Open LONG position at 50000
    await position_manager.update_position(sample_long_order, sample_fill_result)

    # Update with higher price (profit) - need to add more to trigger recalculation
    # Actually, unrealized PnL is calculated based on fill_price in update_position
    # Let's add more quantity at higher price to see unrealized PnL
    accumulate_order = TradeOrder(
        position_id="test-long-pos-1",
        symbol="BTCUSDT",
        side="buy",
        type="market",
        amount=0.0,  # No quantity change
        target_price=51000.0,
        position_side="LONG",
        exchange="binance",
    )
    update_result = {
        "status": "filled",
        "fill_price": 51000.0,  # Higher price = profit for LONG
        "amount": 0.0,  # No quantity change
    }

    # Note: update_position calculates unrealized PnL based on fill_price
    # For LONG: unrealized_pnl = (fill_price - avg_price) * quantity
    # Since we're not changing quantity, we need to check the position after update
    await position_manager.update_position(accumulate_order, update_result)

    position = position_manager.get_position("BTCUSDT", "LONG")
    # Unrealized PnL: (51000 - 50000) * 0.001 = 1000 * 0.001 = 1.0
    assert position["unrealized_pnl"] == pytest.approx(1.0, rel=0.01)


@pytest.mark.asyncio
async def test_unrealized_pnl_short_profit(
    position_manager, sample_short_order, sample_fill_result
):
    """Test unrealized PnL calculation for SHORT position in profit"""
    # Open SHORT position at 50000
    short_result = sample_fill_result.copy()
    short_result["amount"] = 0.002
    await position_manager.update_position(sample_short_order, short_result)

    # Update with lower price (profit for SHORT)
    update_order = TradeOrder(
        position_id="test-short-pos-1",
        symbol="BTCUSDT",
        side="sell",
        type="market",
        amount=0.0,
        target_price=49000.0,
        position_side="SHORT",
        exchange="binance",
    )
    update_result = {
        "status": "filled",
        "fill_price": 49000.0,  # Lower price = profit for SHORT
        "amount": 0.0,
    }

    await position_manager.update_position(update_order, update_result)

    position = position_manager.get_position("BTCUSDT", "SHORT")
    # Unrealized PnL: (50000 - 49000) * 0.002 = 1000 * 0.002 = 2.0
    assert position["unrealized_pnl"] == pytest.approx(2.0, rel=0.01)


# ============================================================================
# Risk Management Tests
# ============================================================================


@pytest.mark.asyncio
async def test_check_position_limits_within_limit(position_manager, sample_long_order):
    """Test position limit check when within limits"""
    position_manager.total_portfolio_value = 10000.0
    position_manager.max_position_size_pct = 0.1  # 10% max

    # Order value: 0.001 * 50000 = $50 (0.5% of portfolio, well within 10%)
    with patch(
        "shared.mysql_client.position_client.get_open_positions",
        new_callable=AsyncMock,
        return_value=[],
    ):
        with patch("tradeengine.position_manager.RISK_MANAGEMENT_ENABLED", True):
            result = await position_manager.check_position_limits(sample_long_order)
            assert result is True


@pytest.mark.asyncio
async def test_check_position_limits_exceeded(position_manager, sample_long_order):
    """Test position limit check when limit is exceeded via position_size_pct"""
    position_manager.total_portfolio_value = 10000.0
    position_manager.max_position_size_pct = 0.01  # 1% max

    # Set order position_size_pct to exceed limit
    large_order = TradeOrder(
        position_id="test-large-pos",
        symbol="BTCUSDT",
        side="buy",
        type="market",
        amount=0.01,
        target_price=50000.0,
        position_side="LONG",
        exchange="binance",
        position_size_pct=0.05,  # 5% (exceeds 1% limit)
    )

    with patch(
        "shared.mysql_client.position_client.get_open_positions",
        new_callable=AsyncMock,
        return_value=[],
    ):
        with patch("tradeengine.position_manager.RISK_MANAGEMENT_ENABLED", True):
            result = await position_manager.check_position_limits(large_order)
            assert result is False


@pytest.mark.asyncio
async def test_check_daily_loss_limits_within_limit(position_manager):
    """Test daily loss limit check when within limits"""
    position_manager.total_portfolio_value = 10000.0
    position_manager.max_daily_loss_pct = 0.05  # 5% max daily loss
    position_manager.daily_pnl = -400.0  # -4% loss (within 5% limit)

    with patch(
        "shared.mysql_client.position_client.get_daily_pnl",
        new_callable=AsyncMock,
        return_value=-400.0,
    ):
        result = await position_manager.check_daily_loss_limits()
        assert result is True


@pytest.mark.asyncio
async def test_check_daily_loss_limits_exceeded(position_manager):
    """Test daily loss limit check when limit is exceeded"""
    position_manager.total_portfolio_value = 10000.0
    position_manager.max_daily_loss_pct = 0.05  # 5% max daily loss = $500
    position_manager.daily_pnl = -600.0  # -6% loss (exceeds 5% limit)

    with patch(
        "shared.mysql_client.position_client.get_daily_pnl",
        new_callable=AsyncMock,
        return_value=-600.0,
    ):
        result = await position_manager.check_daily_loss_limits()
        assert result is False


@pytest.mark.asyncio
async def test_check_position_limits_includes_portfolio_exposure(
    position_manager, sample_long_order
):
    """Test that check_position_limits includes portfolio exposure check"""
    position_manager.total_portfolio_value = 10000.0
    position_manager.max_portfolio_exposure_pct = 0.8  # 80% max exposure

    # No existing positions, so exposure is 0%
    with patch(
        "shared.mysql_client.position_client.get_open_positions",
        new_callable=AsyncMock,
        return_value=[],
    ):
        result = await position_manager.check_position_limits(sample_long_order)
        assert result is True


@pytest.mark.asyncio
async def test_check_position_limits_portfolio_exposure_exceeded(
    position_manager, sample_long_order
):
    """Test that check_position_limits rejects when portfolio exposure is exceeded"""
    position_manager.total_portfolio_value = 10000.0
    position_manager.max_portfolio_exposure_pct = 0.5  # 50% max exposure

    # Add existing positions totaling 60% exposure
    # Note: check_position_limits checks current exposure, not including the new order
    position_manager.positions = {
        ("BTCUSDT", "LONG"): {
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "quantity": 0.1,
            "avg_price": 50000.0,
            "total_value": 5000.0,  # 50% of portfolio
        },
        ("ETHUSDT", "LONG"): {
            "symbol": "ETHUSDT",
            "position_side": "LONG",
            "quantity": 0.33,
            "avg_price": 3000.0,
            "total_value": 1000.0,  # 10% of portfolio
        },
    }
    # Total current exposure: 60% (exceeds 50% limit)
    # check_position_limits checks current_exposure > max_portfolio_exposure_pct
    # Note: _refresh_positions_from_data_manager will be called, so we need to mock it
    # to return the positions we set, otherwise it will clear them

    mock_positions_data = [
        {
            "symbol": "BTCUSDT",
            "position_side": "LONG",
            "quantity": 0.1,
            "avg_price": 50000.0,
            "total_value": 5000.0,
        },
        {
            "symbol": "ETHUSDT",
            "position_side": "LONG",
            "quantity": 0.33,
            "avg_price": 3000.0,
            "total_value": 1000.0,
        },
    ]

    with patch(
        "shared.mysql_client.position_client.get_open_positions",
        new_callable=AsyncMock,
        return_value=mock_positions_data,
    ):
        with patch("tradeengine.position_manager.RISK_MANAGEMENT_ENABLED", True):
            result = await position_manager.check_position_limits(sample_long_order)
            assert result is False


@pytest.mark.asyncio
async def test_calculate_portfolio_exposure(position_manager):
    """Test portfolio exposure calculation"""
    position_manager.total_portfolio_value = 10000.0

    # Add positions
    position_manager.positions = {
        ("BTCUSDT", "LONG"): {
            "quantity": 0.1,
            "avg_price": 50000.0,
        },
        ("ETHUSDT", "LONG"): {
            "quantity": 0.33,
            "avg_price": 3000.0,
        },
    }

    exposure = position_manager._calculate_portfolio_exposure()
    # BTC: 0.1 * 50000 = $5000 (50%)
    # ETH: 0.33 * 3000 = $1000 (10%)
    # Total: 60%
    assert exposure == pytest.approx(0.6, rel=0.01)


# ============================================================================
# Position Query Tests
# ============================================================================


@pytest.mark.asyncio
async def test_get_position_exists(
    position_manager, sample_long_order, sample_fill_result
):
    """Test getting an existing position"""
    await position_manager.update_position(sample_long_order, sample_fill_result)

    position = position_manager.get_position("BTCUSDT", "LONG")
    assert position is not None
    assert position["symbol"] == "BTCUSDT"
    assert position["position_side"] == "LONG"


@pytest.mark.asyncio
async def test_get_position_not_exists(position_manager):
    """Test getting a non-existent position"""
    position = position_manager.get_position("BTCUSDT", "LONG")
    assert position is None


@pytest.mark.asyncio
async def test_get_positions_by_symbol(
    position_manager, sample_long_order, sample_short_order, sample_fill_result
):
    """Test getting all positions for a symbol (hedge mode)"""
    # Create both LONG and SHORT positions for same symbol
    await position_manager.update_position(sample_long_order, sample_fill_result)

    short_result = sample_fill_result.copy()
    short_result["amount"] = 0.002
    await position_manager.update_position(sample_short_order, short_result)

    positions = position_manager.get_positions_by_symbol("BTCUSDT")
    assert len(positions) == 2

    # Verify both LONG and SHORT exist
    position_sides = {pos["position_side"] for pos in positions}
    assert "LONG" in position_sides
    assert "SHORT" in position_sides


@pytest.mark.asyncio
async def test_get_all_positions(
    position_manager, sample_long_order, sample_fill_result
):
    """Test getting all positions"""
    await position_manager.update_position(sample_long_order, sample_fill_result)

    # Add another position
    eth_order = TradeOrder(
        position_id="test-eth-pos",
        symbol="ETHUSDT",
        side="buy",
        type="market",
        amount=0.1,
        target_price=3000.0,
        position_side="LONG",
        exchange="binance",
    )
    eth_result = {
        "status": "filled",
        "fill_price": 3000.0,
        "amount": 0.1,
    }
    await position_manager.update_position(eth_order, eth_result)

    all_positions = position_manager.get_positions()
    assert len(all_positions) == 2
    assert ("BTCUSDT", "LONG") in all_positions
    assert ("ETHUSDT", "LONG") in all_positions


# ============================================================================
# Position Risk Order Updates
# ============================================================================


@pytest.mark.asyncio
async def test_update_position_risk_orders_stop_loss(
    position_manager, sample_long_order, sample_fill_result
):
    """Test updating position with stop loss order ID"""
    await position_manager.update_position(sample_long_order, sample_fill_result)

    with patch(
        "shared.mysql_client.position_client.update_position_risk_orders",
        new_callable=AsyncMock,
    ) as mock_update:
        await position_manager.update_position_risk_orders(
            "test-long-pos-1", stop_loss_order_id="sl-order-123"
        )

        assert mock_update.called
        call_args = mock_update.call_args
        assert call_args[0][0] == "test-long-pos-1"  # position_id
        assert "stop_loss_order_id" in call_args[0][1]
        assert call_args[0][1]["stop_loss_order_id"] == "sl-order-123"


@pytest.mark.asyncio
async def test_update_position_risk_orders_take_profit(
    position_manager, sample_long_order, sample_fill_result
):
    """Test updating position with take profit order ID"""
    await position_manager.update_position(sample_long_order, sample_fill_result)

    with patch(
        "shared.mysql_client.position_client.update_position_risk_orders",
        new_callable=AsyncMock,
    ) as mock_update:
        await position_manager.update_position_risk_orders(
            "test-long-pos-1", take_profit_order_id="tp-order-456"
        )

        assert mock_update.called
        call_args = mock_update.call_args
        assert call_args[0][0] == "test-long-pos-1"  # position_id
        assert "take_profit_order_id" in call_args[0][1]
        assert call_args[0][1]["take_profit_order_id"] == "tp-order-456"


@pytest.mark.asyncio
async def test_update_position_risk_orders_both(
    position_manager, sample_long_order, sample_fill_result
):
    """Test updating position with both SL and TP order IDs"""
    await position_manager.update_position(sample_long_order, sample_fill_result)

    with patch(
        "shared.mysql_client.position_client.update_position_risk_orders",
        new_callable=AsyncMock,
    ) as mock_update:
        await position_manager.update_position_risk_orders(
            "test-long-pos-1",
            stop_loss_order_id="sl-order-123",
            take_profit_order_id="tp-order-456",
        )

        assert mock_update.called
        call_args = mock_update.call_args[0][1]  # Second arg is the update_data dict
        assert call_args["stop_loss_order_id"] == "sl-order-123"
        assert call_args["take_profit_order_id"] == "tp-order-456"


# ============================================================================
# Position Closing Tests
# ============================================================================


@pytest.mark.asyncio
async def test_close_position_record_long(position_manager):
    """Test closing a LONG position record"""
    exit_result = {
        "position_id": "test-long-pos-1",
        "strategy_id": "test_strategy",
        "symbol": "BTCUSDT",
        "position_side": "LONG",
        "exchange": "binance",
        "entry_price": 50000.0,
        "exit_price": 52000.0,
        "quantity": 0.001,
        "entry_time": datetime.utcnow(),
        "entry_commission": 0.05,
        "exit_commission": 0.052,
        "order_id": "exit-order-123",
        "close_reason": "take_profit",
    }

    with patch(
        "shared.mysql_client.position_client.update_position", new_callable=AsyncMock
    ) as mock_update:
        await position_manager.close_position_record("test-long-pos-1", exit_result)

        assert mock_update.called
        # update_position(position_id, update_data) - second arg is update_data
        call_args = mock_update.call_args[0][1]
        assert call_args["status"] == "closed"
        assert "exit_price" in call_args
        assert "pnl" in call_args


@pytest.mark.asyncio
async def test_close_position_record_short(position_manager):
    """Test closing a SHORT position record"""
    exit_result = {
        "position_id": "test-short-pos-1",
        "strategy_id": "test_strategy",
        "symbol": "BTCUSDT",
        "position_side": "SHORT",
        "exchange": "binance",
        "entry_price": 50000.0,
        "exit_price": 48000.0,
        "quantity": 0.002,
        "entry_time": datetime.utcnow(),
        "entry_commission": 0.1,
        "exit_commission": 0.096,
        "order_id": "exit-order-456",
        "close_reason": "stop_loss",
    }

    with patch(
        "shared.mysql_client.position_client.update_position", new_callable=AsyncMock
    ) as mock_update:
        await position_manager.close_position_record("test-short-pos-1", exit_result)

        assert mock_update.called
        # update_position(position_id, update_data) - second arg is update_data
        call_args = mock_update.call_args[0][1]
        assert call_args["status"] == "closed"
        assert "exit_price" in call_args
        assert "pnl" in call_args


# ============================================================================
# Risk Limit Configuration Tests
# ============================================================================


def test_set_risk_limits(position_manager):
    """Test setting risk management limits"""
    position_manager.set_risk_limits(
        max_position_size_pct=0.15,
        max_daily_loss_pct=0.08,
        max_portfolio_exposure_pct=0.9,
    )

    assert position_manager.max_position_size_pct == 0.15
    assert position_manager.max_daily_loss_pct == 0.08
    assert position_manager.max_portfolio_exposure_pct == 0.9


def test_set_portfolio_value(position_manager):
    """Test setting portfolio value"""
    position_manager.set_portfolio_value(20000.0)
    assert position_manager.total_portfolio_value == 20000.0


# ============================================================================
# Edge Cases and Error Handling
# ============================================================================


@pytest.mark.asyncio
async def test_update_position_with_string_prices(position_manager, sample_long_order):
    """Test updating position with string prices (from API responses)"""
    result = {
        "status": "filled",
        "fill_price": "50000.0",  # String price
        "amount": "0.001",  # String amount
    }

    await position_manager.update_position(sample_long_order, result)

    position = position_manager.get_position("BTCUSDT", "LONG")
    assert position is not None
    assert isinstance(position["avg_price"], float)
    assert position["avg_price"] == 50000.0


@pytest.mark.asyncio
async def test_update_position_zero_quantity_close(
    position_manager, sample_long_order, sample_fill_result
):
    """Test closing position with exact quantity match"""
    # Open position
    await position_manager.update_position(sample_long_order, sample_fill_result)

    # Close with exact quantity
    close_order = TradeOrder(
        position_id="test-long-pos-1",
        symbol="BTCUSDT",
        side="sell",
        type="market",
        amount=0.001,  # Exact match
        target_price=52000.0,
        position_side="LONG",
        exchange="binance",
    )
    close_result = {
        "status": "filled",
        "fill_price": 52000.0,
        "amount": 0.001,
    }

    with patch(
        "shared.mysql_client.position_client.close_position", new_callable=AsyncMock
    ):
        await position_manager.update_position(close_order, close_result)

        # Position should be removed
        position = position_manager.get_position("BTCUSDT", "LONG")
        assert position is None


@pytest.mark.asyncio
async def test_update_position_over_close(
    position_manager, sample_long_order, sample_fill_result
):
    """Test closing more than position quantity (edge case)"""
    # Open position with 0.001
    await position_manager.update_position(sample_long_order, sample_fill_result)

    # Try to close more than we have
    close_order = TradeOrder(
        position_id="test-long-pos-1",
        symbol="BTCUSDT",
        side="sell",
        type="market",
        amount=0.002,  # More than position quantity
        target_price=52000.0,
        position_side="LONG",
        exchange="binance",
    )
    close_result = {
        "status": "filled",
        "fill_price": 52000.0,
        "amount": 0.002,
    }

    with patch(
        "shared.mysql_client.position_client.close_position", new_callable=AsyncMock
    ):
        await position_manager.update_position(close_order, close_result)

        # Position should be closed (quantity becomes 0 or negative)
        position = position_manager.get_position("BTCUSDT", "LONG")
        assert position is None  # Position removed when quantity <= 0


class TestPositionManagerHelperMethods:
    """Test position manager helper methods for coverage"""

    def test_get_portfolio_summary_helper(self, position_manager):
        """Test getting portfolio summary"""
        summary = position_manager.get_portfolio_summary()
        assert isinstance(summary, dict)
        assert "total_positions" in summary or "positions" in summary

    def test_get_daily_pnl_helper(self, position_manager):
        """Test getting daily PnL"""
        pnl = position_manager.get_daily_pnl()
        assert isinstance(pnl, (int, float))

    def test_get_total_unrealized_pnl_helper(self, position_manager):
        """Test getting total unrealized PnL"""
        pnl = position_manager.get_total_unrealized_pnl()
        assert isinstance(pnl, (int, float))

    @pytest.mark.asyncio
    async def test_reset_daily_pnl_helper(self, position_manager):
        """Test resetting daily PnL"""
        position_manager.daily_pnl = 100.0
        with patch(
            "shared.mysql_client.position_client.update_daily_pnl",
            new_callable=AsyncMock,
        ):
            await position_manager.reset_daily_pnl()
            assert position_manager.daily_pnl == 0.0
