from unittest.mock import AsyncMock, patch

import pytest

from contracts.order import OrderStatus, TradeOrder
from tradeengine.dispatcher import Dispatcher
from tradeengine.metrics import manual_halt_active


def _order(*, reduce_only: bool) -> TradeOrder:
    return TradeOrder(
        symbol="BTCUSDT",
        type="market",
        side="sell" if reduce_only else "buy",
        amount=0.01,
        order_id="manual-halt-test",
        status=OrderStatus.PENDING,
        reduce_only=reduce_only,
        simulate=True,
    )


@pytest.mark.asyncio
async def test_manual_halt_rejects_new_entry_without_running_risk_checks() -> None:
    dispatcher = Dispatcher()
    dispatcher._emit_execution_event_from_order = AsyncMock()

    with patch.dict("os.environ", {"TRADING_HALT": "true"}):
        result = await dispatcher._execute_order_with_consensus(
            _order(reduce_only=False)
        )

    assert result["status"] == "rejected"
    assert result["reason"] == "manual_halt"
    assert dispatcher._emit_execution_event_from_order.await_args.kwargs["reason"] == (
        "manual_halt"
    )
    assert manual_halt_active._value.get() == 1


@pytest.mark.asyncio
async def test_manual_halt_allows_reduce_only_orders() -> None:
    dispatcher = Dispatcher()
    dispatcher._apply_probe_sizing = AsyncMock(return_value=None)
    dispatcher._apply_risk_sizing = AsyncMock(return_value=None)
    dispatcher._apply_max_usd_cap = AsyncMock(return_value=None)
    dispatcher._apply_margin_precheck = AsyncMock(return_value=None)
    dispatcher._apply_stop_floor_check = AsyncMock(return_value=None)
    dispatcher.position_manager.check_position_limits = AsyncMock(return_value=True)
    dispatcher.position_manager.check_daily_loss_limits = AsyncMock(return_value=True)
    dispatcher.check_leverage_bound = AsyncMock(return_value=(True, ""))
    dispatcher._apply_order_leverage = AsyncMock(return_value=True)
    dispatcher.execute_order = AsyncMock(return_value={"status": "filled"})
    dispatcher._register_pending_fill_signal = lambda order, result: None

    with patch.dict("os.environ", {"TRADING_HALT": "true"}):
        result = await dispatcher._execute_order_with_consensus(
            _order(reduce_only=True)
        )

    assert result["status"] == "filled"
    dispatcher.execute_order.assert_awaited_once()
    assert manual_halt_active._value.get() == 1


@pytest.mark.asyncio
async def test_unhalted_entry_is_allowed() -> None:
    dispatcher = Dispatcher()
    dispatcher._apply_probe_sizing = AsyncMock(return_value=None)
    dispatcher._apply_risk_sizing = AsyncMock(return_value=None)
    dispatcher._apply_max_usd_cap = AsyncMock(return_value=None)
    dispatcher._apply_margin_precheck = AsyncMock(return_value=None)
    dispatcher._apply_stop_floor_check = AsyncMock(return_value=None)
    dispatcher.position_manager.check_position_limits = AsyncMock(return_value=True)
    dispatcher.position_manager.check_daily_loss_limits = AsyncMock(return_value=True)
    dispatcher.check_leverage_bound = AsyncMock(return_value=(True, ""))
    dispatcher._apply_order_leverage = AsyncMock(return_value=True)
    dispatcher.execute_order = AsyncMock(return_value={"status": "filled"})
    dispatcher._register_pending_fill_signal = lambda order, result: None

    with patch.dict("os.environ", {"TRADING_HALT": "false"}):
        result = await dispatcher._execute_order_with_consensus(
            _order(reduce_only=False)
        )

    assert result["status"] == "filled"
    dispatcher.execute_order.assert_awaited_once()
    assert manual_halt_active._value.get() == 0


def test_manual_halt_state_is_reversible() -> None:
    from tradeengine.manual_halt import manual_halt_state

    with patch.dict("os.environ", {"TRADING_HALT": "true"}):
        assert manual_halt_state()["active"] is True
        assert manual_halt_active._value.get() == 1

    with patch.dict("os.environ", {"TRADING_HALT": "false"}):
        assert manual_halt_state()["active"] is False
        assert manual_halt_active._value.get() == 0
