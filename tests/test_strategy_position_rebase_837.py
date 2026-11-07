"""One-shot re-base of persisted open strategy rows onto the exchange's positions (petrosa-tradeengine#837)."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from shared.constants import UTC
from shared.mysql_client import DataManagerPositionClient
from tradeengine import strategy_position_rebase as rebase
from tradeengine.metrics import strategy_position_external_close_total

NOW = datetime(2026, 11, 7, 16, 0, tzinfo=UTC)


def _row(spid: str, symbol: str, side: str, qty: float, hours_ago: float, **kw: Any):
    return {
        "strategy_position_id": spid,
        "strategy_id": kw.pop("strategy_id", "iceberg_detector"),
        "symbol": symbol,
        "side": side,
        "entry_quantity": qty,
        "exit_quantity": kw.pop("exit_quantity", 0),
        "entry_price": 100.0,
        "entry_time": (NOW - timedelta(hours=hours_ago)).isoformat(),
        "status": kw.pop("status", "open"),
        "strategy_metadata": kw.pop("strategy_metadata", {"source": "signal"}),
        **kw,
    }


def _plans(rows, held):
    return {(p.symbol, p.side): p for p in rebase.plan_rebase(rows, held, NOW)}


def test_rows_on_a_key_the_exchange_does_not_hold_close_except_inside_the_grace_period():
    rows = [
        _row("old", "SOLUSDT", "LONG", 1, 48),
        _row("young", "SOLUSDT", "LONG", 1, 0.01),  # 36 s old
    ]
    plan = _plans(rows, {})[("SOLUSDT", "LONG")]
    assert [r["strategy_position_id"] for r in plan.close] == ["old"]
    assert [k["row"]["strategy_position_id"] for k in plan.keep] == ["young"]
    assert plan.reason == rebase.REASON_NO_EXCHANGE_POSITION


def test_newest_rows_cover_the_exchange_quantity_and_the_older_excess_closes():
    rows = [
        _row("a-oldest", "ETHUSDT", "LONG", 1.0, 100),
        _row("b", "ETHUSDT", "LONG", 1.0, 50),
        _row("c", "ETHUSDT", "LONG", 0.03, 10, exit_quantity=0.0),
        _row("d-newest", "ETHUSDT", "LONG", 0.02, 2),
    ]
    plan = _plans(rows, {("ETHUSDT", "LONG"): Decimal("0.046")})[("ETHUSDT", "LONG")]
    kept = {k["row"]["strategy_position_id"]: k for k in plan.keep}
    assert set(kept) == {"d-newest", "c"}
    assert kept["d-newest"]["partial"] is False
    assert kept["c"]["partial"] is True and kept["c"]["covered"] == Decimal("0.026")
    assert [r["strategy_position_id"] for r in plan.close] == ["b", "a-oldest"]
    assert plan.reason == rebase.REASON_EXCEEDS_EXCHANGE_QUANTITY
    summary = plan.summary()
    assert summary["rows_kept"] == 2 and summary["rows_to_close"] == 2
    assert summary["partial_row"] == "c"


def test_one_way_keys_are_skipped_not_guessed_and_closed_rows_are_ignored():
    rows = [
        _row("x", "BTCUSDT", "LONG", 1, 30),
        _row("done", "BTCUSDT", "SHORT", 1, 30, status="closed"),
    ]
    plans = _plans(rows, {("BTCUSDT", "BOTH"): Decimal("1")})
    assert plans[("BTCUSDT", "LONG")].skipped == "one_way_mode_not_guessed"
    assert not plans[("BTCUSDT", "LONG")].close
    assert ("BTCUSDT", "SHORT") not in plans


class _Store:
    """data-manager's strategy_positions as the endpoint sees it: update by id."""

    def __init__(self, rows):
        self.rows = {r["strategy_position_id"]: dict(r) for r in rows}
        self.writes: list[tuple[str, dict]] = []

    async def update_row(self, spid: str, update: dict) -> bool:
        self.writes.append((spid, update))
        self.rows[spid].update(update)
        return True


def _fixture():
    rows = [
        _row("gone-1", "SOLUSDT", "LONG", 1, 48),
        _row("held-old", "ETHUSDT", "LONG", 1.0, 100),
        _row("held-new", "ETHUSDT", "LONG", 0.05, 2),
    ]
    held = {("ETHUSDT", "LONG"): Decimal("0.05")}
    return rows, held


@pytest.mark.asyncio
async def test_dry_run_writes_nothing():
    rows, held = _fixture()
    store = _Store(rows)
    fetcher = AsyncMock(return_value=[])
    report = await rebase.run_rebase(
        rows=rows,
        exchange_quantities=held,
        update_row=store.update_row,
        manager=SimpleNamespace(strategy_positions={}),
        fills_fetcher=fetcher,
        run_id="r1",
        now=NOW,
    )
    assert report["dry_run"] is True
    assert report["rows_to_close"] == 2 and report["rows_kept"] == 1
    assert store.writes == []
    fetcher.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_audits_every_closure_marks_kept_rows_and_counts_by_reason():
    rows, held = _fixture()
    store = _Store(rows)
    manager = SimpleNamespace(strategy_positions={"held-old": dict(rows[1])})
    fetcher = AsyncMock(
        return_value=[]
    )  # no closing fills: unattributable, never invented
    before = {
        reason: strategy_position_external_close_total.labels(
            ledger="strategy_positions", pnl_source=src, reason=reason
        )._value.get()
        for reason, src in (("exceeds_exchange_quantity", "unattributed"),)
    }

    report = await rebase.run_rebase(
        rows=rows,
        exchange_quantities=held,
        update_row=store.update_row,
        manager=manager,
        fills_fetcher=fetcher,
        run_id="r2",
        dry_run=False,
        now=NOW,
    )

    assert report["closed"] == {
        "no_exchange_position": 1,
        "exceeds_exchange_quantity": 1,
    }
    old = store.rows["held-old"]
    assert old["status"] == "closed_externally"
    assert old["close_reason"] == "exceeds_exchange_quantity"
    assert old["realized_pnl"] is None
    audit = old["strategy_metadata"]["reconcile_rebase"]
    assert audit["run_id"] == "r2" and audit["prev_status"] == "open"
    assert audit["prev_remaining_qty"] == "1.0"
    assert old["strategy_metadata"]["external_close"]["pnl_source"] == "unattributed"
    assert old["strategy_metadata"]["source"] == "signal"  # existing metadata kept
    assert store.rows["gone-1"]["close_reason"] == "no_exchange_position"
    kept = store.rows["held-new"]["strategy_metadata"]["inferred_coverage"]
    assert kept["method"] == "newest-first" and kept["run_id"] == "r2"
    assert "held-old" not in manager.strategy_positions
    assert "held-new" in manager.strategy_positions
    fetcher.assert_awaited_once()  # the closing-fills lookup only for the no-exchange-position row
    after = strategy_position_external_close_total.labels(
        ledger="strategy_positions",
        pnl_source="unattributed",
        reason="exceeds_exchange_quantity",
    )._value.get()
    assert after == before["exceeds_exchange_quantity"] + 1


@pytest.mark.asyncio
async def test_apply_refuses_more_closures_than_the_bound():
    rows, held = _fixture()
    store = _Store(rows)
    report = await rebase.run_rebase(
        rows=rows,
        exchange_quantities=held,
        update_row=store.update_row,
        manager=SimpleNamespace(strategy_positions={}),
        fills_fetcher=None,
        run_id="r3",
        dry_run=False,
        max_closures=1,
        now=NOW,
    )
    assert "refused" in report
    assert store.writes == []


@pytest.mark.asyncio
async def test_rollback_restores_a_run_from_the_rows_alone():
    rows, held = _fixture()
    rows.append(
        _row(
            "other-run",
            "XRPUSDT",
            "LONG",
            1,
            30,
            status="closed_externally",
            strategy_metadata={"reconcile_rebase": {"run_id": "someone-else"}},
        )
    )
    store = _Store(rows)
    await rebase.run_rebase(
        rows=rows[:3],
        exchange_quantities=held,
        update_row=store.update_row,
        manager=SimpleNamespace(strategy_positions={}),
        fills_fetcher=AsyncMock(return_value=[]),
        run_id="r4",
        dry_run=False,
        now=NOW,
    )
    persisted = [dict(r) for r in store.rows.values()]  # only what data-manager holds

    dry = await rebase.run_rollback(
        rows=persisted, update_row=store.update_row, run_id="r4"
    )
    assert dry == {"run_id": "r4", "dry_run": True, "rows": 2}

    report = await rebase.run_rollback(
        rows=persisted, update_row=store.update_row, run_id="r4", dry_run=False
    )
    assert report["failed"] == []
    for spid in ("gone-1", "held-old"):
        row = store.rows[spid]
        assert row["status"] == "open"
        assert row["exit_time"] is None and row["close_reason"] is None
        assert "reconcile_rebase" not in row["strategy_metadata"]
        assert "external_close" not in row["strategy_metadata"]
        assert row["strategy_metadata"]["source"] == "signal"
    assert store.rows["other-run"]["status"] == "closed_externally"


@pytest.mark.asyncio
async def test_listing_pages_and_never_returns_a_truncated_set():
    client = DataManagerPositionClient.__new__(DataManagerPositionClient)
    pages = [{"data": [{"i": 1}, {"i": 2}]}, {"data": [{"i": 3}]}]
    query = AsyncMock(side_effect=pages)
    client.data_manager_client = SimpleNamespace(_client=SimpleNamespace(query=query))
    rows = await client.list_strategy_positions({"status": "open"}, page_size=2)
    assert [r["i"] for r in rows] == [1, 2, 3]
    assert query.await_args_list[1].kwargs["offset"] == 2

    query = AsyncMock(return_value={"data": [{"i": 1}, {"i": 2}]})
    client.data_manager_client = SimpleNamespace(_client=SimpleNamespace(query=query))
    with pytest.raises(RuntimeError, match="max_rows"):
        await client.list_strategy_positions(
            {"status": "open"}, page_size=2, max_rows=3
        )
