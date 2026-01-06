"""
Integration tests for OCO order placement logic.

These tests verify ACTUAL order placement logic without heavy mocking:
1. Correct order sides for LONG vs SHORT positions
2. Proper price handling
3. Correct order types (STOP vs TAKE_PROFIT)
4. Actual order execution flow

Unlike unit tests, these tests capture and verify the actual TradeOrder objects
that are created and executed, ensuring the logic is correct.
"""

from typing import Any, Dict, List

import pytest

from contracts.order import OrderSide, OrderType, TradeOrder
from tradeengine.dispatcher import OCOManager


class ExchangeSpy:
    """
    Exchange spy that captures actual TradeOrder objects for verification.
    This allows us to verify the actual order placement logic without heavy mocking.
    """

    def __init__(self):
        self.executed_orders: list[TradeOrder] = []
        self.execute_results: dict[str, dict[str, Any]] = {}
        self._order_counter = 0

    async def execute(self, order: TradeOrder) -> dict[str, Any]:
        """Capture the order and return a result"""
        self.executed_orders.append(order)
        self._order_counter += 1
        # TradeOrder stores type and side as strings (converted from enum)
        order_id = f"test_order_{self._order_counter}_{order.type}"

        result = {
            "status": "filled",
            "order_id": order_id,
            "fill_price": order.target_price or 50000.0,
            "amount": order.amount,
            "symbol": order.symbol,
        }

        self.execute_results[order_id] = result
        return result

    def get_orders_by_type(self, order_type: OrderType) -> list[TradeOrder]:
        """Get all executed orders of a specific type"""
        # TradeOrder.type is stored as string, compare with enum value
        order_type_str = order_type.value
        return [o for o in self.executed_orders if o.type == order_type_str]

    def get_orders_by_side(self, side: OrderSide) -> list[TradeOrder]:
        """Get all executed orders with a specific side"""
        # TradeOrder.side is stored as string, compare with enum value
        side_str = side.value
        return [o for o in self.executed_orders if o.side == side_str]

    def clear(self):
        """Clear all captured orders"""
        self.executed_orders.clear()
        self.execute_results.clear()
        self._order_counter = 0


@pytest.fixture
def exchange_spy():
    """Create an exchange spy for capturing orders"""
    return ExchangeSpy()


@pytest.fixture
def oco_manager(exchange_spy):
    """Create OCO manager with spy exchange"""
    import logging

    logger = logging.getLogger("test_oco_integration")
    return OCOManager(exchange=exchange_spy, logger=logger)


@pytest.mark.asyncio
async def test_oco_placement_long_position_verifies_order_sides(
    oco_manager: OCOManager, exchange_spy: ExchangeSpy
):
    """
    Integration test: Verify OCO orders for LONG position have correct sides.

    For LONG positions:
    - Stop Loss should be SELL (to close the LONG position)
    - Take Profit should be SELL (to close the LONG position)
    """
    # Place OCO orders for LONG position
    result = await oco_manager.place_oco_orders(
        position_id="test_long_123",
        symbol="BTCUSDT",
        position_side="LONG",
        quantity=0.001,
        stop_loss_price=48000.0,
        take_profit_price=52000.0,
    )

    # Verify placement succeeded
    assert result["status"] == "success"
    assert "sl_order_id" in result
    assert "tp_order_id" in result

    # Verify exactly 2 orders were executed
    assert len(exchange_spy.executed_orders) == 2

    # Get the actual orders that were created
    sl_orders = exchange_spy.get_orders_by_type(OrderType.STOP)
    tp_orders = exchange_spy.get_orders_by_type(OrderType.TAKE_PROFIT)

    # Verify we have one of each type
    assert len(sl_orders) == 1, "Should have exactly one STOP order"
    assert len(tp_orders) == 1, "Should have exactly one TAKE_PROFIT order"

    sl_order = sl_orders[0]
    tp_order = tp_orders[0]

    # CRITICAL: Verify order sides are correct for LONG position
    # TradeOrder stores sides as strings, so compare with enum value
    assert sl_order.side == OrderSide.SELL.value, "Stop Loss for LONG must be SELL"
    assert tp_order.side == OrderSide.SELL.value, "Take Profit for LONG must be SELL"

    # Verify order types
    # TradeOrder stores types as strings, so compare with enum value
    assert sl_order.type == OrderType.STOP.value
    assert tp_order.type == OrderType.TAKE_PROFIT.value

    # Verify prices
    assert sl_order.target_price == 48000.0, "Stop loss price must match"
    assert tp_order.target_price == 52000.0, "Take profit price must match"

    # Verify quantities
    assert sl_order.amount == 0.001, "Stop loss quantity must match"
    assert tp_order.amount == 0.001, "Take profit quantity must match"

    # Verify position side
    assert sl_order.position_side == "LONG"
    assert tp_order.position_side == "LONG"

    # Verify reduce_only flag
    assert sl_order.reduce_only is True, "OCO orders must be reduce_only"
    assert tp_order.reduce_only is True, "OCO orders must be reduce_only"

    # Clean up
    await oco_manager.stop_monitoring()


@pytest.mark.asyncio
async def test_oco_placement_short_position_verifies_order_sides(
    oco_manager: OCOManager, exchange_spy: ExchangeSpy
):
    """
    Integration test: Verify OCO orders for SHORT position have correct sides.

    For SHORT positions:
    - Stop Loss should be BUY (to close the SHORT position)
    - Take Profit should be BUY (to close the SHORT position)
    """
    # Place OCO orders for SHORT position
    result = await oco_manager.place_oco_orders(
        position_id="test_short_456",
        symbol="ETHUSDT",
        position_side="SHORT",
        quantity=0.01,
        stop_loss_price=3060.0,  # Above entry for SHORT
        take_profit_price=2940.0,  # Below entry for SHORT
    )

    # Verify placement succeeded
    assert result["status"] == "success"
    assert "sl_order_id" in result
    assert "tp_order_id" in result

    # Verify exactly 2 orders were executed
    assert len(exchange_spy.executed_orders) == 2

    # Get the actual orders that were created
    sl_orders = exchange_spy.get_orders_by_type(OrderType.STOP)
    tp_orders = exchange_spy.get_orders_by_type(OrderType.TAKE_PROFIT)

    # Verify we have one of each type
    assert len(sl_orders) == 1, "Should have exactly one STOP order"
    assert len(tp_orders) == 1, "Should have exactly one TAKE_PROFIT order"

    sl_order = sl_orders[0]
    tp_order = tp_orders[0]

    # CRITICAL: Verify order sides are correct for SHORT position
    # TradeOrder stores sides as strings, so compare with enum value
    assert sl_order.side == OrderSide.BUY.value, "Stop Loss for SHORT must be BUY"
    assert tp_order.side == OrderSide.BUY.value, "Take Profit for SHORT must be BUY"

    # Verify order types
    # TradeOrder stores types as strings, so compare with enum value
    assert sl_order.type == OrderType.STOP.value
    assert tp_order.type == OrderType.TAKE_PROFIT.value

    # Verify prices
    assert sl_order.target_price == 3060.0, "Stop loss price must match"
    assert tp_order.target_price == 2940.0, "Take profit price must match"

    # Verify quantities
    assert sl_order.amount == 0.01, "Stop loss quantity must match"
    assert tp_order.amount == 0.01, "Take profit quantity must match"

    # Verify position side
    assert sl_order.position_side == "SHORT"
    assert tp_order.position_side == "SHORT"

    # Verify reduce_only flag
    assert sl_order.reduce_only is True, "OCO orders must be reduce_only"
    assert tp_order.reduce_only is True, "OCO orders must be reduce_only"

    # Clean up
    await oco_manager.stop_monitoring()


@pytest.mark.asyncio
async def test_oco_placement_verifies_price_handling_for_long(
    oco_manager: OCOManager, exchange_spy: ExchangeSpy
):
    """
    Integration test: Verify price handling for LONG positions.

    For LONG positions:
    - Stop Loss price should be BELOW entry price
    - Take Profit price should be ABOVE entry price
    """
    entry_price = 50000.0
    stop_loss_price = 48000.0  # 4% below entry
    take_profit_price = 52000.0  # 4% above entry

    # Place OCO orders
    result = await oco_manager.place_oco_orders(
        position_id="test_price_long",
        symbol="BTCUSDT",
        position_side="LONG",
        quantity=0.001,
        stop_loss_price=stop_loss_price,
        take_profit_price=take_profit_price,
    )

    assert result["status"] == "success"

    # Get orders
    sl_order = exchange_spy.get_orders_by_type(OrderType.STOP)[0]
    tp_order = exchange_spy.get_orders_by_type(OrderType.TAKE_PROFIT)[0]

    # Verify prices are set correctly
    assert sl_order.target_price == stop_loss_price
    assert sl_order.stop_loss == stop_loss_price
    assert tp_order.target_price == take_profit_price
    assert tp_order.take_profit == take_profit_price

    # Verify price relationships for LONG
    assert sl_order.target_price < entry_price, "SL must be below entry for LONG"
    assert tp_order.target_price > entry_price, "TP must be above entry for LONG"

    # Clean up
    await oco_manager.stop_monitoring()


@pytest.mark.asyncio
async def test_oco_placement_verifies_price_handling_for_short(
    oco_manager: OCOManager, exchange_spy: ExchangeSpy
):
    """
    Integration test: Verify price handling for SHORT positions.

    For SHORT positions:
    - Stop Loss price should be ABOVE entry price
    - Take Profit price should be BELOW entry price
    """
    entry_price = 3000.0
    stop_loss_price = 3060.0  # 2% above entry
    take_profit_price = 2940.0  # 2% below entry

    # Place OCO orders
    result = await oco_manager.place_oco_orders(
        position_id="test_price_short",
        symbol="ETHUSDT",
        position_side="SHORT",
        quantity=0.01,
        stop_loss_price=stop_loss_price,
        take_profit_price=take_profit_price,
    )

    assert result["status"] == "success"

    # Get orders
    sl_order = exchange_spy.get_orders_by_type(OrderType.STOP)[0]
    tp_order = exchange_spy.get_orders_by_type(OrderType.TAKE_PROFIT)[0]

    # Verify prices are set correctly
    assert sl_order.target_price == stop_loss_price
    assert sl_order.stop_loss == stop_loss_price
    assert tp_order.target_price == take_profit_price
    assert tp_order.take_profit == take_profit_price

    # Verify price relationships for SHORT
    assert sl_order.target_price > entry_price, "SL must be above entry for SHORT"
    assert tp_order.target_price < entry_price, "TP must be below entry for SHORT"

    # Clean up
    await oco_manager.stop_monitoring()


@pytest.mark.asyncio
async def test_oco_placement_verifies_order_execution_flow(
    oco_manager: OCOManager, exchange_spy: ExchangeSpy
):
    """
    Integration test: Verify the complete order execution flow.

    This test verifies that:
    1. Both orders are created with correct parameters
    2. Both orders are executed via exchange.execute()
    3. Order IDs are returned and stored correctly
    4. OCO pair is tracked in active_oco_pairs
    """
    position_id = "test_execution_flow"
    symbol = "BTCUSDT"
    position_side = "LONG"
    quantity = 0.001
    stop_loss_price = 48000.0
    take_profit_price = 52000.0

    # Place OCO orders
    result = await oco_manager.place_oco_orders(
        position_id=position_id,
        symbol=symbol,
        position_side=position_side,
        quantity=quantity,
        stop_loss_price=stop_loss_price,
        take_profit_price=take_profit_price,
    )

    # Verify result structure
    assert result["status"] == "success"
    assert "sl_order_id" in result
    assert "tp_order_id" in result
    sl_order_id = result["sl_order_id"]
    tp_order_id = result["tp_order_id"]

    # Verify both orders were executed
    assert len(exchange_spy.executed_orders) == 2

    # Verify order IDs match execution results
    assert sl_order_id in exchange_spy.execute_results
    assert tp_order_id in exchange_spy.execute_results

    # Verify OCO pair is tracked
    exchange_position_key = f"{symbol}_{position_side}"
    assert exchange_position_key in oco_manager.active_oco_pairs
    oco_list = oco_manager.active_oco_pairs[exchange_position_key]
    assert len(oco_list) > 0

    # Find our OCO pair
    oco_info = None
    for oco in oco_list:
        if oco["position_id"] == position_id:
            oco_info = oco
            break

    assert oco_info is not None, "OCO pair should be tracked"
    assert oco_info["sl_order_id"] == sl_order_id
    assert oco_info["tp_order_id"] == tp_order_id
    assert oco_info["symbol"] == symbol
    assert oco_info["position_side"] == position_side
    assert oco_info["quantity"] == quantity
    assert oco_info["status"] == "active"

    # Verify monitoring was started
    assert oco_manager.monitoring_active is True

    # Clean up
    await oco_manager.stop_monitoring()


@pytest.mark.asyncio
async def test_oco_placement_verifies_quantity_consistency(
    oco_manager: OCOManager, exchange_spy: ExchangeSpy
):
    """
    Integration test: Verify quantity is consistent across both orders.

    Both SL and TP orders must have the same quantity as the position.
    """
    quantity = 0.005  # Test with different quantity

    result = await oco_manager.place_oco_orders(
        position_id="test_quantity",
        symbol="BTCUSDT",
        position_side="LONG",
        quantity=quantity,
        stop_loss_price=48000.0,
        take_profit_price=52000.0,
    )

    assert result["status"] == "success"

    # Get orders
    sl_order = exchange_spy.get_orders_by_type(OrderType.STOP)[0]
    tp_order = exchange_spy.get_orders_by_type(OrderType.TAKE_PROFIT)[0]

    # Verify quantities match
    assert sl_order.amount == quantity
    assert tp_order.amount == quantity
    assert sl_order.amount == tp_order.amount, "SL and TP must have same quantity"

    # Clean up
    await oco_manager.stop_monitoring()


@pytest.mark.asyncio
async def test_oco_placement_handles_multiple_strategies_same_position(
    oco_manager: OCOManager, exchange_spy: ExchangeSpy
):
    """
    Integration test: Verify multiple OCO pairs can exist for same exchange position.

    This tests the multi-strategy OCO tracking feature.
    """
    symbol = "BTCUSDT"
    position_side = "LONG"
    exchange_position_key = f"{symbol}_{position_side}"

    # Place first OCO pair
    result1 = await oco_manager.place_oco_orders(
        position_id="pos1",
        symbol=symbol,
        position_side=position_side,
        quantity=0.001,
        stop_loss_price=48000.0,
        take_profit_price=52000.0,
        strategy_position_id="strategy1_pos1",
    )

    assert result1["status"] == "success"

    # Place second OCO pair for same exchange position
    result2 = await oco_manager.place_oco_orders(
        position_id="pos2",
        symbol=symbol,
        position_side=position_side,
        quantity=0.002,
        stop_loss_price=47500.0,
        take_profit_price=52500.0,
        strategy_position_id="strategy2_pos1",
    )

    assert result2["status"] == "success"

    # Verify both pairs are tracked
    assert exchange_position_key in oco_manager.active_oco_pairs
    oco_list = oco_manager.active_oco_pairs[exchange_position_key]
    assert len(oco_list) == 2, "Should have 2 OCO pairs for same exchange position"

    # Verify each pair has unique order IDs
    order_ids = set()
    for oco in oco_list:
        assert oco["sl_order_id"] not in order_ids
        assert oco["tp_order_id"] not in order_ids
        order_ids.add(oco["sl_order_id"])
        order_ids.add(oco["tp_order_id"])

    # Verify strategy tracking
    strategy_ids = [oco.get("strategy_position_id") for oco in oco_list]
    assert "strategy1_pos1" in strategy_ids
    assert "strategy2_pos1" in strategy_ids

    # Clean up
    await oco_manager.stop_monitoring()
