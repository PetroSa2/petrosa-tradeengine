import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from tradeengine.position_health_guard import check_position_stops


def _pos(
    spid="pos-1",
    symbol="BTCUSDT",
    side="LONG",
    sl_order_id=None,
    tp_order_id=None,
    stop_loss_price=None,
    take_profit_price=None,
    entry_quantity=0.01,
    entry_price=50000.0,
    strategy_id="strat-1",
    status="open",
):
    return {
        "strategy_position_id": spid,
        "symbol": symbol,
        "side": side,
        "sl_order_id": sl_order_id,
        "tp_order_id": tp_order_id,
        "stop_loss_price": stop_loss_price,
        "take_profit_price": take_profit_price,
        "entry_quantity": entry_quantity,
        "entry_price": entry_price,
        "strategy_id": strategy_id,
        "status": status,
    }


def _mocks(memory=None, mysql=None, exchange_raises=False):
    spm = MagicMock()
    spm.get_all_open_strategy_positions.return_value = memory or []
    spm.close_strategy_position = AsyncMock()

    pc = MagicMock()
    pc.get_open_positions = AsyncMock(return_value=mysql or [])

    exc = MagicMock()
    if exchange_raises:
        exc.execute = AsyncMock(side_effect=Exception("exchange error"))
    else:
        exc.execute = AsyncMock(return_value={"order_id": "mock-order-id"})

    pub = MagicMock()
    pub.publish = AsyncMock(return_value=True)

    return spm, pc, exc, pub


@pytest.mark.asyncio
async def test_all_healthy():
    pos = _pos(sl_order_id="sl-1", tp_order_id="tp-1")
    spm, pc, exc, pub = _mocks(memory=[pos])

    resp = await check_position_stops(spm, pc, exc, pub)

    assert resp.violation_count == 0
    assert resp.healthy_count == 1
    assert resp.total_checked == 1
    assert resp.alarms_emitted == 0
    assert resp.positions[0].status == "healthy"
    exc.execute.assert_not_called()


@pytest.mark.asyncio
async def test_missing_sl_remediation_success():
    pos = _pos(sl_order_id=None, tp_order_id="tp-1", stop_loss_price=45000.0)
    spm, pc, exc, pub = _mocks(memory=[pos])

    resp = await check_position_stops(spm, pc, exc, pub)

    assert resp.violation_count == 1
    assert resp.positions[0].remediation_outcome == "sl_placed"
    spm.close_strategy_position.assert_not_called()
    pub.publish.assert_not_called()
    exc.execute.assert_called_once()


@pytest.mark.asyncio
async def test_missing_tp_remediation_success():
    pos = _pos(sl_order_id="sl-1", tp_order_id=None, take_profit_price=55000.0)
    spm, pc, exc, pub = _mocks(memory=[pos])

    resp = await check_position_stops(spm, pc, exc, pub)

    assert resp.violation_count == 1
    assert resp.positions[0].remediation_outcome == "tp_placed"
    spm.close_strategy_position.assert_not_called()
    exc.execute.assert_called_once()


@pytest.mark.asyncio
async def test_missing_both_both_succeed():
    pos = _pos(
        sl_order_id=None,
        tp_order_id=None,
        stop_loss_price=45000.0,
        take_profit_price=55000.0,
    )
    spm, pc, exc, pub = _mocks(memory=[pos])

    resp = await check_position_stops(spm, pc, exc, pub)

    assert resp.violation_count == 1
    assert resp.positions[0].remediation_outcome == "both_placed"
    assert exc.execute.call_count == 2
    spm.close_strategy_position.assert_not_called()


@pytest.mark.asyncio
async def test_remediation_fails_closes_position():
    pos = _pos(sl_order_id=None, tp_order_id="tp-1", stop_loss_price=45000.0)
    spm, pc, exc, pub = _mocks(memory=[pos], exchange_raises=True)

    resp = await check_position_stops(spm, pc, exc, pub)

    assert resp.positions[0].remediation_outcome == "position_closed"
    assert resp.alarms_emitted >= 1
    spm.close_strategy_position.assert_called_once_with(
        strategy_position_id="pos-1",
        exit_price=50000.0,
        close_reason="force_closed_missing_stops",
    )
    pub.publish.assert_called_once()
    assert (
        pub.publish.call_args.kwargs["event_type"] == "position_force_closed_no_stops"
    )


@pytest.mark.asyncio
async def test_missing_price_skips_to_close():
    pos = _pos(sl_order_id=None, tp_order_id="tp-1", stop_loss_price=None)
    spm, pc, exc, pub = _mocks(memory=[pos])

    resp = await check_position_stops(spm, pc, exc, pub)

    exc.execute.assert_not_called()
    assert resp.positions[0].remediation_outcome == "position_closed"
    assert resp.alarms_emitted >= 1
    spm.close_strategy_position.assert_called_once()


@pytest.mark.asyncio
async def test_mysql_only_position_detected():
    mysql_pos = _pos(
        spid="mysql-pos-1",
        sl_order_id=None,
        tp_order_id="tp-1",
        stop_loss_price=45000.0,
    )
    spm, pc, exc, pub = _mocks(memory=[], mysql=[mysql_pos])

    resp = await check_position_stops(spm, pc, exc, pub)

    assert resp.violation_count == 1
    assert resp.positions[0].source == "mysql"
    assert resp.positions[0].strategy_position_id == "mysql-pos-1"


@pytest.mark.asyncio
async def test_empty_no_open_positions():
    spm, pc, exc, pub = _mocks(memory=[], mysql=[])

    resp = await check_position_stops(spm, pc, exc, pub)

    assert resp.total_checked == 0
    assert resp.violation_count == 0
    assert resp.alarms_emitted == 0
    assert len(resp.positions) == 0
