"""The effective stop floor is reported, its log is accurate, and an add-on entry never moves the stop (te#738).

First two probe entries (ETHUSDT BUY 0.008 each): the log called a stop 3% below a long's market a wrong-side stop
("would immediately trigger"), every stop was widened to the fixed 6% floor, and #651 resized the aggregated ETH
LONG legs (0.665 -> 0.673 -> 0.681) as each probe entry arrived.
"""

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.binance_futures_fake import (
    FakeFuturesClient,
    make_exchange,
    pin_binance_module,
)
from tradeengine.dispatcher import Dispatcher, OCOManager
from tradeengine.protective_legs import ProtectiveLegManager
from tradeengine.risk.sl_tp_direction import enforce_market_side_stop

DOT = "DOTUSDT"
MARKET = 2696.71


# -- the log distinguishes a floor adjustment from a wrong-side stop ---------------------------
def test_a_long_stop_inside_the_floor_is_widened_and_the_log_says_it_would_not_trigger():
    decision = enforce_market_side_stop(
        position_side="LONG",
        stop_price=2615.1491,  # 3% below market: correct side, inside the 6% floor
        market_price=MARKET,
        min_distance_pct=0.06,
    )

    assert decision.was_reanchored is True
    assert decision.kind == "inside_floor"
    assert decision.price == pytest.approx(MARKET * 0.94)
    assert "would immediately trigger" not in decision.reason
    assert "would not trigger" in decision.reason
    assert "inside the 6.00% safety floor" in decision.reason


def test_a_long_stop_above_market_is_a_wrong_side_stop_and_says_it_would_trigger():
    decision = enforce_market_side_stop(
        position_side="LONG",
        stop_price=2700.0,
        market_price=MARKET,
        min_distance_pct=0.06,
    )

    assert decision.kind == "wrong_side"
    assert "would immediately trigger" in decision.reason
    assert decision.price == pytest.approx(MARKET * 0.94)


def test_a_short_stop_inside_the_floor_and_a_wrong_side_short_stop():
    inside = enforce_market_side_stop(
        position_side="SHORT",
        stop_price=MARKET * 1.03,
        market_price=MARKET,
        min_distance_pct=0.06,
    )
    wrong = enforce_market_side_stop(
        position_side="SHORT",
        stop_price=MARKET * 0.99,
        market_price=MARKET,
        min_distance_pct=0.06,
    )

    assert inside.kind == "inside_floor"
    assert "would not trigger" in inside.reason
    assert wrong.kind == "wrong_side"
    assert "would immediately trigger" in wrong.reason


def test_a_stop_beyond_the_floor_is_left_alone():
    decision = enforce_market_side_stop(
        position_side="LONG",
        stop_price=MARKET * 0.9,
        market_price=MARKET,
        min_distance_pct=0.06,
    )

    assert decision.was_reanchored is False
    assert decision.kind == ""


# -- /state reports the effective floor and where it comes from ----------------------------------
def test_the_default_floor_is_reported_as_a_fallback(monkeypatch):
    monkeypatch.delenv("TE_MIN_SL_DISTANCE_PCT", raising=False)
    monkeypatch.delenv("MIN_SL_DISTANCE_PCT", raising=False)

    state = Dispatcher._stop_floor_state()

    assert state["min_sl_distance_pct"] == 6.0
    assert state["min_sl_distance_source"] == "fallback"
    assert state["min_sl_entry_distance_pct"] == pytest.approx(0.005)
    assert state["min_sl_entry_distance_source"] == "fallback"


def test_a_configured_floor_is_reported_with_the_env_source(monkeypatch):
    monkeypatch.setenv("TE_MIN_SL_DISTANCE_PCT", "2.5")

    state = Dispatcher._stop_floor_state()

    assert state["min_sl_distance_pct"] == 2.5
    assert state["min_sl_distance_source"] == "env"


def test_the_state_risk_limits_carry_the_floor():
    dispatcher = Dispatcher.__new__(Dispatcher)
    dispatcher.position_manager = MagicMock()
    dispatcher.position_manager.get_cio_portfolio_summary.return_value = {}
    dispatcher.position_manager.get_daily_pnl.return_value = 0.0
    dispatcher.position_manager.total_portfolio_value = 10000.0
    dispatcher.position_manager.get_notional_summary.return_value = (0.0, 0.0)
    dispatcher.order_manager = MagicMock()
    dispatcher.order_manager.get_active_orders.return_value = []

    limits = dispatcher.get_cio_state("ETHUSDT")["risk_limits"]

    assert limits["min_sl_distance_pct"] == 6.0
    assert limits["min_sl_distance_source"] in {"env", "fallback"}
    assert "min_sl_entry_distance_pct" in limits


# -- an add-on entry never moves the aggregated stop ----------------------------------------------
@pytest.fixture(autouse=True)
def _explicit_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TE_PROTECTIVE_LEG_MODE", "explicit_qty")
    pin_binance_module(monkeypatch)


@pytest.fixture
def client() -> FakeFuturesClient:
    return FakeFuturesClient()


@pytest.fixture
def oco(client: FakeFuturesClient) -> OCOManager:
    manager = OCOManager(
        exchange=make_exchange(client),
        logger=logging.getLogger("test-738"),
        dispatcher=SimpleNamespace(
            protective_leg_manager=None,
            position_manager=None,
            strategy_position_to_position={},
        ),
    )
    manager.start_monitoring = AsyncMock()  # type: ignore[method-assign]
    return manager


@pytest.fixture
def legs(oco: OCOManager) -> ProtectiveLegManager:
    manager = ProtectiveLegManager(
        oco.exchange,
        oco,
        sync_interval_sec=3600,
        flat_grace_sec=30,
        migrate_legacy=True,
        inversion_check_delays=(0.0,),
    )
    oco.dispatcher.protective_leg_manager = manager
    return manager


async def _place(
    oco: OCOManager, qty: float, stop: float, position_id: str
) -> dict[str, Any]:
    return await oco.place_oco_orders(
        position_id=position_id,
        symbol=DOT,
        position_side="LONG",
        quantity=qty,
        stop_loss_price=stop,
        take_profit_price=4.4,
        strategy_position_id=None,
        entry_price=4.0,
    )


def _stop_triggers(client: FakeFuturesClient) -> list[str]:
    return sorted(
        leg["triggerPrice"]
        for leg in client.open_legs(DOT, "LONG")
        if leg["orderType"] == "STOP_MARKET"
    )


@pytest.mark.asyncio
async def test_an_add_on_entry_with_a_looser_stop_keeps_the_existing_leg_price(
    oco, legs, client, caplog
):
    caplog.set_level(logging.INFO)
    client.set_position(DOT, "LONG", 10.0)
    first = await _place(oco, 10.0, 3.6, "pos-1")
    assert first["status"] == "success"
    assert _stop_triggers(client) == ["3.600"]

    # A probe entry grows the side and carries a stop FURTHER from market than the existing leg.
    client.set_position(DOT, "LONG", 10.5)
    second = await _place(oco, 0.5, 3.0, "pos-2")
    await legs.sync_side(DOT, "LONG", reason="add_on")

    assert second["status"] in {"rejected", "skipped_exchange_pair_exists"}
    assert _stop_triggers(client) == ["3.600"]  # the existing stop price did not move
    assert {leg["quantity"] for leg in client.open_legs(DOT, "LONG")} == {
        "10.5"
    }  # only resized
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "#738: add-on entry" in messages
    assert "keeps the existing SL leg" in messages
    assert "3.6" in messages and "3.0" in messages
    assert "not tighter" in messages


@pytest.mark.asyncio
async def test_an_add_on_entry_with_a_tighter_stop_is_reported_as_tighter_and_changes_nothing(
    oco, client
):
    client.set_position(DOT, "LONG", 10.0)
    await _place(oco, 10.0, 3.6, "pos-1")

    decision = await oco._log_addon_stop(DOT, "LONG", 3.8)

    assert decision == {
        "action": "kept_existing",
        "old": pytest.approx(3.6),
        "new": 3.8,
        "new_is_tighter": True,
    }
    assert _stop_triggers(client) == ["3.600"]


@pytest.mark.asyncio
async def test_a_short_add_on_compares_in_the_other_direction(oco, client):
    client.set_position(DOT, "SHORT", 10.0)
    await oco.place_oco_orders(
        position_id="pos-1",
        symbol=DOT,
        position_side="SHORT",
        quantity=10.0,
        stop_loss_price=4.4,
        take_profit_price=3.6,
        strategy_position_id=None,
        entry_price=4.0,
    )

    looser = await oco._log_addon_stop(DOT, "SHORT", 4.8)
    tighter = await oco._log_addon_stop(DOT, "SHORT", 4.2)

    assert looser["new_is_tighter"] is False
    assert tighter["new_is_tighter"] is True


@pytest.mark.asyncio
async def test_the_comparison_is_best_effort_and_silent_without_legs(oco, client):
    assert (
        await oco._log_addon_stop(DOT, "LONG", 3.0) is None
    )  # nothing to compare with
    oco.exchange.get_open_algo_orders = AsyncMock(side_effect=RuntimeError("down"))
    assert await oco._log_addon_stop(DOT, "LONG", 3.0) is None
