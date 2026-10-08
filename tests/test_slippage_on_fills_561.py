"""Fill events carry slippage_bp on the live fill paths (petrosa-data-manager#561).

The live entries are user-data-stream fills and the live exits are OCO exits; neither went through the
order-keyed path that computes the cost telemetry, so no fill event carried ``slippage_bp``.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tradeengine.services.cost_telemetry import fill_cost_fields

# --- the pure helper -----------------------------------------------------------------------------


def fields(**kw):
    base = {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "order_type": "MARKET",
        "fill_price": 100.05,
        "quantity": 1,
        "fee": 0.04,
        "fee_asset": "USDT",
        "intended_price": 100.0,
    }
    base.update(kw)
    return fill_cost_fields(**base)


def test_an_entry_buy_above_the_signal_price_is_adverse_positive():
    f = fields()
    assert f["slippage_bp"] == pytest.approx(5.0)
    assert f["intended_price"] == 100.0
    assert f["intended_price_source"] == "signal_price"
    assert f["fee_bp"] == pytest.approx(0.04 / 100.05 * 10_000)


def test_an_entry_sell_below_the_signal_price_is_adverse_positive():
    assert fields(side="SELL", fill_price=99.95)["slippage_bp"] == pytest.approx(5.0)
    assert fields(side="SELL", fill_price=100.05)["slippage_bp"] == pytest.approx(-5.0)


def test_a_limit_entry_is_measured_against_its_limit_price():
    f = fields(order_type="LIMIT", intended_price=100.0, fill_price=100.0)
    assert f["slippage_bp"] == pytest.approx(0.0)
    assert f["intended_price_source"] == "limit_price"


def test_a_stop_loss_exit_is_measured_against_its_trigger():
    # a LONG closed by a stop at 98: the closing SELL filled at 97.9, worse than the trigger
    f = fields(
        side="SELL",
        order_type="MARKET",
        trigger="stop_loss",
        intended_price=98.0,
        fill_price=97.9,
        reduce_only=True,
    )
    assert f["slippage_bp"] == pytest.approx((98.0 - 97.9) / 98.0 * 10_000)
    assert f["intended_price_source"] == "stop_trigger"


def test_a_take_profit_exit_filled_better_than_its_trigger_is_negative():
    f = fields(
        side="SELL",
        trigger="take_profit",
        intended_price=102.0,
        fill_price=102.1,
        reduce_only=True,
    )
    assert f["slippage_bp"] < 0
    assert f["intended_price_source"] == "take_profit_trigger"


def test_without_an_intended_price_the_fields_are_present_and_null():
    f = fields(intended_price=None)
    assert f["slippage_bp"] is None and f["intended_price"] is None
    assert set(f) == {
        "intended_price",
        "intended_price_source",
        "slippage_bp",
        "fee_bp",
    }


def test_a_missing_fill_price_is_null_not_an_error():
    assert fields(fill_price=None)["slippage_bp"] is None


def test_an_unknown_fee_leaves_fee_bp_null():
    assert fields(fee=None)["fee_bp"] is None


# --- the user-data entry fill --------------------------------------------------------------------


@pytest.fixture
def dispatcher():
    from tradeengine.dispatcher import Dispatcher

    d = Dispatcher.__new__(Dispatcher)
    d.logger = MagicMock()
    d.exchange_order_id_to_signal = {}
    d.position_manager = MagicMock()
    d.position_manager.persist_entry_fill = AsyncMock(return_value=True)
    return d


ORDER = {
    "s": "BTCUSDT",
    "i": 283194212,
    "X": "FILLED",
    "S": "BUY",
    "o": "MARKET",
    "R": False,
    "L": "100.05",
    "z": "0.010",
    "n": "0.02",
    "N": "USDT",
    "rp": "0",
    "T": 1716163200123,
}


async def run_entry(dispatcher, signal=None, order=None):
    spm = MagicMock()
    spm.get_strategy_position_by_entry_order_id.return_value = {
        "strategy_id": "s1",
        "decision_id": "d1",
        "position_id": "p1",
    }
    if signal is not None:
        dispatcher.exchange_order_id_to_signal["283194212"] = signal
    with (
        patch("tradeengine.dispatcher.execution_event_publisher") as pub,
        patch("tradeengine.strategy_position_manager.strategy_position_manager", spm),
    ):
        pub.publish = AsyncMock(return_value=True)
        await dispatcher._on_user_data_fill(order or ORDER)
    return pub.publish.await_args.kwargs["extra"]


@pytest.mark.asyncio
async def test_an_entry_fill_carries_slippage_against_the_signal_price(dispatcher):
    signal = SimpleNamespace(
        strategy_id="s1", decision_id="d1", current_price=100.0, target_price=None
    )
    extra = await run_entry(dispatcher, signal)
    assert extra["slippage_bp"] == pytest.approx(5.0)
    assert extra["intended_price"] == 100.0
    assert extra["intended_price_source"] == "signal_price"
    assert extra["fee"] == 0.02  # the event's own fee is untouched


@pytest.mark.asyncio
async def test_a_limit_entry_fill_is_measured_against_the_signal_target(dispatcher):
    signal = SimpleNamespace(
        strategy_id="s1", decision_id="d1", current_price=100.4, target_price=100.0
    )
    extra = await run_entry(dispatcher, signal, {**ORDER, "o": "LIMIT"})
    assert extra["intended_price_source"] == "limit_price"
    assert extra["slippage_bp"] == pytest.approx(5.0)


@pytest.mark.asyncio
async def test_an_entry_fill_with_no_signal_has_null_slippage_fields(dispatcher):
    extra = await run_entry(dispatcher)
    assert extra["slippage_bp"] is None and extra["intended_price"] is None
    assert "intended_price_source" in extra and "fee_bp" in extra


# --- the OCO exit fill ---------------------------------------------------------------------------


@pytest.fixture
def oco_manager():
    from tradeengine.dispatcher import OCOManager

    m = OCOManager.__new__(OCOManager)
    m.logger = MagicMock()
    return m


async def run_exit(oco_manager, **kw):
    params = {
        "closure": {"decision_id": "d1", "side": "LONG"},
        "strategy_id": "s1",
        "symbol": "BTCUSDT",
        "exit_price": 97.9,
        "filled_quantity": 0.01,
        "pnl": -1.0,
        "filled_order_id": "x1",
        "close_reason": "stop_loss",
        "fee": 0.02,
        "fee_asset": "USDT",
        "trigger_price": 98.0,
    }
    params.update(kw)
    with patch("tradeengine.dispatcher.execution_event_publisher") as pub:
        pub.publish = AsyncMock(return_value=True)
        await oco_manager._emit_oco_exit_filled_event(**params)
    return pub.publish.await_args.kwargs["extra"]


@pytest.mark.asyncio
async def test_a_stop_loss_exit_carries_slippage_against_the_stop_price(oco_manager):
    extra = await run_exit(oco_manager)
    assert extra["slippage_bp"] == pytest.approx((98.0 - 97.9) / 98.0 * 10_000)
    assert extra["intended_price"] == 98.0
    assert extra["intended_price_source"] == "stop_trigger"
    assert extra["fee"] == 0.02 and extra["close_reason"] == "stop_loss"


@pytest.mark.asyncio
async def test_a_take_profit_exit_carries_slippage_against_the_take_profit_price(
    oco_manager,
):
    extra = await run_exit(
        oco_manager, close_reason="take_profit", exit_price=102.0, trigger_price=102.0
    )
    assert extra["slippage_bp"] == pytest.approx(0.0)
    assert extra["intended_price_source"] == "take_profit_trigger"


@pytest.mark.asyncio
async def test_an_exit_without_a_trigger_price_has_null_slippage(oco_manager):
    extra = await run_exit(oco_manager, trigger_price=None)
    assert extra["slippage_bp"] is None and extra["intended_price"] is None


@pytest.mark.asyncio
async def test_an_exit_that_was_not_a_stop_or_take_profit_has_null_slippage(
    oco_manager,
):
    extra = await run_exit(oco_manager, close_reason="manual", trigger_price=98.0)
    assert extra["slippage_bp"] is None


@pytest.mark.asyncio
async def test_an_exit_whose_fill_is_unknown_has_null_slippage(oco_manager):
    extra = await run_exit(oco_manager, exit_price=None, pnl=None)
    assert extra["slippage_bp"] is None
