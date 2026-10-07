"""Entry fills are linked to their position and give it the economics of the fills (te#737).

First probe entry (ETHUSDT BUY 0.008): the user-data fill arrived before the strategy position existed
("no position identity; position write skipped"), and the position row written 2 s later by the REST path had
entry_trade_ids=[], commission_total=0.0 and the signal price as entry_price.
"""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contracts.order import TradeOrder
from tradeengine.dispatcher import Dispatcher
from tradeengine.entry_fills import EntryFillAggregator
from tradeengine.exchange_truth_store import ExchangeTruthStore
from tradeengine.position_manager import PositionManager


def _fill(trade_id, price, qty, fee, *, status="FILLED", client_id="cid-1"):
    return {
        "s": "ETHUSDT",
        "i": 16816283265,
        "X": status,
        "x": "TRADE",
        "S": "BUY",
        "o": "MARKET",
        "R": False,
        "L": str(price),
        "l": str(qty),
        "z": str(qty),
        "n": str(fee),
        "N": "USDT",
        "t": trade_id,
        "rp": "0",
        "T": 1759792162251,
        "c": client_id,
    }


@pytest.fixture
def dispatcher():
    d = Dispatcher.__new__(Dispatcher)
    d.logger = MagicMock()
    d.exchange_order_id_to_signal = {}
    d.order_to_signal = {}
    d.position_manager = MagicMock()
    d.position_manager.entry_fills = EntryFillAggregator()
    d.position_manager.persist_entry_fill = AsyncMock(return_value=True)
    d.settings = SimpleNamespace(te_entry_link_wait_seconds=0.6)
    return d


def _order(position_id="pos-1", client_order_id="cid-1"):
    return TradeOrder(
        symbol="ETHUSDT",
        side="buy",
        type="market",
        amount=0.008,
        target_price=2696.03,
        position_id=position_id,
        position_side="LONG",
        client_order_id=client_order_id,
        simulate=False,
    )


def _no_strategy_position():
    spm = MagicMock()
    spm.get_strategy_position_by_entry_order_id.return_value = None
    return patch("tradeengine.strategy_position_manager.strategy_position_manager", spm)


def _published(pub):
    pub.publish = AsyncMock(return_value=True)
    return pub


async def _drain(dispatcher):
    await asyncio.gather(*list(getattr(dispatcher, "_background_tasks", ())))


def test_a_vwap_price_and_the_summed_fee_come_from_every_trade_of_an_order():
    fills = EntryFillAggregator()

    fills.record("o1", "t1", 2696.5, 0.005, 0.004, "USDT")
    aggregate = fills.record("o1", "t2", 2697.0, 0.003, 0.003, "USDT")

    assert aggregate["entry_price"] == pytest.approx(
        (2696.5 * 0.005 + 2697.0 * 0.003) / 0.008
    )
    assert aggregate["quantity"] == pytest.approx(0.008)
    assert aggregate["commission_total"] == pytest.approx(0.007)
    assert aggregate["commission_asset"] == "USDT"
    assert aggregate["trade_ids"] == ["t1", "t2"]


def test_recording_the_same_trade_twice_changes_nothing():
    fills = EntryFillAggregator()
    fills.record("o1", "t1", 100.0, 1.0, 0.1, "USDT")

    again = fills.record("o1", "t1", 100.0, 1.0, 0.1, "USDT")

    assert again["quantity"] == 1.0
    assert again["commission_total"] == pytest.approx(0.1)
    assert again["trade_ids"] == ["t1"]


def test_unusable_fills_and_other_orders_are_ignored_and_memory_is_bounded():
    fills = EntryFillAggregator(max_orders=2)

    assert fills.record("o1", "t1", "bad", 1.0) is None
    assert fills.record("o1", "t1", 0, 1.0) is None
    fills.record("o1", "t1", 100.0, 1.0)
    fills.record("o2", "t1", 100.0, 1.0)
    fills.record("o3", "t1", 100.0, 1.0)

    assert fills.aggregate("o1") is None  # evicted: the oldest order
    assert fills.aggregate("o3") is not None


def test_a_fee_in_another_asset_is_not_summed_into_the_first():
    fills = EntryFillAggregator()
    fills.record("o1", "t1", 100.0, 1.0, 0.1, "USDT")

    aggregate = fills.record("o1", "t2", 100.0, 1.0, 0.0005, "BNB")

    assert aggregate["commission_total"] == pytest.approx(0.1)
    assert aggregate["commission_asset"] == "USDT"


@pytest.mark.asyncio
async def test_a_fill_that_beats_every_response_is_linked_through_the_client_order_id(
    dispatcher,
):
    dispatcher._register_pending_entry(_order())  # before the order is placed
    with patch("tradeengine.dispatcher.execution_event_publisher") as pub:
        _published(pub)
        with _no_strategy_position():
            await dispatcher._on_user_data_fill(_fill("t1", 2696.71, 0.008, 0.00862947))

    dispatcher.position_manager.persist_entry_fill.assert_awaited_once()
    saved = dispatcher.position_manager.persist_entry_fill.await_args.args[0]
    assert saved["position_id"] == "pos-1"
    assert saved["entry_price"] == pytest.approx(
        2696.71
    )  # the fill price, not the signal price
    assert saved["commission_total"] == pytest.approx(0.00862947)
    assert saved["entry_trade_ids"] == ["t1"]
    assert pub.publish.await_args.kwargs["extra"]["position_id"] == "pos-1"
    dispatcher.logger.error.assert_not_called()  # no "no position identity"
    assert not getattr(dispatcher, "_background_tasks", set())


@pytest.mark.asyncio
async def test_a_fill_without_a_client_order_id_is_linked_once_the_response_registers(
    dispatcher,
):
    order = _order(client_order_id=None)
    dispatcher.order_to_signal[order.order_id] = MagicMock(
        strategy_id="iceberg_detector", decision_id="dec-1"
    )
    with patch("tradeengine.dispatcher.execution_event_publisher") as pub:
        _published(pub)
        with _no_strategy_position():
            await dispatcher._on_user_data_fill(
                _fill("t1", 2696.71, 0.008, 0.00862947, client_id=None)
            )
            # The fill won the race: nothing could be written yet, and nothing is lost.
            dispatcher.position_manager.persist_entry_fill.assert_not_awaited()
            dispatcher._register_pending_fill_signal(order, {"order_id": "16816283265"})
            await _drain(dispatcher)

    dispatcher.position_manager.persist_entry_fill.assert_awaited_once()
    saved = dispatcher.position_manager.persist_entry_fill.await_args.args[0]
    assert saved["position_id"] == "pos-1"
    assert saved["entry_trade_ids"] == ["t1"]
    pub.publish.assert_awaited_once()  # the event was not duplicated by the deferred write
    dispatcher.logger.error.assert_not_called()


@pytest.mark.asyncio
async def test_a_fill_that_never_finds_its_position_still_ends_with_an_error_not_a_crash(
    dispatcher,
):
    dispatcher.settings = SimpleNamespace(te_entry_link_wait_seconds=0.0)
    with patch("tradeengine.dispatcher.execution_event_publisher") as pub:
        _published(pub)
        with _no_strategy_position():
            await dispatcher._on_user_data_fill(
                _fill("t1", 2696.71, 0.008, 0.01, client_id=None)
            )
            await _drain(dispatcher)

    dispatcher.position_manager.persist_entry_fill.assert_not_awaited()
    assert any(
        "no position identity" in str(call.args[0])
        for call in dispatcher.logger.error.call_args_list
    )


@pytest.mark.asyncio
async def test_a_partial_fill_sequence_gives_a_vwap_entry_price_and_the_summed_fees(
    dispatcher,
):
    dispatcher._register_pending_entry(_order())
    store = ExchangeTruthStore(on_fill=dispatcher._on_user_data_fill)
    store.set_on_trade(dispatcher._on_user_data_trade)

    with patch("tradeengine.dispatcher.execution_event_publisher") as pub:
        _published(pub)
        with _no_strategy_position():
            await store.update_order_from_trade_update(
                {"o": _fill("t1", 2696.5, 0.005, 0.004, status="PARTIALLY_FILLED")}
            )
            await store.update_order_from_trade_update(
                {"o": _fill("t2", 2697.0, 0.003, 0.003)}
            )

    saved = dispatcher.position_manager.persist_entry_fill.await_args.args[0]
    assert saved["entry_price"] == pytest.approx(
        (2696.5 * 0.005 + 2697.0 * 0.003) / 0.008
    )
    assert saved["commission_total"] == pytest.approx(0.007)
    assert saved["entry_trade_ids"] == ["t1", "t2"]
    assert saved["position_id"] == "pos-1"


def test_reduce_only_and_protective_trades_are_not_entry_fills(dispatcher):
    close = _fill("t9", 2700.0, 0.008, 0.01)
    close["R"] = True
    stop = _fill("t8", 2650.0, 0.008, 0.01)
    stop["o"] = "STOP_MARKET"

    dispatcher._on_user_data_trade(close)
    dispatcher._on_user_data_trade(stop)

    assert dispatcher.position_manager.entry_fills.aggregate("16816283265") is None


@pytest.mark.asyncio
async def test_the_position_record_written_after_the_fills_takes_their_economics():
    manager = PositionManager(exchange=MagicMock())
    manager.entry_fills.record("16816283265", "t1", 2696.71, 0.008, 0.00862947, "USDT")
    order = _order()
    result = {
        "order_id": "16816283265",
        "status": "NEW",
        "fill_price": None,  # a market order's REST response carries no fill yet
        "commission": 0.0,
        "trade_ids": [],
    }

    with patch(
        "tradeengine.position_manager.position_client.upsert_position",
        new_callable=AsyncMock,
        return_value=SimpleNamespace(ok=True, error=None),
    ) as upsert:
        await manager.create_position_record(order, result)

    row = upsert.await_args.args[0]
    assert row["entry_price"] == pytest.approx(2696.71)  # not the signal price 2696.03
    assert row["commission_total"] == pytest.approx(0.00862947)
    assert row["commission_asset"] == "USDT"
    assert row["entry_trade_ids"] == ["t1"]


@pytest.mark.asyncio
async def test_the_position_record_without_fills_yet_keeps_the_rest_response_values():
    manager = PositionManager(exchange=MagicMock())
    result = {"order_id": "99", "fill_price": None, "commission": 0.0, "trade_ids": []}

    with patch(
        "tradeengine.position_manager.position_client.upsert_position",
        new_callable=AsyncMock,
        return_value=SimpleNamespace(ok=True, error=None),
    ) as upsert:
        await manager.create_position_record(_order(), result)

    row = upsert.await_args.args[0]
    assert row["entry_price"] == pytest.approx(2696.03)
    assert row["entry_trade_ids"] == []


@pytest.mark.asyncio
async def test_a_logged_error_is_the_only_trace_of_an_unlinkable_fill(
    dispatcher, caplog
):
    caplog.set_level(logging.ERROR)
    fills = dispatcher.position_manager.entry_fills

    with patch("tradeengine.dispatcher.execution_event_publisher") as pub:
        _published(pub)
        with _no_strategy_position():
            dispatcher.settings = SimpleNamespace(te_entry_link_wait_seconds=0.0)
            await dispatcher._on_user_data_fill(
                _fill("t1", 2696.71, 0.008, 0.01, client_id=None)
            )
            await _drain(dispatcher)

    # The fill is still aggregated, so the position record written later takes it.
    assert fills.aggregate("16816283265")["trade_ids"] == ["t1"]


def test_the_exchange_sourced_position_cost_is_the_cost_basis_of_the_open_quantity():
    from datetime import UTC, datetime

    from tradeengine.exchange_truth_store import PositionSnapshot

    manager = PositionManager(exchange=MagicMock())
    store = MagicMock()
    store.get_positions.return_value = {
        ("ETHUSDT", "LONG"): PositionSnapshot(
            symbol="ETHUSDT",
            side="LONG",
            quantity=0.673,
            entry_price=2710.40,
            unrealized_pnl=0.0,
            updated_at=datetime.now(UTC),
        )
    }
    manager.exchange_truth_store = store

    with patch("tradeengine.position_manager.TE_EXCHANGE_TRUTH_STORE_ENABLED", "on"):
        position = manager.get_positions()[("ETHUSDT", "LONG")]

    assert position["total_cost"] == pytest.approx(0.673 * 2710.40)
    assert position["total_cost"] == position["total_value"]
