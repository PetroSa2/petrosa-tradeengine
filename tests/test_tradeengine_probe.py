from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest
import requests

from tradeengine_probe._client import (
    ProbeBinanceClient,
    ProbeForbidden,
    _allowed,
    _WhitelistAdapter,
)
from tradeengine_probe.params import build_order_params

ROOT = Path(__file__).parents[1]


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
    with pytest.raises(ProbeForbidden):
        adapter.send(request)


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
