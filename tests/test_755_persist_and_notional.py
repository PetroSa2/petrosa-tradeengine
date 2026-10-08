"""Entry fills persist and fresh exchange snapshots are priced (petrosa-tradeengine#755).

Production, 2026-10-08 01:15Z, the first trades after the TA bot fix: every entry fill failed to persist
(``Object of type BinanceFuturesExchange is not JSON serializable``) and an entry 8 s after another was rejected
``refresh_failure`` (a fresh ACCOUNT_UPDATE snapshot has no mark price and no notional).
"""

import json
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tradeengine.dispatcher import Dispatcher, exchange_name_of
from tradeengine.exchange_truth_store import ExchangeTruthStore, PositionSnapshot
from tradeengine.json_safe import json_safe
from tradeengine.position_manager import PositionManager


class BinanceFuturesExchange:  # the live client object: not JSON serializable
    pass


# --- the payload -----------------------------------------------------------------------------------


def test_json_safe_makes_a_client_object_text_and_keeps_scalars():
    payload = {
        "exchange": BinanceFuturesExchange(),
        "quantity": 100.0,
        "n": 3,
        "ok": True,
        "none": None,
        "when": datetime(2026, 10, 8, 1, 15, tzinfo=UTC),
        "fee": Decimal("0.0123"),
        "ids": ("a", "b"),
        "nested": {"x": [BinanceFuturesExchange(), 1]},
    }
    safe = json_safe(payload)
    json.dumps(safe)  # must not raise
    assert safe["quantity"] == 100.0 and safe["n"] == 3 and safe["ok"] is True
    assert safe["none"] is None
    assert safe["when"] == "2026-10-08T01:15:00+00:00"
    assert safe["fee"] == 0.0123
    assert safe["ids"] == ["a", "b"]
    assert isinstance(safe["exchange"], str)
    assert safe["nested"]["x"][1] == 1
    assert (
        payload["exchange"].__class__ is BinanceFuturesExchange
    )  # the input is not mutated


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("binance", "binance"),
        (" Binance ", "binance"),
        (None, "binance"),
        ("", "binance"),
        (BinanceFuturesExchange(), "binance"),
    ],
)
def test_the_exchange_is_named_never_passed_as_an_object(value, expected):
    assert exchange_name_of(value) == expected


@pytest.mark.asyncio
async def test_an_entry_fill_with_a_client_object_in_it_persists_on_the_first_attempt():
    pm = PositionManager(exchange=MagicMock())
    seen = {}

    async def upsert(data):
        seen["json"] = json.dumps(
            data
        )  # what the real client does: raises on the exchange object
        return MagicMock(ok=True, error=None, reason=None)

    with (
        patch(
            "tradeengine.position_manager.position_client.upsert_position",
            side_effect=upsert,
        ),
        patch(
            "tradeengine.position_manager.position_client.update_daily_pnl",
            new_callable=AsyncMock,
        ),
        patch("tradeengine.position_manager.persist_retry_queue.enqueue") as enqueue,
    ):
        ok = await pm.persist_entry_fill(
            {
                "position_id": "eca45846",
                "symbol": "XRPUSDT",
                "order_id": "3516737664",
                "trade_id": "1",
                "commission": 0.01,
                "exchange": BinanceFuturesExchange(),
            }
        )
    assert ok is True
    enqueue.assert_not_called()  # retry queue depth stays 0
    assert json.loads(seen["json"])["symbol"] == "XRPUSDT"


@pytest.mark.asyncio
async def test_the_user_data_entry_fill_sends_the_exchange_name():
    d = Dispatcher.__new__(Dispatcher)
    d.logger = MagicMock()
    d.exchange = BinanceFuturesExchange()  # self.exchange is the client object
    d.exchange_order_id_to_signal = {}
    d.position_manager = MagicMock()
    d.position_manager.persist_entry_fill = AsyncMock(return_value=True)
    order_obj = {
        "s": "XRPUSDT",
        "i": 3516737664,
        "X": "FILLED",
        "S": "BUY",
        "o": "MARKET",
        "R": False,
        "L": "2.5",
        "z": "100",
        "n": "0.1",
        "N": "USDT",
        "rp": "0",
        "T": 1791422100123,
    }
    spm = MagicMock()
    spm.get_strategy_position_by_entry_order_id.return_value = {
        "strategy_id": "s1",
        "decision_id": "d1",
        "position_id": "eca45846",
        "side": "LONG",
    }
    with (
        patch("tradeengine.dispatcher.execution_event_publisher") as pub,
        patch("tradeengine.strategy_position_manager.strategy_position_manager", spm),
    ):
        pub.publish = AsyncMock(return_value=True)
        await d._on_user_data_fill(order_obj)
    persisted = d.position_manager.persist_entry_fill.await_args.args[0]
    assert persisted["exchange"] == "binance"
    json.dumps(persisted)  # serialisable as sent


# --- the exposure of fresh snapshots ---------------------------------------------------------------


def snapshot(side="LONG", quantity=100.0, entry=2.0, upnl=0.0, mark=0.0, notional=0.0):
    return PositionSnapshot(
        symbol="XRPUSDT",
        side=side,
        quantity=quantity,
        entry_price=entry,
        unrealized_pnl=upnl,
        mark_price=mark,
        notional=notional,
    )


def manager_with(*snapshots, ready=True):
    pm = PositionManager(exchange=MagicMock())
    pm.equity = 10000.0
    store = ExchangeTruthStore()
    store._is_ready = ready
    store._positions = {(s.symbol, s.side): s for s in snapshots}
    pm.exchange_truth_store = store
    return pm


def notional_of(pm):
    with patch("tradeengine.position_manager.TE_EXCHANGE_TRUTH_STORE_ENABLED", "on"):
        value = pm._position_notional()
    return value, pm._portfolio_exposure_refresh_failed


def test_a_fresh_snapshot_without_a_mark_or_notional_is_valued_at_its_entry_price():
    value, failed = notional_of(manager_with(snapshot()))
    assert failed is False and value == pytest.approx(200.0)


def test_the_mark_implied_by_the_unrealized_pnl_is_preferred_to_the_entry_price():
    long_value, _ = notional_of(manager_with(snapshot(side="LONG", upnl=5.0)))
    assert long_value == pytest.approx(205.0)  # mark 2.05
    short_value, _ = notional_of(manager_with(snapshot(side="SHORT", upnl=5.0)))
    assert short_value == pytest.approx(195.0)  # mark 1.95
    both, _ = notional_of(
        manager_with(snapshot(side="BOTH", quantity=-100.0, upnl=5.0))
    )
    assert both == pytest.approx(195.0)


def test_an_implied_mark_at_or_below_zero_falls_back_to_the_entry_price():
    value, failed = notional_of(manager_with(snapshot(upnl=-500.0)))
    assert failed is False and value == pytest.approx(200.0)


def test_a_known_mark_or_notional_is_used_as_before():
    assert notional_of(manager_with(snapshot(mark=2.5)))[0] == pytest.approx(250.0)
    assert notional_of(manager_with(snapshot(notional=-300.0, mark=2.5)))[
        0
    ] == pytest.approx(300.0)


def test_a_snapshot_with_no_price_at_all_still_fails_closed():
    value, failed = notional_of(manager_with(snapshot(entry=0.0)))
    assert failed is True and value == 0.0


def test_a_store_that_is_not_ready_still_fails_closed():
    value, failed = notional_of(manager_with(snapshot(), ready=False))
    assert failed is True and value == 0.0


def test_two_snapshots_one_of_them_fresh_are_summed_without_failing():
    pm = manager_with(
        snapshot(mark=2.5), PositionSnapshot("XLMUSDT", "LONG", 1000.0, 0.2, 0.0)
    )
    value, failed = notional_of(pm)
    assert failed is False and value == pytest.approx(250.0 + 200.0)


def test_the_exposure_after_an_entry_is_a_number_not_a_refresh_failure():
    pm = manager_with(snapshot())
    with patch("tradeengine.position_manager.TE_EXCHANGE_TRUTH_STORE_ENABLED", "on"):
        exposure = pm._calculate_portfolio_exposure()
    assert pm._portfolio_exposure_refresh_failed is False
    assert exposure == pytest.approx(200.0 / 10000.0)
