"""Open position rows are brought down to the exchange quantity, oldest first (petrosa-tradeengine#739)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from tradeengine.open_row_reconciler import (
    CLOSE_REASON,
    OpenRowReconciler,
    normalise_side,
    plan_rows,
)
from tradeengine.position_reconciler import PositionReconciler

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def _row(pid, qty, minutes_ago, symbol="ETHUSDT", side="LONG", status="open"):
    return {
        "position_id": pid,
        "symbol": symbol,
        "position_side": side,
        "quantity": qty,
        "status": status,
        "entry_time": NOW - timedelta(minutes=minutes_ago),
    }


# --- the allocation ------------------------------------------------------------------------------


def test_the_excess_is_taken_oldest_first_closing_rows_and_reducing_the_next():
    rows = [_row("new", 1.0, 10), _row("old", 1.0, 100), _row("mid", 1.0, 50)]
    plan = plan_rows("ETHUSDT", "LONG", rows, exchange_quantity=1.5)
    assert plan.ledger_quantity == 3.0 and plan.excess == pytest.approx(1.5)
    assert [(a.position_id, a.action, a.quantity) for a in plan.actions] == [
        ("old", "close", 1.0),
        ("mid", "reduce", 0.5),
    ]


def test_an_exchange_position_that_went_flat_closes_every_row():
    rows = [_row(f"r{i}", 0.01, 100 - i) for i in range(17)]  # ETH LONG: 17 open rows
    plan = plan_rows("ETHUSDT", "LONG", rows, exchange_quantity=0.0)
    assert len(plan.actions) == 17 and all(a.action == "close" for a in plan.actions)
    assert sum(a.quantity for a in plan.actions) == pytest.approx(0.17)


def test_matching_quantities_plan_nothing_and_a_larger_exchange_quantity_is_only_noted():
    rows = [_row("a", 0.5, 10), _row("b", 0.5, 20)]
    assert plan_rows("ETHUSDT", "LONG", rows, 1.0).actions == []
    short = plan_rows("ETHUSDT", "LONG", rows, 2.0)
    assert short.actions == [] and short.note == "exchange_exceeds_ledger"


def test_after_the_plan_the_open_quantity_equals_the_exchange_quantity():
    rows = [_row(f"r{i}", q, 100 - i) for i, q in enumerate([0.3, 0.2, 0.5, 0.1])]
    plan = plan_rows("ETHUSDT", "LONG", rows, 0.45)
    remaining = sum(r["quantity"] for r in rows) - sum(a.quantity for a in plan.actions)
    assert remaining == pytest.approx(0.45)


def test_sides_are_normalised():
    assert normalise_side("BUY") == "LONG" and normalise_side("sell") == "SHORT"
    assert normalise_side("short") == "SHORT"


# --- the reconciler ------------------------------------------------------------------------------


def _manager(rows):
    pm = MagicMock()
    pm.position_records = {r["position_id"]: r for r in rows}
    pm.record_position_close = AsyncMock(return_value={})
    return pm


def _binance(symbol="ETHUSDT", side="LONG", amount=1.0):
    return {(symbol, side): {"symbol": symbol, "positionAmt": str(amount)}}


def _reconciler(pm, mode, **kwargs):
    return OpenRowReconciler(pm, mode=mode, clock=lambda: NOW.timestamp(), **kwargs)


@pytest.mark.asyncio
async def test_dry_run_plans_and_measures_but_writes_nothing():
    pm = _manager([_row("a", 1.0, 100), _row("b", 1.0, 90), _row("c", 1.0, 80)])
    reconciler = _reconciler(pm, "dry_run")
    plans = await reconciler.reconcile(_binance(amount=1.0))
    assert len(plans) == 1 and plans[0]["excess"] == pytest.approx(2.0)
    assert plans[0]["applied"] is False
    pm.record_position_close.assert_not_awaited()


@pytest.mark.asyncio
async def test_close_mode_needs_the_plan_on_consecutive_passes():
    pm = _manager([_row("a", 1.0, 100), _row("b", 1.0, 90), _row("c", 1.0, 80)])
    reconciler = _reconciler(pm, "close", confirm_passes=2)
    first = await reconciler.reconcile(_binance(amount=1.0))
    assert first[0]["passes"] == 1 and first[0]["applied"] is False
    pm.record_position_close.assert_not_awaited()
    second = await reconciler.reconcile(_binance(amount=1.0))
    assert second[0]["applied"] is True
    calls = pm.record_position_close.await_args_list
    assert [c.kwargs["position_id"] for c in calls] == [
        "a",
        "b",
    ]  # the two oldest, in full
    kw = calls[0].kwargs
    assert kw["exit_qty"] == 1.0 and kw["close_reason"] == CLOSE_REASON
    assert kw["pnl_unknown"] is True and kw["exit_price"] is None  # no P&L is made up
    assert kw["exit_order_id"].startswith("reconcile-ETHUSDT-LONG-a-")


@pytest.mark.asyncio
async def test_a_changing_plan_restarts_the_confirmation():
    pm = _manager([_row("a", 1.0, 100), _row("b", 1.0, 90)])
    reconciler = _reconciler(pm, "close", confirm_passes=2)
    await reconciler.reconcile(_binance(amount=1.0))
    again = await reconciler.reconcile(_binance(amount=0.5))  # the exchange moved
    assert again[0]["passes"] == 1 and again[0]["applied"] is False
    pm.record_position_close.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_flat_exchange_position_is_treated_as_zero_quantity():
    pm = _manager([_row("a", 0.4, 100), _row("b", 0.4, 90)])
    reconciler = _reconciler(pm, "close", confirm_passes=1)
    plans = await reconciler.reconcile({})  # nothing on the exchange
    assert plans[0]["exchange_quantity"] == 0.0 and plans[0]["applied"] is True
    assert pm.record_position_close.await_count == 2


@pytest.mark.asyncio
async def test_young_rows_and_other_sides_are_left_alone():
    rows = [
        _row("old", 1.0, 100),
        _row("fresh", 1.0, 1),  # inside the 5-minute grace
        _row("short", 1.0, 100, side="SHORT"),
    ]
    pm = _manager(rows)
    reconciler = _reconciler(pm, "close", confirm_passes=1)
    plans = await reconciler.reconcile(
        {
            ("ETHUSDT", "LONG"): {"positionAmt": "2.0"},
            ("ETHUSDT", "SHORT"): {"positionAmt": "-1.0"},
        }
    )
    assert (
        plans == []
    )  # the old row alone (1.0) is below the exchange 2.0; the short matches
    pm.record_position_close.assert_not_awaited()


@pytest.mark.asyncio
async def test_closed_rows_and_buy_sell_rows_are_read_correctly():
    rows = [
        _row("closed", 1.0, 100, status="closed"),
        _row("buy-row", 1.0, 100, side="BUY"),
        _row("zero", 0.0, 100),
    ]
    pm = _manager(rows)
    plans = await _reconciler(pm, "dry_run").reconcile({})
    assert [(p["side"], p["ledger_quantity"]) for p in plans] == [("LONG", 1.0)]


@pytest.mark.asyncio
async def test_off_mode_and_unknown_modes():
    pm = _manager([_row("a", 1.0, 100)])
    assert await _reconciler(pm, "off").reconcile({}) == []
    assert _reconciler(pm, "bogus").mode == "dry_run"


@pytest.mark.asyncio
async def test_a_failed_close_is_logged_and_marks_the_plan_not_applied():
    pm = _manager([_row("a", 1.0, 100)])
    pm.record_position_close = AsyncMock(side_effect=RuntimeError("store down"))
    plans = await _reconciler(pm, "close", confirm_passes=1).reconcile({})
    assert plans[0]["applied"] is False


# --- inside the position reconciler --------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_position_reconciler_runs_the_row_pass_and_survives_its_failure():
    exchange = MagicMock()
    exchange.get_position_info = AsyncMock(
        return_value=[
            {
                "symbol": "ETHUSDT",
                "positionAmt": "1.0",
                "positionSide": "LONG",
                "entryPrice": "100",
                "markPrice": "100",
            }
        ]
    )
    pm = MagicMock()
    pm.get_positions.return_value = {}
    pm.positions = {}
    rows = MagicMock()
    rows.reconcile = AsyncMock(side_effect=RuntimeError("boom"))
    reconciler = PositionReconciler(
        exchange=exchange, position_manager=pm, row_reconciler=rows
    )
    reconciler._detect_unhedged_for = AsyncMock(return_value=([], {}))
    reconciler._check_hedge_mode = AsyncMock()
    await (
        reconciler.reconcile_once()
    )  # the failing row pass must not break the read-only pass
    rows.reconcile.assert_awaited_once()
    assert ("ETHUSDT", "LONG") in rows.reconcile.await_args.args[0]
