"""Unit tests for the AC6 remediation script's pure helpers.

The script (``scripts/close_unhedged_positions.py``) drives live Binance
API calls under ``--commit`` — so this file exercises only the pure
helpers that decide WHICH positions get touched, not the live API
boundary.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import time
from typing import Any

import pytest


def _load_script_module():
    script_path = (
        pathlib.Path(__file__).resolve().parent.parent
        / "scripts"
        / "close_unhedged_positions.py"
    )
    spec = importlib.util.spec_from_file_location(
        "close_unhedged_positions", script_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["close_unhedged_positions"] = module
    spec.loader.exec_module(module)
    return module


SCRIPT = _load_script_module()


# ---------------------------------------------------------------------------
# _normalise_side
# ---------------------------------------------------------------------------


def test_normalise_side_hedge_long():
    assert (
        SCRIPT._normalise_side({"positionSide": "LONG", "positionAmt": "1.0"}) == "LONG"
    )


def test_normalise_side_one_way_positive():
    assert (
        SCRIPT._normalise_side({"positionSide": "BOTH", "positionAmt": "0.5"}) == "LONG"
    )


def test_normalise_side_one_way_negative():
    assert (
        SCRIPT._normalise_side({"positionSide": "BOTH", "positionAmt": "-0.5"})
        == "SHORT"
    )


# ---------------------------------------------------------------------------
# _position_is_unhedged
# ---------------------------------------------------------------------------


def _pos(symbol="BTCUSDT", side="LONG", amt=1.0) -> dict[str, Any]:
    return {"symbol": symbol, "positionSide": side, "positionAmt": str(amt)}


def test_position_is_hedged_when_both_sl_and_tp_present():
    pos = _pos()
    orders = [
        {"positionSide": "LONG", "type": "STOP_MARKET", "reduceOnly": True},
        {"positionSide": "LONG", "type": "TAKE_PROFIT_MARKET", "reduceOnly": True},
    ]
    unhedged, sl, tp = SCRIPT._position_is_unhedged(pos, orders)
    assert unhedged is False
    assert sl is True
    assert tp is True


def test_position_is_unhedged_when_no_orders():
    pos = _pos()
    unhedged, sl, tp = SCRIPT._position_is_unhedged(pos, [])
    assert unhedged is True
    assert sl is False
    assert tp is False


def test_position_is_unhedged_when_only_sl_present():
    pos = _pos()
    orders = [{"positionSide": "LONG", "type": "STOP_MARKET", "reduceOnly": True}]
    unhedged, sl, tp = SCRIPT._position_is_unhedged(pos, orders)
    assert unhedged is True
    assert sl is True
    assert tp is False


def test_position_is_unhedged_when_orders_on_wrong_side():
    pos = _pos(side="LONG")
    orders = [
        {"positionSide": "SHORT", "type": "STOP_MARKET", "reduceOnly": True},
        {"positionSide": "SHORT", "type": "TAKE_PROFIT_MARKET", "reduceOnly": True},
    ]
    unhedged, sl, tp = SCRIPT._position_is_unhedged(pos, orders)
    assert unhedged is True
    assert sl is False
    assert tp is False


def test_position_is_hedged_with_both_positionside_in_one_way_mode():
    pos = _pos()
    orders = [
        {"positionSide": "BOTH", "type": "STOP_MARKET", "reduceOnly": True},
        {"positionSide": "BOTH", "type": "TAKE_PROFIT_MARKET", "reduceOnly": True},
    ]
    unhedged, sl, tp = SCRIPT._position_is_unhedged(pos, orders)
    assert unhedged is False


def test_position_ignores_non_reduce_only_orders():
    pos = _pos()
    orders = [
        {"positionSide": "LONG", "type": "STOP_MARKET", "reduceOnly": False},
        {"positionSide": "LONG", "type": "TAKE_PROFIT_MARKET", "reduceOnly": False},
    ]
    unhedged, sl, tp = SCRIPT._position_is_unhedged(pos, orders)
    assert unhedged is True


def test_position_accepts_closeposition_as_reduce_only():
    pos = _pos()
    orders = [
        {"positionSide": "LONG", "type": "STOP_MARKET", "closePosition": True},
        {"positionSide": "LONG", "type": "TAKE_PROFIT_MARKET", "closePosition": True},
    ]
    unhedged, sl, tp = SCRIPT._position_is_unhedged(pos, orders)
    assert unhedged is False


# ---------------------------------------------------------------------------
# _filter_age
# ---------------------------------------------------------------------------


def test_filter_age_zero_keeps_all():
    positions = [_pos(), _pos(symbol="ETHUSDT")]
    assert SCRIPT._filter_age(positions, min_age_seconds=0) == positions


def test_filter_age_keeps_only_old_positions():
    now_ms = time.time() * 1000.0
    old_pos = {**_pos(symbol="OLD"), "updateTime": now_ms - 3700 * 1000}
    young_pos = {**_pos(symbol="YOUNG"), "updateTime": now_ms - 60 * 1000}
    out = SCRIPT._filter_age([old_pos, young_pos], min_age_seconds=3600)
    assert [p["symbol"] for p in out] == ["OLD"]


def test_filter_age_keeps_positions_without_updatetime():
    # Conservative: rows missing updateTime are kept so we don't silently
    # skip suspicious data — operator can re-filter externally if needed.
    pos = _pos()
    out = SCRIPT._filter_age([pos], min_age_seconds=3600)
    assert out == [pos]


# ---------------------------------------------------------------------------
# CLI argparse
# ---------------------------------------------------------------------------


def test_build_parser_dry_run_default():
    args = SCRIPT._build_parser().parse_args([])
    assert args.commit is False
    assert args.min_age_seconds == 0
    assert args.symbol is None


def test_build_parser_accepts_filters():
    args = SCRIPT._build_parser().parse_args(
        ["--commit", "--symbol", "ETHUSDT", "--min-age-seconds", "3600"]
    )
    assert args.commit is True
    assert args.symbol == "ETHUSDT"
    assert args.min_age_seconds == 3600


def test_main_requires_credentials_for_commit(monkeypatch):
    monkeypatch.delenv("BINANCE_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_API_SECRET", raising=False)
    monkeypatch.setattr(sys, "argv", ["close_unhedged_positions.py", "--commit"])
    rc = SCRIPT.main()
    assert rc == 2  # exits with 2 when credentials missing
