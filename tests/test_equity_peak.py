"""Drawdown from the equity peak, including unrealized P&L, on /state (te#730)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tradeengine.equity_peak import (
    COLLECTION,
    SAVE_MIN_SECONDS,
    EquityPeakTracker,
    peak_from_risk_inputs,
)
from tradeengine.exchange.binance import BinanceFuturesExchange


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def _account(wallet, unrealized=None):
    info = {"total_wallet_balance": str(wallet)}
    if unrealized is not None:
        info["total_unrealized_profit"] = str(unrealized)
    return info


def _tracker(accounts, stored=None, risk_inputs=None, unrealized_pnl=0.0, clock=None):
    client = MagicMock()
    client.query = AsyncMock(return_value={"data": [stored] if stored else []})
    client.upsert_one = AsyncMock(return_value={"updated_count": 1})
    if risk_inputs is None:
        client.request = AsyncMock(side_effect=RuntimeError("404"))
    else:
        client.request = AsyncMock(return_value=risk_inputs)
    exchange = MagicMock()
    exchange.get_account_info = AsyncMock(side_effect=list(accounts))
    position_manager = MagicMock()
    position_manager.get_total_unrealized_pnl.return_value = unrealized_pnl
    position_manager.get_notional_summary.return_value = (6000.0, 3000.0)
    return EquityPeakTracker(client, exchange, position_manager, clock=clock or Clock())


@pytest.mark.asyncio
async def test_unrealized_pnl_is_included_so_a_losing_position_lowers_equity_below_the_peak():
    tracker = _tracker([_account(10000, 0), _account(10000, -250)])

    await tracker.sample()  # flat: equity 10,000 is the peak
    await tracker.sample()  # an open losing position

    state = tracker.state()
    assert state["equity_peak"] == 10000
    assert state["equity_now"] == 9750
    assert state["equity_now"] < state["equity_peak"]
    assert state["from_peak_pct"] == pytest.approx(0.025)


@pytest.mark.asyncio
async def test_the_position_manager_supplies_unrealized_pnl_when_the_account_has_none():
    tracker = _tracker([_account(10000)], unrealized_pnl=-400.0)

    await tracker.sample()

    assert tracker.state()["equity_now"] == 9600


@pytest.mark.asyncio
async def test_the_peak_only_rises_and_records_when():
    tracker = _tracker([_account(10000, 0), _account(10400, 0), _account(10100, 0)])

    await tracker.sample()
    await tracker.sample()
    peak_at = tracker.state()["peak_at"]
    await tracker.sample()

    state = tracker.state()
    assert state["equity_peak"] == 10400
    assert state["peak_at"] == peak_at
    assert state["from_peak_pct"] == pytest.approx((10400 - 10100) / 10400)


@pytest.mark.asyncio
async def test_a_new_high_is_persisted_at_most_once_a_minute():
    clock = Clock()
    tracker = _tracker(
        [_account(10000, 0), _account(10100, 0), _account(10200, 0)], clock=clock
    )

    await tracker.sample()
    assert tracker.client.upsert_one.await_count == 1
    clock.advance(10)
    await tracker.sample()  # a higher high 10 s later: held back
    assert tracker.client.upsert_one.await_count == 1
    clock.advance(SAVE_MIN_SECONDS)
    await tracker.sample()  # the held-back high is saved with the next sample

    saved = tracker.client.upsert_one.await_args.kwargs
    assert saved["collection"] == COLLECTION
    assert saved["record"]["equity_peak"] == 10200


@pytest.mark.asyncio
async def test_the_peak_survives_a_restart():
    clock = Clock()
    first = _tracker([_account(10000, 0), _account(10500, 0)], clock=clock)
    await first.sample()
    clock.advance(SAVE_MIN_SECONDS)
    await first.sample()
    saved = first.client.upsert_one.await_args.kwargs["record"]

    # A new pod: nothing sampled yet, the stored peak is read back, then a lower equity is sampled.
    second = _tracker([_account(10200, 0)], stored=saved)
    await second.seed()
    assert second.state()["equity_peak"] == 10500
    await second.sample()

    state = second.state()
    assert state["equity_peak"] == 10500
    assert state["from_peak_pct"] == pytest.approx((10500 - 10200) / 10500)


@pytest.mark.asyncio
async def test_the_seed_is_the_higher_of_data_manager_and_the_stored_value():
    stored = {"equity_peak": 10100, "peak_at": "2026-10-01T00:00:00+00:00"}
    risk_inputs = {"equity": {"peak": 10900, "peak_at": "2026-09-30T00:00:00+00:00"}}
    tracker = _tracker([_account(10000, 0)], stored=stored, risk_inputs=risk_inputs)

    await tracker.seed()

    state = tracker.state()
    assert state["equity_peak"] == 10900
    assert state["peak_at"] == "2026-09-30T00:00:00+00:00"


@pytest.mark.asyncio
async def test_an_unavailable_data_manager_does_not_stop_the_tracker():
    tracker = _tracker([_account(10000, 0)])
    tracker.client.query = AsyncMock(side_effect=RuntimeError("down"))

    await tracker.seed()
    assert await tracker.sample() is True

    assert tracker.state()["equity_peak"] == 10000


@pytest.mark.asyncio
async def test_a_failed_sample_keeps_the_last_value_and_a_failed_save_is_retried(
    caplog,
):
    clock = Clock()
    tracker = _tracker([_account(10000, 0), RuntimeError("exchange down")], clock=clock)
    tracker.client.upsert_one = AsyncMock(side_effect=RuntimeError("dm down"))
    await tracker.sample()  # the save fails: the high stays marked unsaved

    assert await tracker.sample() is False

    assert tracker.state()["equity_now"] == 10000
    assert tracker._unsaved is True


def test_the_state_without_a_sample_reports_nothing_invented():
    state = EquityPeakTracker(MagicMock(), MagicMock(), MagicMock()).state()

    assert state["equity_now"] is None
    assert state["equity_peak"] is None
    assert state["from_peak_pct"] is None
    assert state["net_notional_ratio"] is None


@pytest.mark.asyncio
async def test_net_notional_is_reported_over_equity():
    tracker = _tracker([_account(10000, 0)])
    await tracker.sample()

    assert tracker.state()["net_notional_ratio"] == pytest.approx(0.3)


def test_the_risk_inputs_shapes_are_read():
    assert peak_from_risk_inputs({"equity": {"peak": 5, "peak_at": "t"}}) == (5.0, "t")
    assert peak_from_risk_inputs({"equity_peak": "7.5"}) == (7.5, None)
    assert peak_from_risk_inputs({"equity": {}}) is None
    assert peak_from_risk_inputs({"equity": {"peak": -1}}) is None
    assert peak_from_risk_inputs("nope") is None


@pytest.mark.asyncio
async def test_the_loop_seeds_once_and_then_samples():
    tracker = _tracker([_account(10000, 0), _account(10100, 0), _account(10050, 0)])

    task = asyncio.create_task(tracker.run(interval_seconds=0.02))
    await asyncio.sleep(0.07)
    task.cancel()
    with pytest.raises(asyncio.CancelledError) as cancelled:
        await task

    assert cancelled.type is asyncio.CancelledError
    tracker.client.request.assert_awaited_once()
    assert tracker.state()["equity_peak"] == 10100


@pytest.mark.asyncio
async def test_state_endpoint_reports_the_drawdown_block():
    import tradeengine.api as api

    tracker = _tracker([_account(10000, 0), _account(10000, -250)])
    await tracker.sample()
    await tracker.sample()
    dispatcher = MagicMock()
    dispatcher.get_cio_state.return_value = {"risk_limits": {}}
    dispatcher.resolve_risk_cap = AsyncMock(
        return_value={"probe_mode": False, "max_position_size_usd": 1000.0}
    )
    with (
        patch.object(api, "dispatcher", dispatcher),
        patch.object(api, "equity_peak_tracker", tracker),
    ):
        state = await api.get_state(symbol="BTCUSDT", side=None)

    drawdown = state["drawdown"]
    assert drawdown["equity_now"] == 9750
    assert drawdown["equity_peak"] == 10000
    assert drawdown["from_peak_pct"] == pytest.approx(0.025)
    assert set(drawdown) >= {
        "equity_now",
        "equity_peak",
        "peak_at",
        "from_peak_pct",
        "net_notional_ratio",
    }


@pytest.mark.asyncio
async def test_the_exchange_account_info_carries_unrealized_profit():
    exchange = BinanceFuturesExchange()
    exchange.initialized = True
    exchange.client = MagicMock()
    exchange.client.futures_account.return_value = {
        "totalWalletBalance": "10000",
        "availableBalance": "4700",
        "totalUnrealizedProfit": "-250",
    }

    info = await exchange.get_account_info()

    assert info["total_unrealized_profit"] == "-250"
    assert info["total_wallet_balance"] == "10000"
