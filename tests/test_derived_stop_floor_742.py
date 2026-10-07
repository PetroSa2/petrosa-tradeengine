"""Derived minimum stop distance (petrosa-tradeengine#742, rule 23 of PetroSa2/petrosa_k8s#1239)."""

from __future__ import annotations

import math
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from tradeengine.dispatcher import Dispatcher
from tradeengine.naked_position_remediator import NakedPositionRemediator
from tradeengine.risk.sl_tp_direction import correct_protective_price
from tradeengine.stop_floor import (
    StopFloorProvider,
    max_placeable_fraction,
    noise_floor,
    noise_multiplier,
    stop_floor,
    technical_floor,
)

BTC = "BTCUSDT"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (
        "TE_MIN_SL_DISTANCE_PCT",
        "TE_STOP_FLOOR_Q",
        "TE_STOP_FLOOR_H_HOURS",
    ):
        monkeypatch.delenv(name, raising=False)


# --- the formulas --------------------------------------------------------------------------------


def test_noise_multiplier_for_q_ten_percent():
    assert noise_multiplier(0.10) == pytest.approx(1.6448536, rel=1e-6)


def test_noise_floor_for_synthetic_sigma_and_horizon():
    # sigma_1h 0.36% over 4 h: k x sigma x sqrt(4) = 1.645 x 0.0036 x 2 = 1.18% (the #1239 BTC value)
    assert noise_floor(0.0036, 4.0, 0.10) == pytest.approx(0.011843, rel=1e-3)
    # a longer horizon widens it with the square root of H
    assert noise_floor(0.0036, 16.0, 0.10) == pytest.approx(
        2 * noise_floor(0.0036, 4.0, 0.10)
    )
    # a larger q (more tolerated noise) narrows it
    assert noise_floor(0.0036, 4.0, 0.20) < noise_floor(0.0036, 4.0, 0.10)


def test_technical_floor_is_ten_ticks_or_three_spreads_capped_by_the_band():
    # tick 0.1 on 50000: 10 ticks = 2 bp; 3 x a 1.5 bp spread = 4.5 bp wins
    assert technical_floor(0.1, 50_000.0, 0.00015, 0.04) == pytest.approx(0.00045)
    assert technical_floor(0.1, 50_000.0, None, 0.04) == pytest.approx(0.00002)
    assert (
        technical_floor(0.1, 50_000.0, 0.5, 0.04) == 0.04
    )  # capped by the PERCENT_PRICE band


def test_max_placeable_fraction_keeps_the_adjusters_margin():
    # a +-5% band minus the 1% safety margin on the multipliers
    assert max_placeable_fraction(1.05, 0.95) == pytest.approx(
        min(1.05 * 0.99 - 1, 1 - 0.95 * 1.01)
    )


# --- the provider --------------------------------------------------------------------------------


def _exchange(
    price=50_000.0, tick="0.1", up="1.05", down="0.95", bid="49999.9", ask="50000.1"
):
    exchange = MagicMock()
    exchange.symbol_info = {
        BTC: {"filters": [{"filterType": "PRICE_FILTER", "tickSize": tick}]}
    }
    exchange._get_current_price = AsyncMock(return_value=price)
    exchange.get_percent_price_filter = MagicMock(
        return_value={"multiplierUp": up, "multiplierDown": down}
    )
    exchange.client.futures_orderbook_ticker = MagicMock(
        return_value={"bidPrice": bid, "askPrice": ask}
    )
    return exchange


def _risk_inputs(sigma=0.0036, sufficient=True, extra=None):
    body = {
        "symbols": {
            BTC: {
                "hourly": {
                    "sigma_1h": sigma,
                    "n_returns": 336,
                    "sufficient": sufficient,
                }
            }
        }
    }
    body.update(extra or {})
    return body


async def _provider(body=None, exchange=None):
    client = MagicMock()
    client.request = AsyncMock(
        return_value=body if body is not None else _risk_inputs()
    )
    provider = StopFloorProvider()
    provider.configure(client, exchange or _exchange(), [BTC])
    await provider.refresh()
    return provider


@pytest.mark.asyncio
async def test_derived_floor_is_the_larger_of_technical_and_noise():
    provider = await _provider()
    result = provider.floor(BTC)
    assert result.source == "derived"
    assert result.reason is None
    assert result.noise_pct == pytest.approx(1.1843, rel=1e-3)
    assert result.technical_pct < result.noise_pct
    assert result.pct == pytest.approx(result.noise_pct)
    assert result.sigma_1h == 0.0036
    assert (result.horizon_hours, result.horizon_source) == (
        4.0,
        "fallback",
    )  # H labelled fallback
    assert result.q == 0.10 and result.k == pytest.approx(1.6449, rel=1e-3)
    assert result.placeable is True
    assert result.fraction == pytest.approx(result.pct / 100)


@pytest.mark.asyncio
async def test_a_wide_spread_can_make_the_technical_floor_win():
    exchange = _exchange(
        bid="49000", ask="51000"
    )  # a 4% spread: 3 x 4% = 12%, capped by the band
    result = (await _provider(_risk_inputs(sigma=0.0001), exchange)).floor(BTC)
    assert result.source == "derived"
    assert result.technical_pct > result.noise_pct
    assert result.technical_pct == pytest.approx(
        max_placeable_fraction(1.05, 0.95) * 100
    )


@pytest.mark.asyncio
async def test_horizon_comes_from_the_strategys_median_holding_time_when_known():
    body = _risk_inputs(
        extra={"strategies": {"s1": {"median_holding_seconds": 4 * 3600 * 4}}}
    )
    provider = await _provider(body)
    default = provider.floor(BTC)
    held = provider.floor(BTC, strategy_id="s1")
    assert (held.horizon_hours, held.horizon_source) == (16.0, "median_holding_time")
    assert held.noise_pct == pytest.approx(2 * default.noise_pct)


@pytest.mark.asyncio
async def test_q_and_the_fallback_horizon_are_configurable(monkeypatch):
    provider = await _provider()
    monkeypatch.setenv("TE_STOP_FLOOR_Q", "0.2")
    monkeypatch.setenv("TE_STOP_FLOOR_H_HOURS", "8")
    result = provider.floor(BTC)
    assert result.q == 0.2 and result.horizon_hours == 8.0
    assert result.noise_pct == pytest.approx(noise_floor(0.0036, 8.0, 0.2) * 100)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ({"symbols": {}}, "sigma_1h_unavailable"),
        (_risk_inputs(sufficient=False), "sigma_1h_insufficient"),
        ({"symbols": {BTC: {"hourly": {"sigma_1h": None}}}}, "sigma_1h_unavailable"),
    ],
)
async def test_missing_volatility_falls_back_to_the_fixed_floor_labelled(body, reason):
    result = (await _provider(body)).floor(BTC)
    assert result.source == "fallback"
    assert result.pct == 6.0
    assert result.reason == reason
    assert result.placeable is True


@pytest.mark.asyncio
async def test_data_manager_failure_falls_back():
    client = MagicMock()
    client.request = AsyncMock(side_effect=RuntimeError("down"))
    provider = StopFloorProvider()
    provider.configure(client, _exchange(), [BTC])
    await provider.refresh()
    assert provider.floor(BTC).source == "fallback"
    assert provider.floor("UNKNOWN").reason == "no_inputs"


@pytest.mark.asyncio
async def test_missing_exchange_filters_fall_back():
    exchange = _exchange()
    exchange._get_current_price = AsyncMock(side_effect=RuntimeError("no price"))
    result = (await _provider(exchange=exchange)).floor(BTC)
    assert result.source == "fallback"
    assert result.reason == "exchange_filters_unavailable"


@pytest.mark.asyncio
async def test_stale_volatility_falls_back():
    provider = await _provider()
    provider._clock = lambda: 10**9
    result = provider.floor(BTC)
    assert result.source == "fallback" and result.reason == "sigma_1h_stale"


@pytest.mark.asyncio
async def test_an_explicit_env_floor_pins_the_value(monkeypatch):
    provider = await _provider()
    monkeypatch.setenv("TE_MIN_SL_DISTANCE_PCT", "2.5")
    result = provider.floor(BTC)
    assert result.source == "env"
    assert result.placeable is True


# --- unplaceable floor: skip the order -----------------------------------------------------------


@pytest.mark.asyncio
async def test_a_floor_beyond_the_placeable_band_is_flagged_and_the_order_skipped(
    monkeypatch,
):
    provider = await _provider(_risk_inputs(sigma=0.02))  # noise floor ~13%, band ~4%
    result = provider.floor(BTC)
    assert result.source == "derived"
    assert result.pct > result.max_placeable_pct
    assert result.placeable is False

    monkeypatch.setattr("tradeengine.dispatcher.stop_floor", provider)
    dispatcher = Dispatcher.__new__(Dispatcher)
    dispatcher.logger = MagicMock()
    dispatcher._reject_sizing = AsyncMock(return_value={"status": "rejected"})
    order = SimpleNamespace(
        symbol=BTC, reduce_only=False, simulate=False, strategy_metadata={}
    )
    out = await dispatcher._apply_stop_floor_check(order)
    assert out == {"status": "rejected"}
    args = dispatcher._reject_sizing.await_args.args
    assert args[1] == "stop_floor_unplaceable"
    assert "exceeds the maximum placeable distance" in args[2]


@pytest.mark.asyncio
async def test_placeable_fallback_reduce_only_and_simulated_orders_are_not_skipped(
    monkeypatch,
):
    dispatcher = Dispatcher.__new__(Dispatcher)
    dispatcher.logger = MagicMock()
    dispatcher._reject_sizing = AsyncMock()

    placeable = await _provider()
    monkeypatch.setattr("tradeengine.dispatcher.stop_floor", placeable)
    order = SimpleNamespace(
        symbol=BTC, reduce_only=False, simulate=False, strategy_metadata={}
    )
    assert await dispatcher._apply_stop_floor_check(order) is None

    # the fixed fallback (6% against a 5% band) keeps its clamping behaviour: not skipped
    fallback = await _provider({"symbols": {}})
    monkeypatch.setattr("tradeengine.dispatcher.stop_floor", fallback)
    assert await dispatcher._apply_stop_floor_check(order) is None

    unplaceable = await _provider(_risk_inputs(sigma=0.02))
    monkeypatch.setattr("tradeengine.dispatcher.stop_floor", unplaceable)
    for flags in (
        {"reduce_only": True, "simulate": False},
        {"reduce_only": False, "simulate": True},
    ):
        exempt = SimpleNamespace(symbol=BTC, strategy_metadata={}, **flags)
        assert await dispatcher._apply_stop_floor_check(exempt) is None
    dispatcher._reject_sizing.assert_not_awaited()


# --- /state and the units ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_state_reports_the_derived_floor_in_percent_with_its_inputs(monkeypatch):
    provider = await _provider()
    monkeypatch.setattr("tradeengine.dispatcher.stop_floor", provider)
    state = Dispatcher._stop_floor_state(BTC)
    assert state["min_sl_distance_source"] == "derived"
    assert state["min_sl_distance_pct"] == pytest.approx(
        1.1843, rel=1e-3
    )  # PERCENT, not a fraction
    assert state["min_sl_entry_distance_pct"] == pytest.approx(
        0.005
    )  # the sibling stays a fraction
    detail = state["stop_floor"]
    assert detail["sigma_1h"] == 0.0036
    assert detail["horizon_hours"] == 4.0 and detail["horizon_source"] == "fallback"
    assert detail["q"] == 0.10
    assert detail["placeable"] is True


def test_state_without_a_symbol_keeps_the_configured_shape(monkeypatch):
    state = Dispatcher._stop_floor_state()
    assert state["min_sl_distance_pct"] == 6.0
    assert state["min_sl_distance_source"] == "fallback"
    assert "stop_floor" not in state


@pytest.mark.asyncio
async def test_state_falls_back_with_the_reason_when_inputs_are_missing(monkeypatch):
    provider = await _provider({"symbols": {}})
    monkeypatch.setattr("tradeengine.dispatcher.stop_floor", provider)
    state = Dispatcher._stop_floor_state(BTC)
    assert state["min_sl_distance_pct"] == 6.0
    assert state["min_sl_distance_source"] == "fallback"
    assert state["min_sl_distance_reason"] == "sigma_1h_unavailable"


# --- take-profit is unchanged; the remediator uses the floor in force ----------------------------


def test_take_profit_is_not_widened_by_the_floor():
    # the entry path corrects a take-profit only for direction, with no distance floor (option A)
    result = correct_protective_price(
        kind="TP",
        position_side="LONG",
        requested_price=100.4,
        requested_pct=0.004,
        reference_price=100.0,
        min_distance_pct=0.0,
    )
    assert result.was_corrected is False
    assert result.price == 100.4


def _remediator(**kwargs):
    exchange = MagicMock()
    exchange.execute = AsyncMock(return_value={"status": "FILLED"})
    pm = MagicMock()
    pm.get_positions = MagicMock(return_value={})
    return NakedPositionRemediator(
        exchange=exchange,
        position_manager=pm,
        close_position=AsyncMock(return_value={}),
        mode="arm_only",
        **kwargs,
    )


def test_remediator_widens_a_tight_stop_to_the_derived_floor_per_symbol():
    positions = {(BTC, "LONG"): {"entryPrice": 100.0, "positionAmt": 1.0}}
    remediator = _remediator(
        min_sl_distance_pct=6.0, floor_provider=lambda symbol: 1.5, fallback_sl_pct=0.5
    )
    sl, tp, _ = remediator._derive_protective_prices(BTC, "LONG", positions)
    assert sl == pytest.approx(98.5)  # widened to 1.5% (not 6%)
    assert tp == pytest.approx(104.0)  # the take-profit is left alone


def test_remediator_falls_back_to_the_fixed_floor_when_the_provider_fails():
    def broken(symbol):
        raise RuntimeError("no floor")

    positions = {(BTC, "LONG"): {"entryPrice": 100.0, "positionAmt": 1.0}}
    remediator = _remediator(
        min_sl_distance_pct=6.0, floor_provider=broken, fallback_sl_pct=0.5
    )
    sl, _, _ = remediator._derive_protective_prices(BTC, "LONG", positions)
    assert sl == pytest.approx(94.0)


def test_the_module_level_provider_is_the_one_the_dispatcher_uses():
    import tradeengine.dispatcher as dispatcher_module

    assert dispatcher_module.stop_floor is stop_floor
    assert math.isfinite(noise_multiplier(0.1))
