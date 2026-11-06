"""Open-row reconciler books closing fills when it can attribute them (#831)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tradeengine.metrics import strategy_position_external_close_total
from tradeengine.open_row_reconciler import CLOSE_REASON, OpenRowReconciler
from tradeengine.position_manager import PositionManager

NOW = datetime(2026, 11, 6, 12, 30, tzinfo=UTC)
ENTRY = NOW - timedelta(hours=2)


def _row(pid: str, qty: float, entry: datetime = ENTRY, **extra) -> dict:
    return {
        "position_id": pid,
        "symbol": "BCHUSDT",
        "position_side": "LONG",
        "quantity": qty,
        "entry_quantity": qty,
        "entry_price": 270.0,
        "status": "open",
        "entry_time": entry,
        "entry_order_id": f"entry-{pid}",
        **extra,
    }


def _fill(trade_id: int, qty: str, price: str, order_id: int = 777) -> dict:
    return {
        "symbol": "BCHUSDT",
        "id": trade_id,
        "orderId": order_id,
        "side": "SELL",
        "positionSide": "BOTH",
        "price": price,
        "qty": qty,
        "realizedPnl": "0.5",
        "commission": "0.0140",
        "commissionAsset": "USDT",
        "time": int((ENTRY + timedelta(minutes=30)).timestamp() * 1000),
    }


def _manager(rows: list[dict]) -> MagicMock:
    pm = MagicMock()
    pm.position_records = {r["position_id"]: r for r in rows}
    pm.record_position_close = AsyncMock(return_value={})
    pm.publish_unattributed_exchange_close = AsyncMock()
    return pm


def _flat(mark: str = "268.0") -> dict:
    return {("BCHUSDT", "LONG"): {"positionAmt": "0.0", "markPrice": mark}}


def _reconciler(pm, fetcher) -> OpenRowReconciler:
    return OpenRowReconciler(
        pm,
        mode="close",
        confirm_passes=1,
        clock=lambda: NOW.timestamp(),
        fills_fetcher=fetcher,
    )


def _count(pnl_source: str, reason: str) -> float:
    return strategy_position_external_close_total.labels(
        ledger="positions", pnl_source=pnl_source, reason=reason
    )._value.get()


@pytest.mark.asyncio
async def test_single_row_books_the_closing_fills() -> None:
    pm = _manager([_row("a", 0.05)])
    fetcher = AsyncMock(
        return_value=[_fill(1, "0.02", "275.0"), _fill(2, "0.03", "276.0")]
    )
    before = _count("exchange_fills", "-")

    plans = await _reconciler(pm, fetcher).reconcile(_flat())

    assert plans[0]["applied"] is True
    fetcher.assert_awaited_once_with("BCHUSDT", int(ENTRY.timestamp() * 1000))
    close = pm.record_position_close.await_args.kwargs
    assert close["pnl_unknown"] is False
    assert close["exit_price"] == pytest.approx(275.6)  # (0.02*275 + 0.03*276)/0.05
    assert close["exit_qty"] == 0.05
    assert close["commission"] == pytest.approx(0.028)
    assert close["fee_asset"] == "USDT"
    assert close["exit_order_id"] == "777"
    assert close["trade_id"] == "1,2"
    assert close["close_reason"] == CLOSE_REASON
    assert _count("exchange_fills", "-") == before + 1


@pytest.mark.asyncio
async def test_two_rows_on_one_side_stay_unknown_with_a_named_reason() -> None:
    pm = _manager([_row("a", 0.05), _row("b", 0.05, ENTRY + timedelta(minutes=5))])
    fetcher = AsyncMock(return_value=[_fill(1, "0.10", "275.0")])
    before = _count("unattributed", "symbol_side_shared")

    await _reconciler(pm, fetcher).reconcile(_flat())

    assert fetcher.await_count == 1  # one read per symbol per pass
    closes = [c.kwargs for c in pm.record_position_close.await_args_list]
    assert [c["position_id"] for c in closes] == ["a", "b"]
    assert all(c["pnl_unknown"] is True for c in closes)
    assert all(c["exit_price"] == 268.0 for c in closes)  # mark price, as before
    assert _count("unattributed", "symbol_side_shared") == before + 2


@pytest.mark.asyncio
async def test_failed_read_still_closes_as_unknown_and_counts_missing() -> None:
    pm = _manager([_row("a", 0.05)])
    fetcher = AsyncMock(side_effect=RuntimeError("HTTP 503"))
    before = _count("missing", "fetch_failed")

    plans = await _reconciler(pm, fetcher).reconcile(_flat())

    assert plans[0]["applied"] is True
    close = pm.record_position_close.await_args.kwargs
    assert close["pnl_unknown"] is True
    assert close["exit_order_id"].startswith("reconcile-BCHUSDT-LONG-a-")
    assert _count("missing", "fetch_failed") == before + 1


@pytest.mark.asyncio
async def test_real_position_manager_books_net_pnl_after_fees() -> None:
    pm = PositionManager()
    pm.position_records = {"a": _row("a", 0.05)}
    pm.publish_unattributed_exchange_close = AsyncMock()  # type: ignore[method-assign]
    fetcher = AsyncMock(return_value=[_fill(1, "0.05", "276.0")])

    with patch(
        "tradeengine.position_manager.position_client.update_position",
        new=AsyncMock(return_value=MagicMock(ok=True)),
    ) as update:
        await _reconciler(pm, fetcher).reconcile(_flat())

    data = update.await_args.args[1]
    assert data["status"] == "closed"
    assert data["pnl_unknown"] is False
    assert data["exit_price"] == 276.0
    assert data["pnl"] == pytest.approx(0.3)  # (276 - 270) * 0.05
    assert data["final_commission"] == pytest.approx(0.014)
    assert data["pnl_after_fees"] == pytest.approx(0.3 - 0.014)
