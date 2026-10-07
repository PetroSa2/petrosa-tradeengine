"""Per-symbol net and gross notional on /state ``portfolio`` (petrosa-tradeengine#731, first part)."""

from unittest.mock import MagicMock

import pytest

from tradeengine.dispatcher import Dispatcher
from tradeengine.position_manager import PositionManager


def _manager(positions):
    pm = PositionManager.__new__(PositionManager)
    pm.get_positions = MagicMock(return_value=positions)
    return pm


POSITIONS = {
    ("BTCUSDT", "LONG"): {
        "symbol": "BTCUSDT",
        "quantity": 0.02,
        "position_side": "LONG",
        "mark_price": 50_000.0,
    },
    ("BTCUSDT", "SHORT"): {
        "symbol": "BTCUSDT",
        "quantity": 0.005,
        "position_side": "SHORT",
        "mark_price": 50_000.0,
    },
    ("ETHUSDT", "SHORT"): {
        "symbol": "ETHUSDT",
        "quantity": 1.0,
        "position_side": "SHORT",
        "avg_price": 3_000.0,
    },
    ("XRPUSDT", "LONG"): {
        "symbol": "XRPUSDT",
        "quantity": 0.0,
        "position_side": "LONG",
        "mark_price": 2.0,
    },
}


def test_notional_by_symbol_is_signed_net_and_gross_with_both_hedge_legs():
    out = _manager(POSITIONS).get_notional_by_symbol()
    assert set(out) == {"BTCUSDT", "ETHUSDT"}  # a flat position is not held
    btc = out["BTCUSDT"]
    assert btc["long"] == pytest.approx(1000.0) and btc["short"] == pytest.approx(250.0)
    assert btc["net"] == pytest.approx(750.0)  # the net of the two legs
    assert btc["gross"] == pytest.approx(1250.0)
    eth = out["ETHUSDT"]  # mark price absent: the average price
    assert eth["net"] == pytest.approx(-3000.0) and eth["gross"] == pytest.approx(
        3000.0
    )


def test_per_symbol_notionals_add_up_to_the_portfolio_totals():
    pm = _manager(POSITIONS)
    gross, net = PositionManager.get_notional_summary(pm)
    by_symbol = pm.get_notional_by_symbol()
    assert sum(v["gross"] for v in by_symbol.values()) == pytest.approx(gross)
    assert sum(v["net"] for v in by_symbol.values()) == pytest.approx(net)


def test_the_symbol_comes_from_the_key_when_the_position_has_none():
    out = _manager(
        {
            ("SOLUSDT", "LONG"): {
                "quantity": 2.0,
                "position_side": "LONG",
                "mark_price": 100.0,
            }
        }
    ).get_notional_by_symbol()
    assert out == {
        "SOLUSDT": {"gross": 200.0, "net": 200.0, "long": 200.0, "short": 0.0}
    }


def _dispatcher(by_symbol):
    d = Dispatcher.__new__(Dispatcher)
    d.logger = MagicMock()
    d.position_manager = MagicMock()
    d.position_manager.get_cio_portfolio_summary.return_value = {
        "gross_exposure": 0.2,
        "same_asset_pct": 0.1,
        "open_positions_count": 3,
    }
    d.position_manager.get_daily_pnl.return_value = 0.0
    d.position_manager.total_portfolio_value = 10_000.0
    d.position_manager.get_notional_summary.return_value = (4250.0, -2250.0)
    d.position_manager.get_notional_by_symbol.return_value = by_symbol
    d.order_manager = MagicMock()
    d.order_manager.get_active_orders.return_value = []
    return d


def test_state_portfolio_carries_the_held_pairs():
    state = _dispatcher(_manager(POSITIONS).get_notional_by_symbol()).get_cio_state(
        "BTCUSDT"
    )
    portfolio = state["portfolio"]
    assert portfolio["net_notional_by_symbol"] == {
        "BTCUSDT": pytest.approx(750.0),
        "ETHUSDT": pytest.approx(-3000.0),
    }
    assert portfolio["gross_notional_by_symbol"]["BTCUSDT"] == pytest.approx(1250.0)
    # the existing portfolio fields are untouched
    assert portfolio["gross_exposure"] == 0.2 and portfolio["open_positions_count"] == 3


def test_state_never_fails_when_the_per_symbol_notional_cannot_be_read():
    d = _dispatcher({})
    d.position_manager.get_notional_by_symbol.side_effect = RuntimeError("no marks")
    state = d.get_cio_state("BTCUSDT")
    assert "net_notional_by_symbol" not in state["portfolio"]
    assert state["portfolio"]["open_positions_count"] == 3


def test_no_open_positions_gives_empty_maps():
    portfolio = _dispatcher({}).get_cio_state("BTCUSDT")["portfolio"]
    assert (
        portfolio["net_notional_by_symbol"] == {}
        and portfolio["gross_notional_by_symbol"] == {}
    )
