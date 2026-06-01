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


def _mocks(memory=None, mysql=None, exchange_raises=False, binance_open_ids=None):
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
    # AC3 of #424: stops-health now verifies stored sl/tp ids against
    # the on-Binance open-order set; tests must seed this explicitly so
    # the verified-healthy path can be exercised.
    exc.get_all_open_orders = AsyncMock(return_value=set(binance_open_ids or []))

    pub = MagicMock()
    pub.publish = AsyncMock(return_value=True)

    return spm, pc, exc, pub


@pytest.mark.asyncio
async def test_all_healthy():
    # AC3 of #424: real Binance algo-order IDs (>=13 digit integers) AND
    # both must be in the on-Binance open-orders set before stops-health
    # may declare a position healthy.
    real_sl = "1398104567890123"
    real_tp = "1398104567890124"
    pos = _pos(sl_order_id=real_sl, tp_order_id=real_tp)
    spm, pc, exc, pub = _mocks(memory=[pos], binance_open_ids={real_sl, real_tp})

    resp = await check_position_stops(spm, pc, exc, pub)

    assert resp.violation_count == 0
    assert resp.healthy_count == 1
    assert resp.total_checked == 1
    assert resp.alarms_emitted == 0
    assert resp.positions[0].status == "healthy"
    assert resp.divergences == []
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


# ---------------------------------------------------------------------------
# AC3 of #424 — Binance verification + divergences + Pydantic validator
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_h3_price_string_ids_are_not_healthy():
    """AC3 / H3 of #424: stored sl/tp IDs that are price strings (e.g.
    "2022.6338") must NOT be reported healthy regardless of memory
    state — they fail both the algo-ID shape check AND on-Binance
    presence. Mirrors test_h3_stops_health_must_verify_against_binance
    from the 2026-05-30 incident reproduction file."""
    pos = _pos(
        spid="repro-h3-1",
        symbol="ETHUSDT",
        side="LONG",
        sl_order_id="2022.6338",
        tp_order_id="2050.12",
        stop_loss_price=1980.0,
        take_profit_price=2060.0,
        entry_quantity=0.05,
        entry_price=2022.6338,
    )
    spm, pc, exc, pub = _mocks(memory=[pos], binance_open_ids=set())

    resp = await check_position_stops(spm, pc, exc, pub)

    assert resp.healthy_count == 0
    assert resp.violation_count == 1
    assert len(resp.divergences) == 1

    dv = resp.divergences[0]
    assert dv.strategy_position_id == "repro-h3-1"
    assert dv.symbol == "ETHUSDT"
    assert dv.sl_id_is_real_algo_id is False
    assert dv.tp_id_is_real_algo_id is False
    assert dv.sl_present_on_binance is False
    assert dv.tp_present_on_binance is False


@pytest.mark.asyncio
async def test_h3_real_ids_but_missing_on_binance_reports_divergence():
    """AC3: even with proper-shape IDs, a position is NOT healthy when
    Binance reports neither ID as open."""
    real_sl = "1398104567890123"
    real_tp = "1398104567890124"
    pos = _pos(
        sl_order_id=real_sl,
        tp_order_id=real_tp,
        stop_loss_price=45000.0,
        take_profit_price=55000.0,
    )
    # Binance returns an unrelated open order — neither stored ID present.
    spm, pc, exc, pub = _mocks(memory=[pos], binance_open_ids={"9999999999999"})

    resp = await check_position_stops(spm, pc, exc, pub)

    assert resp.healthy_count == 0
    assert resp.violation_count == 1
    assert len(resp.divergences) == 1
    dv = resp.divergences[0]
    assert dv.sl_id_is_real_algo_id is True
    assert dv.tp_id_is_real_algo_id is True
    assert dv.sl_present_on_binance is False
    assert dv.tp_present_on_binance is False


@pytest.mark.asyncio
async def test_h3_only_one_side_on_binance_reports_partial_divergence():
    """AC3: when only SL is on Binance and TP is missing, the position
    is unhealthy with sl_present=True / tp_present=False."""
    real_sl = "1398104567890123"
    real_tp = "1398104567890124"
    pos = _pos(
        sl_order_id=real_sl,
        tp_order_id=real_tp,
        stop_loss_price=45000.0,
        take_profit_price=55000.0,
    )
    # Only SL is on Binance.
    spm, pc, exc, pub = _mocks(memory=[pos], binance_open_ids={real_sl})

    resp = await check_position_stops(spm, pc, exc, pub)

    assert resp.healthy_count == 0
    assert len(resp.divergences) == 1
    dv = resp.divergences[0]
    assert dv.sl_present_on_binance is True
    assert dv.tp_present_on_binance is False


def test_risk_order_ids_validator_accepts_real_algo_ids():
    """AC3: Pydantic validator MUST accept 13+ digit integer-string IDs
    (Binance algo-order shape) and None."""
    from tradeengine.position_health_guard import RiskOrderIds

    ok = RiskOrderIds(sl_order_id="1398104567890123", tp_order_id=None)
    assert ok.sl_order_id == "1398104567890123"
    assert ok.tp_order_id is None


def test_risk_order_ids_validator_rejects_price_string():
    """AC3: Pydantic validator MUST reject price-shaped strings — the
    exact failure mode that produced 366 falsely-healthy positions on
    2026-05-30."""
    from pydantic import ValidationError

    from tradeengine.position_health_guard import RiskOrderIds

    with pytest.raises(ValidationError) as sl_exc:
        RiskOrderIds(sl_order_id="2022.6338", tp_order_id=None)
    assert "Binance algo-order ID" in str(sl_exc.value)

    with pytest.raises(ValidationError) as tp_exc:
        RiskOrderIds(sl_order_id=None, tp_order_id="2050.12")
    assert "Binance algo-order ID" in str(tp_exc.value)


def test_risk_order_ids_validator_rejects_short_integers():
    """AC3: a 5-digit number is shorter than any Binance algo-order ID
    — reject as well so debug placeholders cannot leak through."""
    from pydantic import ValidationError

    from tradeengine.position_health_guard import RiskOrderIds

    with pytest.raises(ValidationError) as exc:
        RiskOrderIds(sl_order_id="12345", tp_order_id=None)
    assert "Binance algo-order ID" in str(exc.value)


@pytest.mark.asyncio
async def test_set_strategy_position_orders_validates_via_pydantic():
    """AC3: the StrategyPositionManager setter rejects non-algo IDs at
    the boundary so future callers cannot reintroduce the bug."""
    from pydantic import ValidationError

    from tradeengine.strategy_position_manager import StrategyPositionManager

    spm = StrategyPositionManager()
    spm.strategy_positions = {
        "spid-1": {
            "strategy_position_id": "spid-1",
            "symbol": "BTCUSDT",
            "side": "LONG",
            "sl_order_id": None,
            "tp_order_id": None,
        }
    }

    # Real-shape IDs accepted + persisted in memory
    await spm.set_strategy_position_orders(
        strategy_position_id="spid-1",
        sl_order_id="1398104567890123",
        tp_order_id="1398104567890124",
    )
    assert spm.strategy_positions["spid-1"]["sl_order_id"] == "1398104567890123"
    assert spm.strategy_positions["spid-1"]["tp_order_id"] == "1398104567890124"

    # Price-shaped value rejected at the boundary
    with pytest.raises(ValidationError) as exc:
        await spm.set_strategy_position_orders(
            strategy_position_id="spid-1",
            sl_order_id="2022.6338",
        )
    assert "Binance algo-order ID" in str(exc.value)
