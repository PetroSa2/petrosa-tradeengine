"""
Regression tests for tradeengine#531 — no `filled` execution events ever
emitted in production.

Root cause: Binance Futures returns ``status: "NEW"`` on the synchronous
create-order response (including for MARKET orders); the fill arrives async.
The dispatcher's ``execute_order`` status-map therefore always emits ``placed``
and the ``filled`` branch is dead. Fills are published from two async paths
instead:

  1. ``Dispatcher._on_user_data_fill`` — entry fills via the user-data stream
     ``ORDER_TRADE_UPDATE`` (status FILLED).
  2. ``OCOManager._emit_oco_exit_filled_event`` — SL/TP exit fills after the
     ``futures_get_order`` fetch in the OCO close path.

These tests use a fixture reproducing Binance's real ``status: "NEW"``
create-order response, NOT the simulator's synchronous ``"filled"`` — the
pre-#531 suite passed precisely because it did not.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tradeengine.exchange_truth_store import ExchangeTruthStore

# ---------------------------------------------------------------------------
# AC: real Binance create-order response is status="NEW", mapped to `placed`.
# The synchronous path can NEVER emit `filled` for a market order.
# ---------------------------------------------------------------------------


def _binance_market_create_response() -> dict:
    """A verbatim-shaped Binance Futures create-order response for a MARKET
    order: status is NEW even though the order fills moments later async."""
    return {
        "orderId": 283194212,
        "symbol": "BTCUSDT",
        "status": "NEW",  # <-- the crux of #531
        "clientOrderId": "x-abc",
        "price": "0",
        "avgPrice": "0.00000",
        "origQty": "0.010",
        "executedQty": "0",
        "type": "MARKET",
        "side": "BUY",
    }


@pytest.fixture
def dispatcher():
    from tradeengine.dispatcher import Dispatcher

    d = Dispatcher.__new__(Dispatcher)
    d.logger = MagicMock()
    return d


def test_binance_market_create_response_status_is_new_not_filled():
    """Documents the root cause: create-order returns NEW, so the synchronous
    map cannot yield `filled`."""
    resp = _binance_market_create_response()
    assert resp["status"] == "NEW"
    assert resp["executedQty"] == "0"


# ---------------------------------------------------------------------------
# ExchangeTruthStore fires on_fill only on FILLED status.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_truth_store_invokes_on_fill_only_when_filled():
    calls: list[dict] = []

    async def _cb(order_obj: dict) -> None:
        calls.append(order_obj)

    store = ExchangeTruthStore(on_fill=_cb)

    # NEW status → snapshot updated, NO callback.
    await store.update_order_from_trade_update(
        {"o": {"s": "BTCUSDT", "i": 1, "X": "NEW", "S": "BUY", "q": "0.01", "p": "0"}}
    )
    assert calls == []

    # FILLED status → callback fired with the raw `o` payload.
    filled = {
        "s": "BTCUSDT",
        "i": 1,
        "X": "FILLED",
        "S": "BUY",
        "z": "0.01",
        "L": "50000",
    }
    await store.update_order_from_trade_update({"o": filled})
    assert len(calls) == 1
    assert calls[0]["X"] == "FILLED"
    assert calls[0]["i"] == 1


@pytest.mark.asyncio
async def test_truth_store_on_fill_failure_does_not_raise():
    async def _boom(order_obj: dict) -> None:
        raise RuntimeError("publisher down")

    store = ExchangeTruthStore(on_fill=_boom)
    # Must not propagate — truth-store bookkeeping is protected.
    await store.update_order_from_trade_update(
        {"o": {"s": "BTCUSDT", "i": 2, "X": "FILLED", "S": "SELL", "z": "1", "L": "10"}}
    )


@pytest.mark.asyncio
async def test_truth_store_set_on_fill_registers_callback():
    calls: list[dict] = []

    async def _cb(order_obj: dict) -> None:
        calls.append(order_obj)

    store = ExchangeTruthStore()
    store.set_on_fill(_cb)
    await store.update_order_from_trade_update(
        {
            "o": {
                "s": "ETHUSDT",
                "i": 3,
                "X": "FILLED",
                "S": "BUY",
                "z": "2",
                "L": "3000",
            }
        }
    )
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# Dispatcher._on_user_data_fill — entry fill publishes `filled`.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_user_data_fill_publishes_filled_with_recovered_context(dispatcher):
    order_obj = {
        "s": "BTCUSDT",
        "i": 283194212,
        "X": "FILLED",
        "S": "BUY",
        "o": "MARKET",
        "R": False,
        "L": "50000.5",
        "z": "0.010",
        "n": "0.02",
        "N": "USDT",
        "rp": "0",
        "T": 1716163200123,
    }

    fake_spm = MagicMock()
    fake_spm.get_strategy_position_by_entry_order_id.return_value = {
        "strategy_id": "rsi_reversal",
        "decision_id": "dec-ENTRY",
        "entry_order_id": "283194212",
    }

    with (
        patch("tradeengine.dispatcher.execution_event_publisher") as pub,
        patch(
            "tradeengine.strategy_position_manager.strategy_position_manager",
            fake_spm,
        ),
    ):
        pub.publish = AsyncMock(return_value=True)
        await dispatcher._on_user_data_fill(order_obj)

    pub.publish.assert_awaited_once()
    kw = pub.publish.await_args.kwargs
    assert kw["event_type"] == "filled"
    assert kw["strategy_id"] == "rsi_reversal"
    assert kw["decision_id"] == "dec-ENTRY"
    assert kw["order_id"] == "283194212"
    assert kw["reason"] == "user_data_stream_fill"
    assert kw["extra"]["fill_price"] == 50000.5
    assert kw["extra"]["price"] == 50000.5
    assert kw["extra"]["fill_quantity"] == 0.010
    assert kw["extra"]["fill_qty"] == 0.010
    assert kw["extra"]["fee"] == 0.02
    assert kw["extra"]["fee_asset"] == "USDT"
    assert kw["extra"]["symbol"] == "BTCUSDT"
    assert kw["extra"]["side"] == "BUY"
    assert kw["extra"]["fill_time"] == "2024-05-20T00:00:00.123000+00:00"


@pytest.mark.asyncio
async def test_on_user_data_fill_skips_reduce_only_exit(dispatcher):
    """SL/TP exits are reduce-only; the OCO path owns their `filled` event.
    Skipping here prevents a duplicate."""
    order_obj = {
        "s": "BTCUSDT",
        "i": 999,
        "X": "FILLED",
        "S": "SELL",
        "o": "STOP_MARKET",
        "R": True,
        "L": "49000",
        "z": "0.01",
    }
    with patch("tradeengine.dispatcher.execution_event_publisher") as pub:
        pub.publish = AsyncMock(return_value=True)
        await dispatcher._on_user_data_fill(order_obj)
    pub.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_user_data_fill_publisher_error_does_not_raise(dispatcher):
    order_obj = {
        "s": "BTCUSDT",
        "i": 5,
        "X": "FILLED",
        "S": "BUY",
        "o": "MARKET",
        "R": False,
        "L": "1",
        "z": "1",
    }
    fake_spm = MagicMock()
    fake_spm.get_strategy_position_by_entry_order_id.return_value = None
    with (
        patch("tradeengine.dispatcher.execution_event_publisher") as pub,
        patch(
            "tradeengine.strategy_position_manager.strategy_position_manager",
            fake_spm,
        ),
    ):
        pub.publish = AsyncMock(side_effect=RuntimeError("nats down"))
        await dispatcher._on_user_data_fill(order_obj)  # must not raise
    dispatcher.logger.warning.assert_called()


# ---------------------------------------------------------------------------
# OCOManager._emit_oco_exit_filled_event — exit fill publishes `filled`.
# ---------------------------------------------------------------------------


@pytest.fixture
def oco_manager():
    from tradeengine.dispatcher import OCOManager

    m = OCOManager.__new__(OCOManager)
    m.logger = MagicMock()
    return m


@pytest.mark.asyncio
async def test_oco_exit_emits_filled_with_pnl_and_decision_id(oco_manager):
    closure = {
        "strategy_position_id": "sp-1",
        "decision_id": "dec-EXIT",
        "side": "LONG",
    }
    with patch("tradeengine.dispatcher.execution_event_publisher") as pub:
        pub.publish = AsyncMock(return_value=True)
        await oco_manager._emit_oco_exit_filled_event(
            closure=closure,
            strategy_id="rsi_reversal",
            symbol="BTCUSDT",
            exit_price=51000.0,
            filled_quantity=0.01,
            pnl=10.0,
            filled_order_id="binance-exit-1",
            close_reason="take_profit",
        )
    pub.publish.assert_awaited_once()
    kw = pub.publish.await_args.kwargs
    assert kw["event_type"] == "filled"
    assert kw["strategy_id"] == "rsi_reversal"
    assert kw["decision_id"] == "dec-EXIT"
    assert kw["order_id"] == "binance-exit-1"
    assert kw["reason"] == "oco_exit_take_profit"
    assert kw["extra"]["fill_price"] == 51000.0
    assert kw["extra"]["price"] == 51000.0
    assert kw["extra"]["fill_quantity"] == 0.01
    assert kw["extra"]["pnl"] == 10.0
    assert kw["extra"]["close_reason"] == "take_profit"
    assert kw["extra"]["symbol"] == "BTCUSDT"


@pytest.mark.asyncio
async def test_oco_exit_warns_when_decision_id_missing(oco_manager):
    """Without a decision_id the consumer drops the event — must warn."""
    with patch("tradeengine.dispatcher.execution_event_publisher") as pub:
        pub.publish = AsyncMock(return_value=True)
        await oco_manager._emit_oco_exit_filled_event(
            closure={"strategy_position_id": "sp-2", "decision_id": None},
            strategy_id="momentum",
            symbol="ETHUSDT",
            exit_price=3000.0,
            filled_quantity=1.0,
            pnl=-5.0,
            filled_order_id="x",
            close_reason="stop_loss",
        )
    oco_manager.logger.warning.assert_called()
    # Event is still published (defence in depth) — consumer decides.
    pub.publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_oco_exit_publisher_error_does_not_raise(oco_manager):
    with patch("tradeengine.dispatcher.execution_event_publisher") as pub:
        pub.publish = AsyncMock(side_effect=RuntimeError("nats down"))
        await oco_manager._emit_oco_exit_filled_event(
            closure={"decision_id": "d"},
            strategy_id="s",
            symbol="BTCUSDT",
            exit_price=1.0,
            filled_quantity=1.0,
            pnl=0.0,
            filled_order_id="o",
            close_reason="take_profit",
        )  # must not raise
    oco_manager.logger.warning.assert_called()
