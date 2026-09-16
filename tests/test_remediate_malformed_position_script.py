"""Unit tests for the #586 malformed-position remediation script's pure
helpers.

The script (``scripts/remediate-malformed-position.py``) drives live
Binance API calls under ``--apply`` — so this file exercises only the pure
``is_malformed`` classifier and the async re-read/close helpers with a fake
client, not the live API boundary. Mirrors the existing precedent in
``tests/test_close_unhedged_positions_script.py`` (#424 AC6).
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from typing import Any

import pytest


def _load_script_module():
    script_path = (
        pathlib.Path(__file__).resolve().parent.parent
        / "scripts"
        / "remediate-malformed-position.py"
    )
    spec = importlib.util.spec_from_file_location(
        "remediate_malformed_position", script_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["remediate_malformed_position"] = module
    spec.loader.exec_module(module)
    return module


SCRIPT = _load_script_module()


# ---------------------------------------------------------------------------
# is_malformed
# ---------------------------------------------------------------------------


def test_is_malformed_long_with_negative_amt():
    # The exact #586 live evidence shape.
    assert SCRIPT.is_malformed(
        {"symbol": "LTCUSDT", "positionSide": "LONG", "positionAmt": "-0.303"}
    )


def test_is_malformed_short_with_positive_amt():
    assert SCRIPT.is_malformed(
        {"symbol": "XLMUSDT", "positionSide": "SHORT", "positionAmt": "5.0"}
    )


def test_not_malformed_long_with_positive_amt():
    assert not SCRIPT.is_malformed(
        {"symbol": "BTCUSDT", "positionSide": "LONG", "positionAmt": "0.5"}
    )


def test_not_malformed_short_with_negative_amt():
    assert not SCRIPT.is_malformed(
        {"symbol": "ETHUSDT", "positionSide": "SHORT", "positionAmt": "-1.2"}
    )


def test_not_malformed_zero_amt():
    assert not SCRIPT.is_malformed(
        {"symbol": "BNBUSDT", "positionSide": "LONG", "positionAmt": "0"}
    )


def test_not_malformed_one_way_mode_both():
    # One-way mode (positionSide=BOTH) has no declared side to conflict with.
    assert not SCRIPT.is_malformed(
        {"symbol": "ADAUSDT", "positionSide": "BOTH", "positionAmt": "-2.0"}
    )


def test_not_malformed_bad_amt_value():
    assert not SCRIPT.is_malformed(
        {"symbol": "SOLUSDT", "positionSide": "LONG", "positionAmt": "not-a-number"}
    )


# ---------------------------------------------------------------------------
# _fetch_malformed
# ---------------------------------------------------------------------------


class _FakeClient:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.create_order_calls: list[dict[str, Any]] = []

    async def get_position_info(self) -> list[dict[str, Any]]:
        return list(self._rows)

    async def create_order(self, **params: Any) -> dict[str, Any]:
        self.create_order_calls.append(params)
        return {"orderId": 1, "status": "FILLED", "executedQty": params["quantity"]}


@pytest.mark.asyncio
async def test_fetch_malformed_filters_to_sign_mismatch_only():
    client = _FakeClient(
        [
            {"symbol": "LTCUSDT", "positionSide": "LONG", "positionAmt": "-0.303"},
            {"symbol": "BTCUSDT", "positionSide": "LONG", "positionAmt": "0.5"},
        ]
    )
    rows = await SCRIPT._fetch_malformed(client, symbol=None)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "LTCUSDT"


@pytest.mark.asyncio
async def test_fetch_malformed_symbol_scope():
    client = _FakeClient(
        [
            {"symbol": "LTCUSDT", "positionSide": "LONG", "positionAmt": "-0.303"},
            {"symbol": "XLMUSDT", "positionSide": "SHORT", "positionAmt": "5.0"},
        ]
    )
    rows = await SCRIPT._fetch_malformed(client, symbol="XLMUSDT")
    assert len(rows) == 1
    assert rows[0]["symbol"] == "XLMUSDT"


# ---------------------------------------------------------------------------
# _reread_live_qty — never trust the earlier scan for the actual close (#586)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reread_live_qty_returns_fresh_abs_amount():
    client = _FakeClient(
        [{"symbol": "LTCUSDT", "positionSide": "LONG", "positionAmt": "-0.303"}]
    )
    qty = await SCRIPT._reread_live_qty(client, "LTCUSDT", "LONG")
    assert qty == pytest.approx(0.303)


@pytest.mark.asyncio
async def test_reread_live_qty_returns_none_when_already_resolved():
    # Position no longer malformed (e.g. an operator/other process already
    # flattened it between the scan and this call) -> must skip, not close.
    client = _FakeClient(
        [{"symbol": "LTCUSDT", "positionSide": "LONG", "positionAmt": "0"}]
    )
    qty = await SCRIPT._reread_live_qty(client, "LTCUSDT", "LONG")
    assert qty is None


@pytest.mark.asyncio
async def test_reread_live_qty_returns_none_when_position_gone():
    client = _FakeClient([])
    qty = await SCRIPT._reread_live_qty(client, "LTCUSDT", "LONG")
    assert qty is None


# ---------------------------------------------------------------------------
# _close_one — closing direction/params
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_one_long_sends_sell_no_reduce_only():
    client = _FakeClient([])
    result = await SCRIPT._close_one(client, "LTCUSDT", "LONG", 0.303)
    assert result["status"] == "FILLED"
    assert len(client.create_order_calls) == 1
    params = client.create_order_calls[0]
    assert params["side"] == "SELL"
    assert params["positionSide"] == "LONG"
    assert params["quantity"] == pytest.approx(0.303)
    assert "reduceOnly" not in params


@pytest.mark.asyncio
async def test_close_one_short_sends_buy():
    client = _FakeClient([])
    result = await SCRIPT._close_one(client, "XLMUSDT", "SHORT", 5.0)
    assert result["status"] == "FILLED"
    params = client.create_order_calls[0]
    assert params["side"] == "BUY"
    assert params["positionSide"] == "SHORT"


# ---------------------------------------------------------------------------
# main() opt-in gating — never place a live order without double opt-in
# ---------------------------------------------------------------------------


def test_main_apply_without_credentials_refuses(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("BINANCE_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_API_SECRET", raising=False)
    monkeypatch.setattr(sys, "argv", ["remediate-malformed-position.py", "--apply"])
    assert SCRIPT.main() == 2


def test_main_apply_without_second_optin_refuses(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BINANCE_API_KEY", "k")
    monkeypatch.setenv("BINANCE_API_SECRET", "s")
    monkeypatch.delenv("TE_REMEDIATE_MALFORMED_ACK", raising=False)
    monkeypatch.setattr(sys, "argv", ["remediate-malformed-position.py", "--apply"])
    assert SCRIPT.main() == 2
