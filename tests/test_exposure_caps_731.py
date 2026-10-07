"""Volatility-derived exposure caps and the worst-case stop-risk budget (petrosa-tradeengine#731, rule 11)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tradeengine.dispatcher import Dispatcher
from tradeengine.exposure_caps import (
    RiskInputs,
    RiskInputsCache,
    basket_sigma,
    caps_mode,
    check_projection,
    leg_stop_risk,
    net_cap,
    parse_risk_inputs,
    risk_budget,
    snapshot,
    stress_quantile,
    symbol_cap,
)
from tradeengine.position_manager import PositionManager


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for name in ("TE_RISK_BUDGET_PCT", "TE_STRESS_QUANTILE", "TE_EXPOSURE_CAPS_MODE"):
        monkeypatch.delenv(name, raising=False)


def _inputs(sigma=None, rho=0.8):
    sigma = sigma or {"BTCUSDT": 0.019, "ETHUSDT": 0.025, "BCHUSDT": 0.062}
    corr = {a: {b: (1.0 if a == b else rho) for b in sigma} for a in sigma}
    return RiskInputs(sigma_daily=sigma, correlation=corr, fetched_at=1.0)


# --- the operator input --------------------------------------------------------------------------


def test_the_risk_budget_is_an_operator_input_of_three_percent(monkeypatch):
    assert risk_budget() == pytest.approx(0.03)
    assert stress_quantile() == 2.33
    monkeypatch.setenv("TE_RISK_BUDGET_PCT", "2.3")
    assert risk_budget() == pytest.approx(0.023)
    monkeypatch.setenv("TE_RISK_BUDGET_PCT", "nonsense")
    assert risk_budget() == pytest.approx(0.03)


def test_the_mode_defaults_to_report(monkeypatch):
    assert caps_mode() == "report"
    monkeypatch.setenv("TE_EXPOSURE_CAPS_MODE", "enforce")
    assert caps_mode() == "enforce"
    monkeypatch.setenv("TE_EXPOSURE_CAPS_MODE", "garbage")
    assert caps_mode() == "report"


# --- the caps ------------------------------------------------------------------------------------


def test_per_symbol_caps_use_each_pairs_own_sigma_without_diversification():
    inputs = _inputs()
    btc = symbol_cap("BTCUSDT", inputs, 0.03, 2.33)
    bch = symbol_cap("BCHUSDT", inputs, 0.03, 2.33)
    assert btc.source == bch.source == "derived"
    assert btc.ratio == pytest.approx(0.03 / (2.33 * 0.019))  # about 0.68x equity
    assert bch.ratio == pytest.approx(0.03 / (2.33 * 0.062))  # about 0.21x equity
    assert btc.ratio > 3 * bch.ratio  # a calm pair may hold far more than a wild one


def test_a_symbol_without_a_sufficient_sigma_falls_back_to_the_fixed_ratio_labelled():
    cap = symbol_cap("XRPUSDT", _inputs(), 0.03, 2.33)
    assert (cap.ratio, cap.source) == (0.15, "fallback")
    assert cap.reason == "no_sufficient_sigma_XRPUSDT"
    none = symbol_cap("BTCUSDT", None, 0.03, 2.33)
    assert none.source == "fallback" and none.reason == "risk_inputs_unavailable"


def test_basket_sigma_is_weighted_by_the_held_pairs_net_notional():
    inputs = _inputs({"A": 0.02, "B": 0.04}, rho=0.5)
    sigma, why = basket_sigma(
        {"A": 3000.0, "B": -1000.0}, inputs
    )  # a short counts by its size
    expected = (
        0.75**2 * 0.02**2 + 0.25**2 * 0.04**2 + 2 * 0.75 * 0.25 * 0.5 * 0.02 * 0.04
    ) ** 0.5
    assert sigma == pytest.approx(expected) and why is None


def test_the_net_cap_follows_the_held_basket():
    inputs = _inputs({"A": 0.027, "B": 0.027}, rho=1.0)
    cap = net_cap({"A": 500.0, "B": 500.0}, inputs, 0.03, 2.33)
    assert cap.source == "derived" and cap.sigma == pytest.approx(0.027)
    assert cap.ratio == pytest.approx(0.03 / (2.33 * 0.027))  # about 0.48x equity


def test_the_net_cap_falls_back_when_an_input_is_missing_with_the_reason():
    inputs = _inputs({"A": 0.02}, rho=0.5)
    held_without_sigma = net_cap({"A": 1.0, "Z": 1.0}, inputs, 0.03, 2.33)
    assert (held_without_sigma.ratio, held_without_sigma.source) == (0.6, "fallback")
    assert held_without_sigma.reason == "no_sufficient_sigma_Z"
    no_held = net_cap({}, inputs, 0.03, 2.33)
    assert no_held.source == "fallback" and no_held.reason == "no_held_pairs"
    missing_corr = RiskInputs(
        sigma_daily={"A": 0.02, "B": 0.03},
        correlation={"A": {"B": None}, "B": {"A": None}},
    )
    assert net_cap({"A": 1.0, "B": 1.0}, missing_corr, 0.03, 2.33).reason.startswith(
        "no_sufficient_correlation"
    )
    assert net_cap({"A": 1.0}, None, 0.03, 2.33).reason == "risk_inputs_unavailable"


def test_no_all_pairs_fallback_for_the_caps():
    # a pair that is not held does not enter the net cap (the all-pairs basket would be more diversified, so its
    # lower sigma would LOOSEN the cap)
    inputs = _inputs({"A": 0.05, "B": 0.01}, rho=0.0)
    held_only_a = net_cap({"A": 1000.0}, inputs, 0.03, 2.33)
    assert held_only_a.sigma == pytest.approx(0.05)


def test_parse_risk_inputs_keeps_only_sufficient_items():
    body = {
        "symbols": {
            "BTCUSDT": {"sigma_daily_best": {"value": 0.02, "sufficient": True}},
            "BCHUSDT": {"sigma_daily_best": {"value": None, "sufficient": False}},
        },
        "correlation": {
            "matrix": {"BTCUSDT": {"BTCUSDT": 1.0, "BCHUSDT": 0.5}},
            "sufficient": {"BTCUSDT": {"BTCUSDT": True, "BCHUSDT": False}},
        },
    }
    inputs = parse_risk_inputs(body)
    assert inputs.sigma_daily == {"BTCUSDT": 0.02}
    assert inputs.correlation["BTCUSDT"]["BCHUSDT"] is None


# --- the projection of an order ------------------------------------------------------------------


def _check(
    symbol="BTCUSDT",
    signed=0.0,
    equity=1000.0,
    net=None,
    gross=None,
    risk=0.0,
    new_risk=0.0,
    inputs="x",
):
    return check_projection(
        symbol=symbol,
        signed_notional=signed,
        equity=equity,
        net_by_symbol=net or {},
        gross_by_symbol=gross or {},
        stop_risk_usd=risk,
        new_stop_risk_usd=new_risk,
        inputs=_inputs() if inputs == "x" else inputs,
    )


def test_an_order_inside_every_cap_breaches_nothing():
    assert (
        _check(
            signed=300.0, net={"BTCUSDT": 100.0}, gross={"BTCUSDT": 100.0}, new_risk=5.0
        )
        == []
    )


def test_a_per_symbol_breach_is_named():
    # BCH cap is about 0.21x equity: 250 on 1000 is over it
    breaches = _check(symbol="BCHUSDT", signed=250.0)
    by_cap = {b.cap: b for b in breaches}
    assert (
        "symbol_exposure_cap" in by_cap
    )  # BCH alone is also the whole basket, so the net cap trips too
    assert "BCHUSDT projected 25.00%" in by_cap["symbol_exposure_cap"].detail


def test_a_net_breach_uses_the_projected_basket():
    # BTC alone: cap 0.68; holding 600 and adding 200 -> 80% net
    breaches = _check(signed=200.0, net={"BTCUSDT": 600.0}, gross={"BTCUSDT": 600.0})
    assert "net_exposure_cap" in [b.cap for b in breaches]


def test_long_and_short_net_out_in_the_net_cap_but_not_in_the_symbol_gross():
    breaches = _check(
        symbol="ETHUSDT",
        signed=-300.0,
        net={"BTCUSDT": 300.0},
        gross={"BTCUSDT": 300.0},
    )
    assert "net_exposure_cap" not in [b.cap for b in breaches]


def test_the_stop_risk_budget_counts_existing_and_new_stop_risk():
    # B x equity = 30; 25 already at risk, the new order adds 6
    breaches = _check(risk=25.0, new_risk=6.0)
    assert [b.cap for b in breaches] == ["stop_risk_budget"]
    assert _check(risk=25.0, new_risk=4.0) == []


def test_without_inputs_the_fixed_ratios_apply():
    # 0.15 per symbol, 0.6 net
    assert [b.cap for b in _check(signed=200.0, inputs=None)] == ["symbol_exposure_cap"]
    assert [b.cap for b in _check(symbol="ETHUSDT", signed=700.0, inputs=None)] == [
        "net_exposure_cap",
        "symbol_exposure_cap",
    ]


def test_no_equity_gives_no_projection():
    assert _check(signed=1e9, equity=0.0) == []


# --- the stop-risk of an aggregated leg ----------------------------------------------------------


def test_leg_stop_risk_is_notional_times_the_distance_to_the_stop():
    assert leg_stop_risk(2.0, 100.0, 98.0, "LONG", 0.06) == pytest.approx(
        2.0 * 100.0 * 0.02
    )
    assert leg_stop_risk(2.0, 100.0, 103.0, "SHORT", 0.06) == pytest.approx(
        2.0 * 100.0 * 0.03
    )


def test_a_missing_or_wrong_side_stop_uses_the_floor():
    assert leg_stop_risk(2.0, 100.0, None, "LONG", 0.06) == pytest.approx(
        2.0 * 100.0 * 0.06
    )
    assert leg_stop_risk(2.0, 100.0, 101.0, "LONG", 0.06) == pytest.approx(
        2.0 * 100.0 * 0.06
    )
    assert leg_stop_risk(0.0, 100.0, 98.0, "LONG", 0.06) == 0.0


def _manager(positions, records, equity=1000.0):
    pm = PositionManager.__new__(PositionManager)
    pm.get_positions = MagicMock(return_value=positions)
    pm.position_records = records
    pm.equity = equity
    pm.max_portfolio_exposure_pct = 0.8
    return pm


def test_stop_risk_aggregates_one_leg_per_symbol_side_with_the_weighted_stop():
    positions = {
        ("ETHUSDT", "LONG"): {
            "symbol": "ETHUSDT",
            "quantity": 3.0,
            "position_side": "LONG",
            "mark_price": 100.0,
        },
    }
    records = {  # three add-on rows of one leg: weighted stop (1 x 98 + 2 x 95) / 3 = 96
        "a": {
            "symbol": "ETHUSDT",
            "position_side": "LONG",
            "quantity": 1.0,
            "stop_loss": 98.0,
        },
        "b": {
            "symbol": "ETHUSDT",
            "position_side": "LONG",
            "quantity": 2.0,
            "stop_loss": 95.0,
        },
    }
    pm = _manager(positions, records)
    pm._stop_floor_fraction = lambda symbol: 0.06
    assert pm.stop_risk_usd() == pytest.approx(3.0 * (100.0 - 96.0))


def test_a_leg_without_a_stop_row_counts_at_the_floor():
    positions = {
        ("ETHUSDT", "SHORT"): {
            "symbol": "ETHUSDT",
            "quantity": 1.0,
            "position_side": "SHORT",
            "mark_price": 200.0,
        }
    }
    pm = _manager(positions, {})
    pm._stop_floor_fraction = lambda symbol: 0.05
    assert pm.stop_risk_usd() == pytest.approx(200.0 * 0.05)


# --- the order gate: report and enforce ----------------------------------------------------------


def _order(symbol="BCHUSDT", amount=1.0, price=250.0, side="buy", stop_loss=None):
    return SimpleNamespace(
        symbol=symbol,
        amount=amount,
        side=side,
        position_side=None,
        target_price=price,
        stop_loss=stop_loss,
        stop_loss_pct=None,
    )


def _gate(monkeypatch, mode):
    from tradeengine import position_manager as module

    cache = RiskInputsCache()
    cache.inputs = _inputs()
    cache._clock = lambda: 1.0
    monkeypatch.setattr(module, "risk_inputs_cache", cache)
    if mode:
        monkeypatch.setenv("TE_EXPOSURE_CAPS_MODE", mode)
    pm = _manager({}, {})
    pm.get_notional_by_symbol = lambda: {}
    pm.stop_risk_usd = lambda: 0.0
    pm._stop_floor_fraction = lambda symbol: 0.0
    return pm


def test_report_mode_logs_the_breach_and_lets_the_order_through(monkeypatch, caplog):
    pm = _gate(monkeypatch, None)
    with caplog.at_level("WARNING"):
        assert pm._check_exposure_caps(_order(), 250.0, 1000.0) is True
    assert any(
        "EXPOSURE_CAP symbol_exposure_cap (report)" in r.message for r in caplog.records
    )


def test_enforce_mode_rejects_the_breach_with_its_reason(monkeypatch):
    pm = _gate(monkeypatch, "enforce")
    assert pm._check_exposure_caps(_order(), 250.0, 1000.0) is False
    assert pm._cap_rejection in {"net_exposure_cap", "symbol_exposure_cap"}
    assert (
        pm._check_exposure_caps(_order(amount=0.4), 100.0, 1000.0) is True
    )  # inside the BCH cap


def test_off_mode_skips_the_check(monkeypatch):
    pm = _gate(monkeypatch, "off")
    assert pm._check_exposure_caps(_order(), 1e9, 1000.0) is True


def test_a_failing_check_never_blocks_an_order(monkeypatch):
    pm = _gate(monkeypatch, "enforce")
    pm.get_notional_by_symbol = MagicMock(side_effect=RuntimeError("no marks"))
    assert pm._check_exposure_caps(_order(), 250.0, 1000.0) is True


def test_the_new_orders_stop_distance_is_at_least_the_floor(monkeypatch):
    pm = _gate(monkeypatch, "enforce")
    pm._stop_floor_fraction = lambda symbol: 0.06
    # 100 USD notional x 6% floor = 6 USD of stop risk; the budget is 3% of 150 = 4.5
    assert (
        pm._check_exposure_caps(
            _order(symbol="BTCUSDT", amount=1.0, price=100.0, stop_loss=99.9),
            100.0,
            150.0,
        )
        is False
    )
    assert pm._cap_rejection == "stop_risk_budget"


# --- /state --------------------------------------------------------------------------------------


def test_the_snapshot_shows_each_cap_with_its_source_and_inputs():
    state = snapshot(
        equity=1000.0,
        net_by_symbol={"BTCUSDT": 400.0},
        gross_by_symbol={"BTCUSDT": 400.0},
        symbols=["BTCUSDT", "XRPUSDT"],
        stop_risk_usd=12.0,
        inputs=_inputs(),
        gross_ratio=0.8,
    )
    assert state["mode"] == "report"
    assert state["risk_budget"] == {"ratio": 0.03, "source": "operator"}
    assert state["net"]["source"] == "derived" and state["net"][
        "net_ratio"
    ] == pytest.approx(0.4)
    assert state["per_symbol"]["BTCUSDT"]["source"] == "derived"
    assert state["per_symbol"]["BTCUSDT"]["gross_ratio"] == pytest.approx(0.4)
    assert state["per_symbol"]["XRPUSDT"]["source"] == "fallback"
    assert state["gross"]["source"] == "fallback" and "684" in state["gross"]["reason"]
    assert state["stop_risk"]["budget_usd"] == pytest.approx(30.0)
    assert state["stop_risk"]["ratio_of_budget"] == pytest.approx(0.4)
    assert state["inputs"]["available"] is True


def test_the_snapshot_without_inputs_labels_every_cap_a_fallback():
    state = snapshot(
        equity=1000.0,
        net_by_symbol={},
        gross_by_symbol={},
        symbols=["BTCUSDT"],
        stop_risk_usd=0.0,
        inputs=None,
        gross_ratio=0.8,
    )
    assert (
        state["net"]["source"] == "fallback" and state["inputs"]["available"] is False
    )
    assert state["per_symbol"]["BTCUSDT"]["reason"] == "risk_inputs_unavailable"


def _dispatcher(caps):
    d = Dispatcher.__new__(Dispatcher)
    d.logger = MagicMock()
    d.position_manager = MagicMock()
    d.position_manager.get_cio_portfolio_summary.return_value = {}
    d.position_manager.get_daily_pnl.return_value = 0.0
    d.position_manager.total_portfolio_value = 1000.0
    d.position_manager.get_notional_summary.return_value = (0.0, 0.0)
    d.position_manager.get_notional_by_symbol.return_value = {}
    d.position_manager.exposure_caps_state = caps
    d.order_manager = MagicMock()
    d.order_manager.get_active_orders.return_value = []
    return d


def test_state_carries_the_exposure_caps_and_survives_their_failure():
    d = _dispatcher(MagicMock(return_value={"mode": "report"}))
    assert d.get_cio_state("BTCUSDT")["risk_limits"]["exposure_caps"] == {
        "mode": "report"
    }
    broken = _dispatcher(MagicMock(side_effect=RuntimeError("x")))
    assert broken.get_cio_state("BTCUSDT")["risk_limits"]["exposure_caps"] is None
