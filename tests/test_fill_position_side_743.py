"""``position_side`` on every filled / partial_fill execution event (petrosa-tradeengine#743).

The account trades in hedge mode, where a BUY can open a LONG or close a SHORT, so consumers that rebuild P&L
and rounds from the fills need the position side. The event ``side`` must also be the order side (buy/sell): the
strategy-position records keep the position side (LONG/SHORT) in ``side``, and the exits used to be published
with it, which data-manager's P&L calculator and round book cannot use.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contracts.order import TradeOrder
from tradeengine.dispatcher import (
    Dispatcher,
    OCOManager,
    exit_order_side,
    position_side_of,
)


def test_position_side_helpers():
    assert position_side_of("long") == "LONG"
    assert position_side_of("SHORT") == "SHORT"
    assert position_side_of("BOTH") == "BOTH"
    assert position_side_of("") is None
    assert position_side_of(None) is None
    assert position_side_of("buy") is None
    assert exit_order_side("LONG") == "sell"
    assert exit_order_side("short") == "buy"
    assert exit_order_side("BOTH") is None
    assert exit_order_side(None) is None


# --- entry fills: the user-data stream carries ``ps`` --------------------------------------------


@pytest.fixture
def dispatcher():
    d = Dispatcher.__new__(Dispatcher)
    d.logger = MagicMock()
    d.exchange_order_id_to_signal = {}
    d.position_manager = MagicMock()
    d.position_manager.persist_entry_fill = AsyncMock(return_value=True)
    return d


async def _user_data_fill(dispatcher, **overrides):
    order_obj = {
        "s": "BTCUSDT",
        "i": 11,
        "X": "FILLED",
        "S": "SELL",
        "o": "MARKET",
        "R": False,
        "L": "50000",
        "z": "0.01",
        "T": 1716163200123,
        **overrides,
    }
    fake_spm = MagicMock()
    fake_spm.get_strategy_position_by_entry_order_id.return_value = {
        "strategy_id": "s1",
        "decision_id": "d1",
        "entry_order_id": "11",
        "position_id": "p1",
    }
    with (
        patch("tradeengine.dispatcher.execution_event_publisher") as pub,
        patch(
            "tradeengine.strategy_position_manager.strategy_position_manager", fake_spm
        ),
    ):
        pub.publish = AsyncMock(return_value=True)
        await dispatcher._on_user_data_fill(order_obj)
    return pub.publish.await_args.kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize("ps", ["LONG", "SHORT"])
async def test_user_data_entry_fill_carries_the_position_side(dispatcher, ps):
    kw = await _user_data_fill(dispatcher, ps=ps)
    assert kw["event_type"] == "filled"
    assert kw["extra"]["position_side"] == ps
    assert kw["extra"]["side"] == "SELL"  # the order side, unchanged


@pytest.mark.asyncio
async def test_user_data_fill_without_ps_has_no_position_side(dispatcher):
    kw = await _user_data_fill(dispatcher)
    assert "position_side" not in kw["extra"]


# --- exit fills: OCO close, CIO exit_now and scale_out -------------------------------------------


@pytest.fixture
def oco_manager():
    m = OCOManager.__new__(OCOManager)
    m.logger = MagicMock()
    return m


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("position_side", "order_side"), [("LONG", "sell"), ("SHORT", "buy")]
)
async def test_oco_exit_fill_has_the_order_side_and_the_position_side(
    oco_manager, position_side, order_side
):
    closure = {
        "strategy_position_id": "sp-1",
        "decision_id": "d",
        "side": position_side,
    }
    with patch("tradeengine.dispatcher.execution_event_publisher") as pub:
        pub.publish = AsyncMock(return_value=True)
        await oco_manager._emit_oco_exit_filled_event(
            closure=closure,
            strategy_id="s1",
            symbol="BTCUSDT",
            exit_price=51000.0,
            filled_quantity=0.01,
            pnl=10.0,
            filled_order_id="x1",
            close_reason="take_profit",
        )
    extra = pub.publish.await_args.kwargs["extra"]
    assert extra["position_side"] == position_side
    assert extra["side"] == order_side


def _open_position(side: str) -> dict:
    return {
        "strategy_position_id": "sp-1",
        "strategy_id": "s1",
        "decision_id": "d",
        "symbol": "BTCUSDT",
        "side": side,
        "entry_quantity": 2.0,
        "entry_price": 50000.0,
        "status": "open",
        "client_order_id": "cid",
    }


@pytest.fixture
def cio_dispatcher():
    d = Dispatcher.__new__(Dispatcher)
    d.logger = MagicMock()
    d.exchange = AsyncMock()
    d.oco_manager = AsyncMock()
    return d


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("position_side", "order_side"), [("LONG", "sell"), ("SHORT", "buy")]
)
async def test_cio_exit_now_fill_has_the_order_side_and_the_position_side(
    cio_dispatcher, position_side, order_side
):
    cio_dispatcher.close_position_with_cleanup = AsyncMock(
        return_value={
            "position_closed": True,
            "close_result": {"order_id": "c1"},
            "status": "success",
        }
    )
    with (
        patch("tradeengine.dispatcher.strategy_position_manager") as spm,
        patch("tradeengine.dispatcher.execution_event_publisher") as pub,
    ):
        spm.get_strategy_positions_by_strategy.return_value = [
            _open_position(position_side)
        ]
        pub.publish = AsyncMock(return_value=True)
        await cio_dispatcher.handle_cio_position_lifecycle_action(
            action="exit_now", strategy_id="s1", decision_payload={"action": "EXIT_NOW"}
        )
    extra = pub.publish.await_args.kwargs["extra"]
    assert extra["position_side"] == position_side
    assert extra["side"] == order_side


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("position_side", "order_side"), [("LONG", "sell"), ("SHORT", "buy")]
)
async def test_cio_scale_out_fill_has_the_order_side_and_the_position_side(
    cio_dispatcher, position_side, order_side
):
    cio_dispatcher.exchange.execute = AsyncMock(
        return_value={"status": "FILLED", "order_id": "s1", "fill_price": 51000.0}
    )
    with (
        patch("tradeengine.dispatcher.strategy_position_manager") as spm,
        patch("tradeengine.dispatcher.execution_event_publisher") as pub,
        patch.dict("os.environ", {"CIO_SCALE_OUT_DEFAULT_FRACTION": "0.5"}),
    ):
        spm.get_strategy_positions_by_strategy.return_value = [
            _open_position(position_side)
        ]
        spm.close_strategy_position = AsyncMock(
            return_value={"position_status": "open"}
        )
        pub.publish = AsyncMock(return_value=True)
        await cio_dispatcher.handle_cio_position_lifecycle_action(
            action="scale_out",
            strategy_id="s1",
            decision_payload={"action": "SCALE_OUT"},
        )
    extra = pub.publish.await_args.kwargs["extra"]
    assert extra["position_side"] == position_side
    assert extra["side"] == order_side


# --- order-keyed events (the REST path) ----------------------------------------------------------


def _order(position_side):
    return TradeOrder(
        symbol="BTCUSDT",
        type="market",
        side="buy",
        amount=0.01,
        position_side=position_side,
        strategy_metadata={"strategy_id": "s1", "decision_id": "d"},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", ["filled", "partial_fill"])
async def test_order_keyed_fill_event_carries_the_order_position_side(event_type):
    d = Dispatcher.__new__(Dispatcher)
    d.logger = MagicMock()
    with patch("tradeengine.dispatcher.execution_event_publisher") as pub:
        pub.publish = AsyncMock(return_value=True)
        await d._emit_execution_event_from_order(
            _order("SHORT"),
            {"status": "filled", "order_id": "1", "fill_price": 100.0, "amount": 0.01},
            event_type=event_type,
            reason="r",
        )
    assert pub.publish.await_args.kwargs["extra"]["position_side"] == "SHORT"


@pytest.mark.asyncio
async def test_order_keyed_events_other_than_fills_and_orders_without_a_side_have_none():
    d = Dispatcher.__new__(Dispatcher)
    d.logger = MagicMock()
    with patch("tradeengine.dispatcher.execution_event_publisher") as pub:
        pub.publish = AsyncMock(return_value=True)
        await d._emit_execution_event_from_order(
            _order("LONG"), {"status": "new"}, event_type="placed", reason="r"
        )
        placed = pub.publish.await_args.kwargs["extra"]
        await d._emit_execution_event_from_order(
            _order(None),
            {"status": "filled", "order_id": "1", "fill_price": 100.0, "amount": 0.01},
            event_type="filled",
            reason="r",
        )
        unsided = pub.publish.await_args.kwargs["extra"]
    assert "position_side" not in placed
    assert "position_side" not in unsided
