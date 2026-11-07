"""After a restart the reconciler loads the persisted open rows that cover the exchange (petrosa-tradeengine#837).

Rows written before a restart used to stay out of memory forever, so nothing reconciled them. Now the first pass
with a ready store reads them from data-manager and loads only the rows that cover the exchange's quantity on
their (symbol, side), newest first. The rest are counted and logged, never loaded and never closed here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from tests.test_strategy_position_reconciler import _build_store, _manager_with, _spos
from tradeengine.exchange_truth_store import PositionSnapshot
from tradeengine.metrics import strategy_position_persisted_uncovered_gauge
from tradeengine.strategy_position_reconciler import StrategyPositionReconciler


def _persisted(spid: str, hours_ago: float, qty: float = 0.01, **kw) -> dict:
    row = _spos(spid, entry_time=datetime.now(UTC) - timedelta(hours=hours_ago), **kw)
    row["entry_time"] = row["entry_time"].isoformat()  # as data-manager returns it
    row["entry_quantity"] = qty
    return row


def _snap(qty: float) -> PositionSnapshot:
    return PositionSnapshot(
        symbol="BTCUSDT",
        side="LONG",
        quantity=qty,
        entry_price=50_000,
        unrealized_pnl=0,
    )


def _gauge(reason: str) -> float:
    return strategy_position_persisted_uncovered_gauge.labels(
        reason=reason
    )._value.get()


@pytest.mark.asyncio
async def test_first_pass_loads_only_the_rows_covering_the_exchange_quantity():
    store = await _build_store({("BTCUSDT", "LONG"): _snap(0.02)})
    manager = _manager_with()
    rows = [
        _persisted("oldest", 72),
        _persisted("older", 48),
        _persisted("new", 5),
        _persisted("newest", 2),
    ]
    loader = AsyncMock(return_value=rows)
    reconciler = StrategyPositionReconciler(
        manager,
        store,
        fills_fetcher=AsyncMock(return_value=[]),
        persisted_loader=loader,
    )

    summary = await reconciler.reconcile_once()

    assert set(manager.strategy_positions) == {"new", "newest"}
    assert summary["ghosts"] == 0  # the loaded rows are covered: nothing to evict
    assert _gauge("exceeds_exchange_quantity") == 2
    await reconciler.reconcile_once()
    loader.assert_awaited_once()  # read once, not every pass


@pytest.mark.asyncio
async def test_rows_on_a_key_the_exchange_does_not_hold_are_not_loaded():
    store = await _build_store({})
    manager = _manager_with()
    reconciler = StrategyPositionReconciler(
        manager,
        store,
        persisted_loader=AsyncMock(return_value=[_persisted("stale", 30)]),
    )

    await reconciler.reconcile_once()

    assert manager.strategy_positions == {}
    assert _gauge("no_exchange_position") == 1


@pytest.mark.asyncio
async def test_rows_already_in_memory_are_not_replaced():
    store = await _build_store({("BTCUSDT", "LONG"): _snap(0.01)})
    in_memory = _spos("live")
    manager = _manager_with(in_memory)
    persisted = dict(in_memory, entry_time=in_memory["entry_time"].isoformat())
    reconciler = StrategyPositionReconciler(
        manager, store, persisted_loader=AsyncMock(return_value=[persisted])
    )

    await reconciler.reconcile_once()

    assert manager.strategy_positions["live"] is in_memory


@pytest.mark.asyncio
async def test_a_failed_read_is_retried_and_never_breaks_the_pass():
    store = await _build_store({("BTCUSDT", "LONG"): _snap(0.01)})
    manager = _manager_with()
    loader = AsyncMock(
        side_effect=[RuntimeError("data-manager down"), [_persisted("a", 3)]]
    )
    reconciler = StrategyPositionReconciler(manager, store, persisted_loader=loader)

    first = await reconciler.reconcile_once()
    assert first["open"] == 0 and manager.strategy_positions == {}
    await reconciler.reconcile_once()
    assert set(manager.strategy_positions) == {"a"}


@pytest.mark.asyncio
async def test_without_a_loader_nothing_changes():
    store = await _build_store({})
    manager = _manager_with()
    reconciler = StrategyPositionReconciler(manager, store)
    assert (await reconciler.reconcile_once())["open"] == 0


@pytest.mark.asyncio
async def test_the_wired_loader_uses_data_managers_flat_filter_contract(monkeypatch):
    from types import SimpleNamespace

    import shared.mysql_client as mysql_client
    from shared.mysql_client import DataManagerPositionClient
    from tradeengine.dispatcher import _load_persisted_open_strategy_rows

    seen = []

    async def query(**kwargs):
        f = kwargs["filter"]
        assert f and all(
            not k.startswith("$") and not isinstance(v, dict) for k, v in f.items()
        )
        seen.append(f)
        return {"data": [{"strategy_position_id": f["status"]}]}

    store = DataManagerPositionClient.__new__(DataManagerPositionClient)
    store.data_manager_client = SimpleNamespace(_client=SimpleNamespace(query=query))
    monkeypatch.setattr(mysql_client, "position_client", store)

    rows = await _load_persisted_open_strategy_rows()

    assert seen == [{"status": "open"}, {"status": "partially_closed"}]
    assert len(rows) == 2
