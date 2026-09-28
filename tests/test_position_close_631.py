from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from shared.constants import UTC
from tradeengine.position_manager import PositionManager


def _manager(quantity: float = 2.0, side: str = "LONG") -> PositionManager:
    manager = PositionManager()
    manager.position_records = {
        "P": {
            "position_id": "P",
            "symbol": "BTCUSDT",
            "position_side": side,
            "entry_price": 100.0,
            "entry_quantity": quantity,
            "quantity": quantity,
            "entry_time": datetime(2026, 1, 1, tzinfo=UTC),
            "commission_total": 0.0,
            "exchange": "binance",
            "strategy_id": "test",
            "status": "open",
        }
    }
    return manager


@pytest.mark.asyncio
async def test_full_close_records_real_pnl_and_columns():
    manager = _manager()
    with (
        patch(
            "shared.mysql_client.position_client.update_position",
            new_callable=AsyncMock,
        ) as update,
        patch(
            "tradeengine.position_manager.trading_store.update_daily_pnl",
            new_callable=AsyncMock,
        ),
    ):
        await manager.record_position_close(
            "P", 110.0, 2.0, "exit-1", datetime.now(UTC), "take_profit"
        )

    body = update.await_args.args[1]
    assert body["status"] == "closed"
    assert body["exit_price"] == 110.0
    assert body["pnl"] == pytest.approx(20.0)
    assert body["pnl_after_fees"] == pytest.approx(20.0)
    assert "closed_at" not in body
    assert "final_realized_pnl" not in body
    assert manager.daily_pnl == pytest.approx(20.0)


@pytest.mark.asyncio
async def test_short_loss_and_partial_close():
    manager = _manager(side="SHORT")
    with (
        patch(
            "shared.mysql_client.position_client.update_position",
            new_callable=AsyncMock,
        ) as update,
        patch(
            "tradeengine.position_manager.trading_store.update_daily_pnl",
            new_callable=AsyncMock,
        ),
    ):
        await manager.record_position_close(
            "P", 110.0, 1.0, "exit-2", datetime.now(UTC), "stop_loss"
        )

    body = update.await_args.args[1]
    assert body["status"] == "open"
    assert body["quantity"] == pytest.approx(1.0)
    assert body["pnl"] == pytest.approx(-10.0)
    assert manager.daily_pnl == pytest.approx(-10.0)


@pytest.mark.asyncio
async def test_duplicate_exit_order_is_a_no_op():
    manager = _manager()
    with (
        patch(
            "shared.mysql_client.position_client.update_position",
            new_callable=AsyncMock,
        ) as update,
        patch(
            "tradeengine.position_manager.trading_store.update_daily_pnl",
            new_callable=AsyncMock,
        ),
    ):
        await manager.record_position_close(
            "P", 110.0, 2.0, "exit-3", datetime.now(UTC), "take_profit"
        )
        await manager.record_position_close(
            "P", 110.0, 2.0, "exit-3", datetime.now(UTC), "take_profit"
        )

    assert update.await_count == 1
    assert manager.daily_pnl == pytest.approx(20.0)
