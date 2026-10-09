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
            "client_order_id": "cio-position",
            "entry_order_id": "entry-1",
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


@pytest.mark.asyncio
async def test_distinct_trade_ids_allow_partial_fills_and_preserve_fee_status():
    manager = _manager()
    with patch(
        "shared.mysql_client.position_client.update_position",
        new_callable=AsyncMock,
    ) as update:
        await manager.record_position_close(
            "P",
            110.0,
            1.0,
            "exit-4",
            datetime.now(UTC),
            "take_profit",
            commission=-0.5,
            trade_id="trade-1",
            fee_asset="USDT",
        )
        await manager.record_position_close(
            "P",
            111.0,
            1.0,
            "exit-4",
            datetime.now(UTC),
            "take_profit",
            commission=None,
            trade_id="trade-2",
            fee_asset="USDT",
        )

    assert update.await_count == 2
    body = update.await_args.args[1]
    assert body["fee_status"] == "unknown"
    assert body["pnl_unknown"] is False
    assert body["closed_by_strategy_id"] == "test"
    assert manager.daily_pnl == pytest.approx(20.5)


@pytest.mark.asyncio
async def test_missing_exit_price_is_flagged_not_zero_pnl():
    manager = _manager()
    with patch(
        "shared.mysql_client.position_client.update_position",
        new_callable=AsyncMock,
    ) as update:
        await manager.record_position_close(
            "P",
            None,
            2.0,
            "exit-5",
            datetime.now(UTC),
            "manual",
            commission=0.0,
            trade_id="trade-5",
        )

    body = update.await_args.args[1]
    assert body["pnl"] is None
    assert body["pnl_unknown"] is True
    assert body["close_reason"] == "manual"


@pytest.mark.asyncio
async def test_unknown_close_publishes_position_closed_without_pnl():
    manager = _manager()
    with (
        patch(
            "shared.mysql_client.position_client.update_position",
            new_callable=AsyncMock,
        ),
        patch(
            "tradeengine.position_manager.execution_event_publisher.publish",
            new_callable=AsyncMock,
        ) as publish,
    ):
        await manager.record_position_close(
            "P",
            None,
            1.0,
            "reconcile-1",
            datetime.now(UTC),
            "reconciled_to_exchange",
            pnl_unknown=True,
        )

    event = publish.await_args.kwargs
    assert event["event_type"] == "position_closed"
    assert event["client_order_id"] == "cio-position"
    assert event["extra"]["pnl_basis"] == "unknown"
    assert event["extra"]["pnl"] is None
