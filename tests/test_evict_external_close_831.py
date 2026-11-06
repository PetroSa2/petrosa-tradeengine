"""evict_ghost_position records exchange P&L or its source (#831)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from tradeengine.external_close_fills import missing_closure
from tradeengine.metrics import strategy_position_external_close_total
from tradeengine.strategy_position_manager import StrategyPositionManager

EXIT_TIME = datetime(2026, 11, 5, 13, 0, tzinfo=UTC)


def _row(spid: str = "sp-1") -> dict:
    return {
        "strategy_position_id": spid,
        "strategy_id": "iceberg_detector",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "entry_quantity": 0.01,
        "exit_quantity": 0,
        "entry_price": 100_000.0,
        "entry_fee": "0.04",
        "entry_time": EXIT_TIME - timedelta(hours=1),
        "status": "open",
        "exchange_position_key": "BTCUSDT_LONG",
        "strategy_metadata": {"timeframe": "5m"},
    }


def _manager(*rows: dict) -> StrategyPositionManager:
    mgr = StrategyPositionManager()
    for row in rows:
        mgr.strategy_positions[row["strategy_position_id"]] = row
    mgr._update_strategy_position_closure = AsyncMock(return_value=None)  # type: ignore[method-assign]
    return mgr


def _count(pnl_source: str, reason: str) -> float:
    return strategy_position_external_close_total.labels(
        ledger="strategy_positions", pnl_source=pnl_source, reason=reason
    )._value.get()


def _journaled(mgr: StrategyPositionManager) -> dict:
    return mgr._update_strategy_position_closure.await_args.args[1]  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_exchange_fills_record_exit_price_pnl_and_commission() -> None:
    mgr = _manager(_row())
    closure = {
        "pnl_source": "exchange_fills",
        "exit_price": Decimal("101600"),
        "exit_quantity": Decimal("0.010"),
        "realized_pnl": Decimal("16.0"),
        "commission_by_asset": {"USDT": Decimal("0.1016")},
        "exit_time": EXIT_TIME,
        "exit_order_ids": ["222", "223"],
        "fills": [{"trade_id": 2}, {"trade_id": 3}],
    }
    before = _count("exchange_fills", "-")

    assert await mgr.evict_ghost_position("sp-1", closure=closure) is True

    row = _journaled(mgr)
    assert row["status"] == "closed_externally"
    assert row["close_reason"] == "no_exchange_position"
    assert row["exit_price"] == 101600.0
    assert row["exit_quantity"] == 0.01
    assert row["exit_time"] == EXIT_TIME
    assert row["exit_order_id"] == "223"
    # net = gross 16.0 - exit commission 0.1016 - entry fee 0.04 (full quantity)
    assert row["realized_pnl"] == Decimal("15.85840000")
    assert row["gross_realized_pnl"] == Decimal("16.00000000")
    assert row["realized_pnl_pct"] == Decimal("1.58584000")
    assert row["commission_total"] == Decimal("0.14160000")
    record = row["strategy_metadata"]["external_close"]
    assert record["pnl_source"] == "exchange_fills"
    assert record["pnl_basis"] == "exchange_realized_pnl_net_of_fees"
    assert record["exit_commission_by_asset"] == {"USDT": "0.10160000"}
    assert record["exit_order_ids"] == ["222", "223"]
    assert row["strategy_metadata"]["timeframe"] == "5m"
    assert _count("exchange_fills", "-") == before + 1
    assert "sp-1" not in mgr.strategy_positions


@pytest.mark.asyncio
async def test_fetch_failure_still_closes_marks_missing_and_counts() -> None:
    mgr = _manager(_row())
    before = _count("missing", "fetch_failed")

    assert await mgr.evict_ghost_position(
        "sp-1", closure=missing_closure("fetch_failed")
    )

    row = _journaled(mgr)
    assert row["status"] == "closed_externally"
    assert row["realized_pnl"] is None
    assert row.get("exit_price") is None
    assert row["strategy_metadata"]["external_close"] == {
        "pnl_source": "missing",
        "reason": "fetch_failed",
    }
    assert _count("missing", "fetch_failed") == before + 1
    assert "sp-1" not in mgr.strategy_positions


@pytest.mark.asyncio
async def test_unattributed_gets_no_invented_pnl() -> None:
    mgr = _manager(_row())
    closure = {
        "pnl_source": "unattributed",
        "reason": "symbol_side_shared",
        "sharing_positions": ["sp-2"],
        "candidate_fills": [{"trade_id": 2, "realized_pnl": "10.0"}],
    }
    before = _count("unattributed", "symbol_side_shared")

    assert await mgr.evict_ghost_position("sp-1", closure=closure)

    row = _journaled(mgr)
    assert row["realized_pnl"] is None
    assert row["gross_realized_pnl"] is None
    assert row["realized_pnl_pct"] is None
    assert row.get("exit_price") is None
    record = row["strategy_metadata"]["external_close"]
    assert record["pnl_source"] == "unattributed"
    assert record["sharing_positions"] == ["sp-2"]
    assert record["candidate_fills"] == [{"trade_id": 2, "realized_pnl": "10.0"}]
    assert _count("unattributed", "symbol_side_shared") == before + 1


@pytest.mark.asyncio
async def test_no_closure_is_counted_as_missing_not_fetched() -> None:
    mgr = _manager(_row())
    before = _count("missing", "not_fetched")

    assert await mgr.evict_ghost_position("sp-1")

    row = _journaled(mgr)
    assert row["realized_pnl"] is None
    assert row["strategy_metadata"]["external_close"]["pnl_source"] == "missing"
    assert _count("missing", "not_fetched") == before + 1


@pytest.mark.asyncio
async def test_non_usdt_commission_leaves_commission_total_unchanged() -> None:
    mgr = _manager(_row())
    closure = {
        "pnl_source": "exchange_fills",
        "exit_price": Decimal("101000"),
        "exit_quantity": Decimal("0.010"),
        "realized_pnl": Decimal("10.0"),
        "commission_by_asset": {"BNB": Decimal("0.0001")},
        "exit_time": EXIT_TIME,
        "exit_order_ids": ["222"],
    }

    assert await mgr.evict_ghost_position("sp-1", closure=closure)

    row = _journaled(mgr)
    assert "commission_total" not in row
    assert row["gross_realized_pnl"] == Decimal("10.00000000")
    assert row["realized_pnl"] is None  # can't net a BNB fee without a price
    record = row["strategy_metadata"]["external_close"]
    assert record["exit_commission_by_asset"] == {"BNB": "0.00010000"}
    assert record["net_unavailable"] == "commission_not_usdt"


@pytest.mark.asyncio
async def test_unknown_position_returns_false() -> None:
    mgr = _manager()
    assert await mgr.evict_ghost_position("nope", closure=missing_closure("x")) is False
