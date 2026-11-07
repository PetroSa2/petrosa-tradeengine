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


def _assert_flat_equality(filter_dict):
    """data-manager's /api/v1/data/query contract: a non-empty, flat equality match (else HTTP 400)."""
    assert filter_dict
    for key, value in filter_dict.items():
        assert not key.startswith("$") and not isinstance(value, dict), filter_dict


@pytest.mark.asyncio
async def test_open_rows_are_read_with_flat_equality_filters_and_merged():
    client = DataManagerPositionClient.__new__(DataManagerPositionClient)
    by_status = {
        "open": [{"strategy_position_id": "a", "status": "open"}],
        "partially_closed": [
            {"strategy_position_id": "b", "status": "partially_closed"},
            {"strategy_position_id": "a", "status": "open"},  # never twice
        ],
    }

    async def query(**kwargs):
        _assert_flat_equality(kwargs["filter"])
        return {"data": by_status[kwargs["filter"]["status"]]}

    client.data_manager_client = SimpleNamespace(_client=SimpleNamespace(query=query))
    rows = await client.list_open_strategy_positions()
    assert sorted(r["strategy_position_id"] for r in rows) == ["a", "b"]


@pytest.mark.asyncio
async def test_the_rebase_endpoint_reads_with_data_managers_filter_contract(
    monkeypatch,
):
    from fastapi.testclient import TestClient

    import shared.mysql_client as mysql_client
    import tradeengine.api as api

    seen = []

    async def query(**kwargs):
        _assert_flat_equality(kwargs["filter"])
        seen.append(kwargs["filter"])
        return {"data": []}

    store = DataManagerPositionClient.__new__(DataManagerPositionClient)
    store.data_manager_client = SimpleNamespace(_client=SimpleNamespace(query=query))
    monkeypatch.setattr(mysql_client, "position_client", store)
    truth = SimpleNamespace(is_ready=True, get_positions=lambda: {})
    monkeypatch.setattr(api._strategy_position_manager, "exchange_truth_store", truth)

    response = TestClient(api.app).post("/admin/strategy-positions/rebase")

    assert response.status_code == 200, response.text
    assert response.json()["dry_run"] is True
    assert seen == [{"status": "open"}, {"status": "partially_closed"}]


# --- #837 follow-up: an attributed closure carries the closing fills' P&L --------------------


def _short_row(**kw):
    row = _row("ada-short", "ADAUSDT", "SHORT", 21, 30, entry_order_id="E1", **kw)
    row["entry_price"] = 0.50
    row["entry_fee"] = 0.0
    return row


def _closing_fill(**kw):
    return {
        "symbol": "ADAUSDT",
        "side": "BUY",
        "positionSide": "SHORT",
        "qty": "21",
        "price": "0.48",
        "realizedPnl": "0.42",
        "commission": "0.004",
        "commissionAsset": "USDT",
        "orderId": 777,
        "id": 9001,
        "time": int((NOW - timedelta(hours=2)).timestamp() * 1000),
        **kw,
    }


def test_an_attributed_closure_writes_the_fills_pnl_like_an_organic_external_close():
    from tradeengine.external_close_fills import attribute_closing_fills

    row = _short_row()
    closure = attribute_closing_fills(row, [_closing_fill()], [row])
    assert closure["pnl_source"] == "exchange_fills"

    update = rebase.build_closure_update(
        row, "no_exchange_position", closure, "r5", NOW
    )

    assert update["status"] == "closed_externally"
    assert update["exit_price"] == pytest.approx(0.48)
    assert update["exit_order_id"] == "777"
    assert float(update["gross_realized_pnl"]) == pytest.approx(0.42)
    assert float(update["realized_pnl"]) == pytest.approx(
        0.416
    )  # gross minus the exit commission
    record = update["strategy_metadata"]["external_close"]
    assert (
        record["pnl_source"] == "exchange_fills"
        and record["reason"] == "no_exchange_position"
    )
    assert (
        update["strategy_metadata"]["reconcile_rebase"]["prev_fields"]["realized_pnl"]
        is None
    )


def test_an_unattributed_closure_still_writes_no_pnl():
    row = _short_row()
    closure = {"pnl_source": "unattributed", "reason": "symbol_side_shared"}
    update = rebase.build_closure_update(
        row, "no_exchange_position", closure, "r6", NOW
    )
    assert update["realized_pnl"] is None and update["gross_realized_pnl"] is None
    assert (
        update["strategy_metadata"]["external_close"]["closure_reason"]
        == "symbol_side_shared"
    )


def _closed_without_pnl(run_id="rebase-x", **kw):
    """The 2026-11-07 apply's defect: an exchange_fills closure whose P&L was left null."""
    row = _short_row(
        status="closed_externally", close_reason="no_exchange_position", **kw
    )
    row["exit_time"] = NOW.isoformat()
    row["realized_pnl"] = None
    row["strategy_metadata"] = {
        "source": "signal",
        "reconcile_rebase": {
            "run_id": run_id,
            "prev_status": "open",
            "reason": "no_exchange_position",
        },
        "external_close": {
            "pnl_source": "exchange_fills",
            "reason": "no_exchange_position",
        },
    }
    return row


@pytest.mark.asyncio
async def test_pnl_repair_plans_then_writes_and_audits_only_the_runs_null_attributed_rows():
    target = _closed_without_pnl()
    other_run = _closed_without_pnl(run_id="someone-else", strategy_position_id="other")
    other_run["strategy_position_id"] = "other"
    already = _closed_without_pnl()
    already["strategy_position_id"] = "already"
    already["realized_pnl"] = 1.0
    rows = [target, other_run, already]
    store = _Store(rows)
    fetcher = AsyncMock(return_value=[_closing_fill()])

    dry = await rebase.run_pnl_repair(
        rows=rows,
        update_row=store.update_row,
        fills_fetcher=fetcher,
        run_id="rebase-x",
        now=NOW,
    )
    assert dry["candidates"] == ["ada-short"] and store.writes == []
    assert dry["planned"][0]["exit_price"] == pytest.approx(0.48)

    applied = await rebase.run_pnl_repair(
        rows=rows,
        update_row=store.update_row,
        fills_fetcher=fetcher,
        run_id="rebase-x",
        dry_run=False,
        now=NOW,
    )
    assert applied["repaired"] == ["ada-short"] and applied["failed"] == []
    repaired = store.rows["ada-short"]
    assert float(repaired["realized_pnl"]) == pytest.approx(0.416)
    assert repaired["exit_order_id"] == "777"
    assert repaired["status"] == "closed_externally"
    audit = repaired["strategy_metadata"]["reconcile_rebase"]
    assert audit["run_id"] == "rebase-x"  # the original audit is kept
    assert audit["pnl_repair"]["replaced"]["realized_pnl"] is None
    assert repaired["strategy_metadata"]["source"] == "signal"
    assert [w[0] for w in store.writes] == ["ada-short"]  # nothing else written


@pytest.mark.asyncio
async def test_pnl_repair_never_writes_from_a_closure_that_is_no_longer_attributable():
    rows = [_closed_without_pnl()]
    store = _Store(rows)
    fetcher = AsyncMock(
        return_value=[_closing_fill(qty="5")]
    )  # quantity no longer matches
    report = await rebase.run_pnl_repair(
        rows=rows,
        update_row=store.update_row,
        fills_fetcher=fetcher,
        run_id="rebase-x",
        dry_run=False,
        now=NOW,
    )
    assert (
        report["repaired"] == []
        and report["not_attributable"][0]["closure"] == "quantity_mismatch"
    )
    assert store.writes == []


@pytest.mark.asyncio
async def test_the_repair_endpoint_reads_with_data_managers_filter_contract(
    monkeypatch,
):
    from fastapi.testclient import TestClient

    import shared.mysql_client as mysql_client
    import tradeengine.api as api

    seen = []

    async def query(**kwargs):
        _assert_flat_equality(kwargs["filter"])
        seen.append(kwargs["filter"])
        return {"data": []}

    store = DataManagerPositionClient.__new__(DataManagerPositionClient)
    store.data_manager_client = SimpleNamespace(_client=SimpleNamespace(query=query))
    monkeypatch.setattr(mysql_client, "position_client", store)

    response = TestClient(api.app).post(
        "/admin/strategy-positions/rebase/repair-pnl", params={"run_id": "rebase-x"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["dry_run"] is True
    assert seen == [{"strategy_metadata.reconcile_rebase.run_id": "rebase-x"}]
