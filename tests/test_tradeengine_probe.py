from __future__ import annotations

import ast
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

from tradeengine_probe import __main__ as probe_main
from tradeengine_probe._client import (
    ProbeBinanceClient,
    ProbeForbidden,
    _allowed,
    _WhitelistAdapter,
)
from tradeengine_probe.checks import Result, run_cycle
from tradeengine_probe.params import build_order_params

ROOT = Path(__file__).parents[1]


class FakeProbeClient:
    def __init__(self):
        self.order_params = None

    def futures_time(self):
        return {"serverTime": int(time.time() * 1000)}

    def futures_account(self):
        return {"canTrade": True}

    def futures_get_position_mode(self):
        return {"dualSidePosition": True}

    def futures_exchange_info(self):
        return {
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "filters": [
                        {
                            "filterType": "LOT_SIZE",
                            "minQty": "0.001",
                            "stepSize": "0.001",
                        },
                        {"filterType": "MIN_NOTIONAL", "notional": "5"},
                        {
                            "filterType": "PRICE_FILTER",
                            "minPrice": "0.01",
                            "maxPrice": "1000000",
                            "tickSize": "0.01",
                        },
                    ],
                }
            ]
        }

    def futures_symbol_ticker(self, *, symbol):
        assert symbol == "BTCUSDT"
        return {"price": "100"}

    def futures_create_test_order(self, **kwargs):
        self.order_params = kwargs
        return {}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/fapi/v1/time"),
        ("GET", "/fapi/v1/exchangeInfo"),
        ("GET", "/fapi/v2/account"),
        ("POST", "/fapi/v1/order/test"),
    ],
)
def test_allowlist_accepts_exact_testnet_triples(method, path):
    assert _allowed(method, f"https://testnet.binancefuture.com{path}", testnet=True)


@pytest.mark.unit
@pytest.mark.parametrize(
    "url",
    [
        "https://testnet.binancefuture.com/fapi/v1/order",
        "https://testnet.binancefuture.com/fapi/v1/order/test/extra",
        "https://fapi.binance.com/fapi/v1/order/test",
        "https://user@testnet.binancefuture.com/fapi/v1/time",
        "https://testnet.binancefuture.com:444/fapi/v1/time",
        "http://testnet.binancefuture.com/fapi/v1/time",
        "https://testnet.binancefuture.com/fapi/v1/order/test?x=1",
    ],
)
def test_allowlist_rejects_unsafe_triples(url):
    assert not _allowed("POST" if "order/test" in url else "GET", url, testnet=True)


@pytest.mark.unit
def test_adapter_rejects_direct_session_request_without_sending(monkeypatch):
    adapter = _WhitelistAdapter(testnet=True)
    request = requests.Request(
        "POST", "https://testnet.binancefuture.com/fapi/v1/order"
    ).prepare()
    monkeypatch.setattr(adapter, "send", adapter.send)
    with pytest.raises(ProbeForbidden) as exc_info:
        adapter.send(request)
    assert "request refused" in str(exc_info.value)


@pytest.mark.unit
def test_constructor_does_not_ping_and_disables_environment(monkeypatch):
    monkeypatch.setattr(
        requests.Session, "request", lambda *args, **kwargs: pytest.fail("network")
    )
    client = ProbeBinanceClient(api_key="key", api_secret="secret")
    assert client.session.trust_env is False
    assert client.session.max_redirects == 0
    assert set(client.session.adapters) == {"https://", "http://"}


@pytest.mark.unit
def test_websocket_names_are_fail_closed():
    client = ProbeBinanceClient(api_key="key", api_secret="secret")
    names = [name for name in dir(client) if name.startswith(("ws_", "_ws_"))]
    assert names
    for name in names:
        if name in {"ws_api", "ws_future"}:
            continue
        with pytest.raises(ProbeForbidden):
            getattr(client, name)()


@pytest.mark.unit
def test_probe_parameters_preserve_one_way_and_hedge_exclusivity():
    one_way = build_order_params(
        symbol="BTCUSDT",
        side="BUY",
        order_type="MARKET",
        price=100,
        step="0.001",
        min_qty="0.001",
        min_notional="5",
        position_side="BOTH",
        reduce_only=True,
    )
    hedge = build_order_params(
        symbol="BTCUSDT",
        side="BUY",
        order_type="MARKET",
        price=100,
        step="0.001",
        min_qty="0.001",
        min_notional="5",
        position_side="LONG",
        reduce_only=True,
    )
    assert one_way["reduceOnly"] is True
    assert "positionSide" not in one_way
    assert hedge["positionSide"] == "LONG"
    assert "reduceOnly" not in hedge


@pytest.mark.unit
def test_startup_refuses_without_testnet(monkeypatch):
    env = os.environ.copy()
    env.pop("BINANCE_TESTNET", None)
    env["BINANCE_API_KEY"] = "key"
    env["BINANCE_API_SECRET"] = "secret"
    result = subprocess.run(
        [sys.executable, "-m", "tradeengine_probe"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0


@pytest.mark.unit
def test_probe_source_has_no_forbidden_engine_imports():
    forbidden = {"tradeengine.api", "tradeengine.dispatcher", "tradeengine.exchange"}
    for path in (ROOT / "tradeengine_probe").glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(alias.name not in forbidden for alias in node.names)
            if isinstance(node, ast.ImportFrom):
                assert node.module not in forbidden


@pytest.mark.unit
def test_cycle_runs_all_checks_and_derives_order_quantity_from_filters():
    client = FakeProbeClient()

    outcomes = run_cycle(client, symbol="BTCUSDT")

    assert outcomes == {
        "clock_skew": Result.SUCCESS,
        "latency": Result.SUCCESS,
        "auth": Result.SUCCESS,
        "hedge_mode": Result.SUCCESS,
        "filters": Result.SUCCESS,
        "order_test": Result.SUCCESS,
    }
    assert client.order_params == {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "type": "MARKET",
        "quantity": "0.052",
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    "method",
    [
        "futures_time",
        "futures_account",
        "futures_get_position_mode",
        "futures_exchange_info",
        "futures_create_test_order",
    ],
)
def test_cycle_classifies_each_check_failure_and_continues(method):
    client = FakeProbeClient()

    def fail(*args, **kwargs):
        raise TimeoutError(method)

    setattr(client, method, fail)

    outcomes = run_cycle(client, symbol="BTCUSDT")

    assert Result.TIMEOUT in outcomes.values()
    assert set(outcomes) == {
        "clock_skew",
        "latency",
        "auth",
        "hedge_mode",
        "filters",
        "order_test",
    }


@pytest.mark.unit
def test_one_shot_returns_nonzero_when_any_check_fails(monkeypatch):
    client = FakeProbeClient()
    client.futures_account = lambda: {"canTrade": False}
    monkeypatch.setattr(probe_main, "ProbeBinanceClient", lambda **kwargs: client)
    monkeypatch.setenv("BINANCE_TESTNET", "true")
    monkeypatch.setenv("BINANCE_API_KEY", "key")
    monkeypatch.setenv("BINANCE_API_SECRET", "secret")
    monkeypatch.setenv("TE_SYNTHETIC_PROBE_ONESHOT", "true")

    assert probe_main.main() != 0
