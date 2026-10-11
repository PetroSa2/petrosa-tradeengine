from __future__ import annotations

import ast
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from prometheus_client import REGISTRY

from tradeengine_probe import (
    __main__ as probe_main,
    metrics,
)
from tradeengine_probe._client import (
    ProbeBinanceClient,
    ProbeForbidden,
    _allowed,
    _WhitelistAdapter,
)
from tradeengine_probe._facade import ProbeFacade
from tradeengine_probe.checks import (
    ProbeState,
    Result,
    classify_error,
    retry_after_seconds,
    run_cycle,
)
from tradeengine_probe.params import build_order_params

ROOT = Path(__file__).parents[1]
CHECKS = (
    "filters_changed",  # exchangeInfo first: the real limits are known before any weight is judged
    "time_sync",
    "signed_read",
    "account",
    "order_test_market",
    "order_test_limit",
    "order_test_negative_control",
)
FILTERS = [
    {"filterType": "LOT_SIZE", "minQty": "0.001", "stepSize": "0.001"},
    {"filterType": "MARKET_LOT_SIZE", "minQty": "0.001", "stepSize": "0.001"},
    {"filterType": "MIN_NOTIONAL", "notional": "5"},
    {
        "filterType": "PRICE_FILTER",
        "minPrice": "0.01",
        "maxPrice": "1000000",
        "tickSize": "0.01",
    },
    {"filterType": "PERCENT_PRICE", "multiplierUp": "1.05", "multiplierDown": "0.95"},
]


class ExchangeError(Exception):
    """A look-alike of BinanceAPIException (``code``, ``status_code``, ``response``). Other test modules in this
    suite replace ``binance.exceptions`` in ``sys.modules``, so the tests do not import the real class by name;
    the real one is loaded from its file in test_the_real_binance_exception_is_classified."""

    def __init__(self, response, status_code, text):
        super().__init__(text)
        self.response = response
        self.status_code = status_code
        self.code = json.loads(text)["code"]


def api_error(code: int, status: int = 400, headers: dict | None = None):
    response = SimpleNamespace(
        headers=headers or {},
        status_code=status,
        text=json.dumps({"code": code, "msg": "x"}),
    )
    return ExchangeError(response, status, response.text)


class FakeProbeClient:
    """What the exchange answers; the checks only ever see it through ProbeFacade."""

    def __init__(
        self,
        *,
        hedge=False,
        filters=None,
        skew_ms=0,
        symbol="BTCUSDT",
        price="100",
        rate_limits=None,
    ):
        self.hedge = hedge
        self.skew_ms = skew_ms
        self.symbol = symbol
        self.price = price
        self.rate_limits = rate_limits
        self.filters = filters if filters is not None else FILTERS
        self.test_orders: list[dict] = []
        self.exchange_info_calls = 0
        self.response = SimpleNamespace(headers={})
        self.REQUEST_RECVWINDOW = 10000

    def futures_time(self):
        return {"serverTime": int(time.time() * 1000) + self.skew_ms}

    def futures_account(self):
        return {"canTrade": True}

    def futures_get_position_mode(self):
        return {"dualSidePosition": self.hedge}

    def futures_exchange_info(self):
        self.exchange_info_calls += 1
        info = {"symbols": [{"symbol": self.symbol, "filters": self.filters}]}
        if self.rate_limits is not None:
            info["rateLimits"] = self.rate_limits
        return info

    def futures_symbol_ticker(self, *, symbol):
        assert symbol == self.symbol
        return {"price": self.price}

    def futures_mark_price(self, *, symbol):
        return {"markPrice": self.price}

    def futures_create_test_order(self, **kwargs):
        self.test_orders.append(kwargs)
        if float(kwargs["quantity"]) < 0.001:
            raise api_error(-1013)
        return {}

    def futures_create_order(self, **kwargs):  # never reachable through the facade
        pytest.fail("a real order was placed")


def filters_of(*, step, tick, min_qty=None, notional="5", up="1.05", down="0.95"):
    return [
        {"filterType": "LOT_SIZE", "minQty": min_qty or step, "stepSize": step},
        {"filterType": "MARKET_LOT_SIZE", "minQty": min_qty or step, "stepSize": step},
        {"filterType": "MIN_NOTIONAL", "notional": notional},
        {
            "filterType": "PRICE_FILTER",
            "minPrice": tick,
            "maxPrice": "1000000",
            "tickSize": tick,
        },
        {"filterType": "PERCENT_PRICE", "multiplierUp": up, "multiplierDown": down},
    ]


def cycle_of(client, **kwargs):
    return run_cycle(ProbeFacade(client), symbol=client.symbol, **kwargs)


def sample(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def runs(check: str, result: str) -> float:
    return sample("tradeengine_synthetic_probe_runs_total", check=check, result=result)


# ---------------------------------------------------------------- transport (the safety tests)


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
def test_adapter_rejects_direct_session_request_without_sending():
    adapter = _WhitelistAdapter(testnet=True)
    request = requests.Request(
        "POST", "https://testnet.binancefuture.com/fapi/v1/order"
    ).prepare()
    with pytest.raises(ProbeForbidden) as exc_info:
        adapter.send(request)
    assert "request refused" in str(exc_info.value)


@pytest.mark.unit
def test_request_refuses_every_call_outside_the_allowlist_before_sending(monkeypatch):
    monkeypatch.setattr(
        requests.Session, "request", lambda *a, **k: pytest.fail("network")
    )
    client = ProbeBinanceClient(api_key="key", api_secret="secret")
    for method, uri in (
        ("post", "https://testnet.binancefuture.com/fapi/v1/order"),
        ("delete", "https://testnet.binancefuture.com/fapi/v1/order"),
        ("post", "https://testnet.binancefuture.com/fapi/v1/leverage"),
        ("post", "https://testnet.binancefuture.com/fapi/v1/algoOrder"),
        ("get", "https://fapi.binance.com/fapi/v1/time"),
    ):
        with pytest.raises(ProbeForbidden):
            client._request(method, uri, False)
    with pytest.raises(ProbeForbidden):
        client.futures_create_order(
            symbol="BTCUSDT", side="BUY", type="MARKET", quantity="0.001"
        )


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
    for stub in (client.ws_api, client.ws_future):
        with pytest.raises(ProbeForbidden):
            stub.anything  # noqa: B018
    with pytest.raises(ProbeForbidden):
        client.ws_future.futures_account_balance()


@pytest.mark.unit
def test_a_live_client_is_refused():
    with pytest.raises(ProbeForbidden) as refusal:
        ProbeBinanceClient(api_key="key", api_secret="secret", testnet=False)
    assert "testnet-only" in str(refusal.value)


@pytest.mark.unit
def test_the_facade_exposes_only_the_allowed_methods():
    facade = ProbeFacade(FakeProbeClient())
    public = {name for name in dir(facade) if not name.startswith("_")}
    assert public == {
        "futures_time",
        "futures_exchange_info",
        "futures_get_position_mode",
        "futures_account",
        "futures_symbol_ticker",
        "futures_mark_price",
        "futures_klines",
        "futures_create_test_order",
        "recv_window_seconds",
        "response_headers",
    }
    with pytest.raises(AttributeError):
        facade.futures_create_order  # noqa: B018


# ---------------------------------------------------------------- startup guards (subprocess)


def run_probe(env_overrides: dict[str, str | None]):
    env = os.environ.copy()
    env.update(
        {
            "BINANCE_API_KEY": "key",
            "BINANCE_API_SECRET": "secret",
            "TE_SYNTHETIC_PROBE_ONESHOT": "true",
        }
    )
    for name, value in env_overrides.items():
        if value is None:
            env.pop(name, None)
        else:
            env[name] = value
    return subprocess.run(
        [sys.executable, "-m", "tradeengine_probe"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


@pytest.mark.unit
@pytest.mark.parametrize("value", [None, "false", "", "0"])
def test_startup_refuses_unless_testnet_is_true(value):
    result = run_probe({"BINANCE_TESTNET": value})
    assert result.returncode == 2
    assert "BINANCE_TESTNET=true is required" in result.stderr


@pytest.mark.unit
def test_startup_refuses_without_credentials():
    result = run_probe({"BINANCE_TESTNET": "true", "BINANCE_API_KEY": None})
    assert result.returncode == 2
    assert "BINANCE_API_KEY and BINANCE_API_SECRET are required" in result.stderr


@pytest.mark.unit
@pytest.mark.parametrize("interval", ["59", "10", "0", "-5"])
def test_startup_refuses_an_interval_below_the_minimum(interval):
    result = run_probe(
        {"BINANCE_TESTNET": "true", "TE_SYNTHETIC_PROBE_INTERVAL_SECONDS": interval}
    )
    assert result.returncode == 2
    assert "must be at least 60" in result.stderr


@pytest.mark.unit
def test_startup_refuses_settings_that_are_not_numbers():
    result = run_probe(
        {"BINANCE_TESTNET": "true", "TE_SYNTHETIC_PROBE_INTERVAL_SECONDS": "soon"}
    )
    assert result.returncode == 2
    assert "must be numbers" in result.stderr


# ---------------------------------------------------------------- the import lint


ALLOWED_TRADEENGINE = "tradeengine.minimum_order"
ALLOWED_BINANCE = {"binance.client", "binance.exceptions"}


def forbidden_imports(source: str) -> list[str]:
    """Any reference to ``tradeengine`` other than ``tradeengine.minimum_order``, to a ``binance`` module other
    than the sync client and its exceptions, or a dynamic import of either."""
    problems: list[str] = []

    def judge(module: str, node: ast.AST) -> None:
        if module == "tradeengine" or module.startswith("tradeengine."):
            if module != ALLOWED_TRADEENGINE:
                problems.append(f"{module}@{node.lineno}")
        if module == "binance" or module.startswith("binance."):
            if module not in ALLOWED_BINANCE:
                problems.append(f"{module}@{node.lineno}")

    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                judge(alias.name, node)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in {"tradeengine", "binance"}:
                for (
                    alias
                ) in node.names:  # from tradeengine import api -> tradeengine.api
                    judge(f"{module}.{alias.name}", node)
            else:
                judge(module, node)
        elif isinstance(node, ast.Call):
            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
            if name in {"import_module", "__import__"} and node.args:
                target = node.args[0]
                if isinstance(target, ast.Constant) and isinstance(target.value, str):
                    judge(target.value, node)
                else:
                    problems.append(f"dynamic import@{node.lineno}")
    return problems


@pytest.mark.unit
def test_probe_source_imports_only_what_it_may():
    for path in (ROOT / "tradeengine_probe").glob("*.py"):
        assert forbidden_imports(path.read_text()) == [], path.name


@pytest.mark.unit
@pytest.mark.parametrize(
    "source",
    [
        "import tradeengine",
        "import tradeengine.api",
        "from tradeengine import dispatcher",
        "from tradeengine.exchange import x",
        "from tradeengine.minimum_order_extra import x",
        "import tradeengine.dispatcher as d",
        "import importlib\nimportlib.import_module('tradeengine.api')",
        "__import__('tradeengine.exchange')",
        "import importlib\nimportlib.import_module(name)",
        "from binance import AsyncClient",
        "from binance.ws.streams import X",
        "import binance.websockets",
    ],
)
def test_the_import_lint_rejects(source):
    assert forbidden_imports(source)


@pytest.mark.unit
@pytest.mark.parametrize(
    "source",
    [
        "from tradeengine.minimum_order import minimum_quantity",
        "import tradeengine.minimum_order",
        "from binance.client import Client",
        "from binance.exceptions import BinanceAPIException",
        "import requests",
    ],
)
def test_the_import_lint_accepts(source):
    assert forbidden_imports(source) == []


# ---------------------------------------------------------------- parameters


@pytest.mark.unit
def test_probe_parameters_preserve_one_way_and_hedge_exclusivity():
    common = {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "order_type": "MARKET",
        "price": 100,
        "step": "0.001",
        "min_qty": "0.001",
        "min_notional": "5",
        "reduce_only": True,
    }
    one_way = build_order_params(position_side="BOTH", **common)
    hedge = build_order_params(position_side="LONG", **common)
    assert one_way["reduceOnly"] is True
    assert "positionSide" not in one_way
    assert hedge["positionSide"] == "LONG"
    assert "reduceOnly" not in hedge


# ---------------------------------------------------------------- the cycle, through ProbeFacade


@pytest.mark.unit
def test_a_cycle_runs_every_check_with_the_names_of_the_alert_rules():
    client = FakeProbeClient()
    before = {c: runs(c, "success") for c in CHECKS}

    outcomes = cycle_of(client)

    assert set(outcomes) == set(CHECKS)
    assert all(result is Result.SUCCESS for result in outcomes.values())
    for check in CHECKS:
        assert runs(check, "success") == before[check] + 1
    market, limit, negative = client.test_orders
    assert market["type"] == "MARKET" and limit["type"] == "LIMIT"
    assert limit["timeInForce"] == "GTC"
    assert (
        float(limit["price"]) < 100
    )  # below the mark, inside the percent-price band, never marketable
    assert float(limit["price"]) >= 95
    assert float(negative["quantity"]) < 0.001
    for order in client.test_orders:
        assert order["newClientOrderId"].startswith("ptest-")
        assert "reduceOnly" not in order and "positionSide" not in order  # one-way


@pytest.mark.unit
def test_the_metric_names_match_the_spec_and_have_no_placeholder_series():
    assert metrics.CHECKS == (
        "time_sync",
        "signed_read",
        "account",
        "order_test_market",
        "order_test_limit",
        "order_test_negative_control",
        "filters_changed",
    )
    for placeholder in (
        "clock_skew",
        "latency",
        "auth",
        "hedge_mode",
        "filters",
        "order_test",
    ):
        assert placeholder not in metrics.CHECKS


@pytest.mark.unit
def test_clock_skew_is_stored_in_seconds():
    cycle_of(FakeProbeClient(skew_ms=2000))
    skew = sample("tradeengine_synthetic_probe_clock_skew_seconds")
    assert 1.9 < skew < 2.1  # not 2000


@pytest.mark.unit
def test_hedge_mode_sends_the_position_side_and_never_reduce_only():
    client = FakeProbeClient(hedge=True)
    outcomes = cycle_of(client)
    assert all(result is Result.SUCCESS for result in outcomes.values())
    assert len(client.test_orders) == 3
    for order in client.test_orders:
        assert order["positionSide"] == "LONG"
        assert "reduceOnly" not in order
    assert sample("tradeengine_synthetic_probe_hedge_mode") == 1


@pytest.mark.unit
def test_a_position_side_mismatch_is_an_exchange_reject():
    client = FakeProbeClient()

    def mismatch(**kwargs):
        raise api_error(-4061)

    client.futures_create_test_order = mismatch
    outcomes = cycle_of(client)
    assert outcomes["order_test_market"] is Result.EXCHANGE_REJECT
    assert outcomes["order_test_limit"] is Result.EXCHANGE_REJECT


@pytest.mark.unit
def test_a_whitelist_refusal_is_blocked_not_a_transport_error():
    client = FakeProbeClient()

    def refused():
        raise ProbeForbidden("request refused: post https://x/fapi/v1/order")

    client.futures_account = refused
    before = runs("account", "blocked")
    outcomes = cycle_of(client)
    assert outcomes["account"] is Result.BLOCKED
    assert runs("account", "blocked") == before + 1


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
def test_a_check_failure_is_classified_and_the_cycle_continues(method):
    client = FakeProbeClient()

    def fail(*args, **kwargs):
        raise requests.exceptions.ReadTimeout(method)

    setattr(client, method, fail)
    outcomes = cycle_of(client)
    assert Result.TIMEOUT in outcomes.values()
    assert set(outcomes) <= set(CHECKS)
    assert len(outcomes) >= 4  # the other checks still ran or were counted


@pytest.mark.unit
def test_the_negative_control_needs_a_rejection():
    accepted = FakeProbeClient()
    accepted.futures_create_test_order = lambda **kwargs: {}
    assert cycle_of(accepted)["order_test_negative_control"] is Result.UNEXPECTED

    rejected = FakeProbeClient()
    assert cycle_of(rejected)["order_test_negative_control"] is Result.SUCCESS

    other = FakeProbeClient()

    def margin(**kwargs):
        if float(kwargs["quantity"]) < 0.001:
            raise api_error(-2019)

    other.futures_create_test_order = margin
    assert cycle_of(other)["order_test_negative_control"] is Result.EXCHANGE_REJECT


@pytest.mark.unit
def test_filters_are_fetched_once_per_ttl_and_a_change_is_flagged():
    client = FakeProbeClient()
    state = ProbeState()
    cycle_of(client, state=state)
    second = cycle_of(client, state=state)
    assert client.exchange_info_calls == 1
    assert "filters_changed" not in second  # not due: no run recorded
    assert sample("tradeengine_synthetic_probe_filters_changed", symbol="BTCUSDT") == 0
    client.filters = [
        {**f, "stepSize": "0.01"} if f["filterType"] == "LOT_SIZE" else f
        for f in FILTERS
    ]
    changed = cycle_of(client, state=state, filters_ttl_seconds=0)
    assert changed["filters_changed"] is Result.SUCCESS
    assert sample("tradeengine_synthetic_probe_filters_changed", symbol="BTCUSDT") == 1
    cycle_of(client, state=state, filters_ttl_seconds=0)
    assert sample("tradeengine_synthetic_probe_filters_changed", symbol="BTCUSDT") == 0


@pytest.mark.unit
def test_order_tests_are_skipped_not_failed_when_the_filters_are_unavailable():
    client = FakeProbeClient()

    def down():
        raise requests.exceptions.ConnectionError("down")

    client.futures_exchange_info = down
    outcomes = cycle_of(client)
    assert outcomes["filters_changed"] is Result.TRANSPORT_ERROR
    for check in (
        "order_test_market",
        "order_test_limit",
        "order_test_negative_control",
    ):
        assert outcomes[check] is Result.SKIPPED
    assert client.test_orders == []


# ---------------------------------------------------------------- shared-key protection


@pytest.mark.unit
@pytest.mark.parametrize(
    "headers",
    [
        {"X-MBX-USED-WEIGHT-1M": "1300"},  # above half of 2400
        {"X-MBX-ORDER-COUNT-10S": "200"},  # above half of 300
    ],
)
def test_a_cycle_is_skipped_when_the_shared_key_is_busy(headers):
    client = FakeProbeClient()
    client.response = SimpleNamespace(headers=headers)
    outcomes = cycle_of(client)
    assert outcomes["filters_changed"] is Result.SUCCESS
    for check in CHECKS[1:]:
        assert outcomes[check] is Result.SKIPPED
    assert client.test_orders == [] and client.exchange_info_calls == 1


@pytest.mark.unit
def test_used_weight_and_order_count_are_published_and_a_quiet_key_runs():
    client = FakeProbeClient()
    client.response = SimpleNamespace(
        headers={"X-MBX-USED-WEIGHT-1M": "42", "X-MBX-ORDER-COUNT-10S": "3"}
    )
    outcomes = cycle_of(client)
    assert all(result is Result.SUCCESS for result in outcomes.values())
    assert sample("tradeengine_synthetic_probe_used_weight_1m") == 42
    assert sample("tradeengine_synthetic_probe_order_count_10s") == 3


@pytest.mark.unit
def test_a_rate_limit_stops_the_cycle_and_records_retry_after():
    client = FakeProbeClient()

    def limited():
        raise api_error(-1003, status=429, headers={"Retry-After": "120"})

    client.futures_account = limited
    state = ProbeState()
    outcomes = cycle_of(client, state=state)
    assert outcomes["account"] is Result.RATE_LIMITED
    for check in (
        "order_test_market",
        "order_test_limit",
        "order_test_negative_control",
    ):
        assert outcomes[check] is Result.SKIPPED
    assert state.retry_after == 120
    assert client.test_orders == []


@pytest.mark.unit
def test_a_cycle_past_its_deadline_counts_the_rest_as_timeout():
    client = FakeProbeClient()
    ticks = iter(range(0, 1000, 20))  # every clock read advances 20 s
    outcomes = cycle_of(client, deadline_seconds=45, clock=lambda: next(ticks))
    assert outcomes["filters_changed"] is Result.SUCCESS
    assert Result.TIMEOUT in outcomes.values()
    assert client.test_orders == []


@pytest.mark.unit
def test_retry_after_is_read_from_the_error_response():
    assert retry_after_seconds(api_error(-1003, 429, {"Retry-After": "30"})) == 30
    assert retry_after_seconds(api_error(-1003, 429)) is None
    assert retry_after_seconds(RuntimeError("x")) is None


@pytest.mark.unit
def test_the_loop_waits_before_the_first_cycle_and_jitters_the_interval():
    sleeps: list[float] = []
    state = ProbeState()
    probe_main.run_loop(
        lambda: {"time_sync": Result.SUCCESS},
        interval=300,
        first_run_delay=30,
        state=state,
        sleep=sleeps.append,
        uniform=lambda low, high: high,
        max_cycles=3,
    )
    assert sleeps[0] == 30
    assert sleeps[1:] == [pytest.approx(330.0)] * 2  # +10 %
    assert probe_main.next_delay(
        300, probe_main.Backoff(), lambda low, high: low
    ) == pytest.approx(270.0)


@pytest.mark.unit
def test_backoff_doubles_honours_retry_after_caps_and_resets():
    backoff = probe_main.Backoff()
    limited = {"account": Result.RATE_LIMITED}
    ok = {"account": Result.SUCCESS}
    delays = []
    for _ in range(6):
        backoff.update(limited, None)
        delays.append(backoff.delay)
    assert delays == [60, 120, 240, 480, 900, 900]
    backoff.update(limited, 1200)
    assert backoff.delay == 1200  # Retry-After wins over the cap
    backoff.update(ok, None)
    assert backoff.delay == 0
    assert (
        probe_main.next_delay(300, backoff := probe_main.Backoff(), lambda a, b: 1)
        == 300
    )
    backoff.delay = 480
    assert probe_main.next_delay(300, backoff, lambda a, b: 1) == 480


# ---------------------------------------------------------------- classification


@pytest.mark.unit
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ProbeForbidden("x"), Result.BLOCKED),
        (requests.exceptions.ReadTimeout("x"), Result.TIMEOUT),
        (requests.exceptions.ConnectTimeout("x"), Result.TIMEOUT),
        (TimeoutError("x"), Result.TIMEOUT),
        (requests.exceptions.ConnectionError("x"), Result.TRANSPORT_ERROR),
        (RuntimeError("x"), Result.TRANSPORT_ERROR),
        (api_error(-1022), Result.AUTH_ERROR),
        (api_error(-2015), Result.AUTH_ERROR),
        (api_error(-1021), Result.CLOCK_ERROR),
        (api_error(-1013), Result.EXCHANGE_REJECT),
        (api_error(-4061), Result.EXCHANGE_REJECT),
        (api_error(-1003, 429), Result.RATE_LIMITED),
        (api_error(-1000, 418), Result.RATE_LIMITED),
        (api_error(-9999), Result.UNEXPECTED),
    ],
)
def test_classification(error, expected):
    assert classify_error(error) is expected


# ---------------------------------------------------------------- metrics are served


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.mark.unit
def test_the_metrics_are_served_over_http_with_every_series_initialised():
    from prometheus_client import start_http_server

    metrics.initialise_series()
    port = free_port()
    start_http_server(port, addr="127.0.0.1")
    body = (
        urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=10)
        .read()
        .decode()
    )
    for check in metrics.CHECKS:
        for result in metrics.RESULTS:
            assert (
                f'tradeengine_synthetic_probe_runs_total{{check="{check}",result="{result}"}}'
                in body
            )
    assert "tradeengine_synthetic_probe_recv_window_seconds" in body


def fake_main_env(monkeypatch, client):
    monkeypatch.setattr(probe_main, "ProbeBinanceClient", lambda **kwargs: client)
    monkeypatch.setenv("BINANCE_TESTNET", "true")
    monkeypatch.setenv("BINANCE_API_KEY", "key")
    monkeypatch.setenv("BINANCE_API_SECRET", "secret")


@pytest.mark.unit
def test_loop_mode_starts_the_metrics_server_before_the_first_cycle(monkeypatch):
    events: list[str] = []
    client = FakeProbeClient()
    fake_main_env(monkeypatch, client)
    monkeypatch.delenv("TE_SYNTHETIC_PROBE_ONESHOT", raising=False)
    monkeypatch.setenv("TE_SYNTHETIC_PROBE_METRICS_PORT", "9123")
    monkeypatch.setattr(
        probe_main, "start_http_server", lambda port: events.append(f"serve:{port}")
    )
    monkeypatch.setattr(
        probe_main, "run_loop", lambda cycle, **kwargs: events.append("loop")
    )
    assert probe_main.main() == 0
    assert events == ["serve:9123", "loop"]
    assert sample("tradeengine_synthetic_probe_recv_window_seconds") == 10.0
    assert (
        sample(
            "tradeengine_synthetic_probe_info",
            mode="loop",
            testnet="true",
            endpoint_host="testnet.binancefuture.com",
        )
        == 1
    )


@pytest.mark.unit
def test_one_shot_serves_nothing_and_returns_nonzero_when_any_check_fails(monkeypatch):
    client = FakeProbeClient()
    client.futures_account = lambda: {"canTrade": False}
    fake_main_env(monkeypatch, client)
    monkeypatch.setenv("TE_SYNTHETIC_PROBE_ONESHOT", "true")
    monkeypatch.setattr(
        probe_main,
        "start_http_server",
        lambda port: pytest.fail("one-shot serves nothing"),
    )
    assert probe_main.main() == 1


@pytest.mark.unit
def test_the_real_binance_exception_is_classified():
    """The real BinanceAPIException, loaded from its file (the sys.modules entry may be another test's mock)."""
    import importlib.machinery
    import importlib.util

    spec = importlib.machinery.PathFinder.find_spec("binance")
    path = Path(spec.submodule_search_locations[0]) / "exceptions.py"
    loader = importlib.util.spec_from_file_location("real_binance_exceptions", path)
    module = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(module)

    def real(code, status, headers=None):
        response = SimpleNamespace(headers=headers or {}, status_code=status)
        return module.BinanceAPIException(
            response, status, json.dumps({"code": code, "msg": "x"})
        )

    assert classify_error(real(-1022, 401)) is Result.AUTH_ERROR
    assert classify_error(real(-1021, 400)) is Result.CLOCK_ERROR
    assert classify_error(real(-4061, 400)) is Result.EXCHANGE_REJECT
    assert classify_error(real(-1003, 429)) is Result.RATE_LIMITED
    assert classify_error(real(-9999, 400)) is Result.UNEXPECTED
    assert retry_after_seconds(real(-1003, 429, {"Retry-After": "45"})) == 45
    assert (
        classify_error(module.BinanceRequestException("not json"))
        is Result.TRANSPORT_ERROR
    )


# ---------------------------------------------------------------- review round 2 (#784)


def decimals(text: str) -> int:
    return len(text.split(".")[1]) if "." in text else 0


@pytest.mark.unit
@pytest.mark.parametrize(
    ("step", "tick", "price", "expected"),
    [
        (
            "0.001",
            "0.10",
            58509.84,
            "58509.8",
        ),  # step finer than tick: BTCUSDT was sent as 58509.800
        ("1", "0.1", 58509.84, "58509.8"),  # step coarser than tick
        ("0.0001", "0.1", 58509.84, "58509.8"),
        ("0.1", "0.0001", 0.52319, "0.5231"),  # XRPUSDT: was floored to 0.4
        ("1", "0.00001", 0.123459, "0.12345"),  # DOGEUSDT: was floored to 0
        ("1", "0.5", 101.74, "101.5"),
    ],
)
def test_the_limit_price_follows_the_tick_size_not_the_quantity_step(
    step, tick, price, expected
):
    params = build_order_params(
        symbol="X",
        side="BUY",
        order_type="LIMIT",
        price=price,
        step=step,
        min_qty=step,
        min_notional="5",
        tick=tick,
        limit_price=price,
    )
    assert params["price"] == expected
    assert decimals(params["quantity"]) == decimals(
        step
    )  # the quantity keeps its own step


@pytest.mark.unit
def test_a_limit_order_needs_its_tick():
    with pytest.raises(ValueError) as error:
        build_order_params(
            symbol="X",
            side="BUY",
            order_type="LIMIT",
            price=100,
            step="0.001",
            min_qty="0.001",
            min_notional="5",
            limit_price=100,
        )
    assert "tick" in str(error.value)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("symbol", "price", "step", "tick"),
    [
        ("BTCUSDT", "80000.05", "0.001", "0.10"),
        ("ETHUSDT", "2500.55", "0.01", "0.01"),
        ("XRPUSDT", "0.5231", "0.1", "0.0001"),
        ("DOGEUSDT", "0.12345", "1", "0.00001"),
    ],
)
def test_the_cycle_sends_a_valid_limit_price_for_any_symbol(symbol, price, step, tick):
    client = FakeProbeClient(
        symbol=symbol, price=price, filters=filters_of(step=step, tick=tick)
    )
    client.futures_create_test_order = lambda **kw: client.test_orders.append(kw) or {}
    outcomes = cycle_of(client)
    assert outcomes["order_test_limit"] is Result.SUCCESS
    limit = next(o for o in client.test_orders if o["type"] == "LIMIT")
    sent = Decimal(limit["price"])
    assert sent % Decimal(tick) == 0  # a multiple of the tick
    assert decimals(limit["price"]) == decimals(format(Decimal(tick).normalize(), "f"))
    assert (
        Decimal(price) * Decimal("0.95") <= sent < Decimal(price)
    )  # inside the band, below the mark
    assert decimals(limit["quantity"]) == decimals(step)


@pytest.mark.unit
@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "NaN", "Infinity"])
@pytest.mark.parametrize(
    "name",
    [
        "TE_SYNTHETIC_PROBE_INTERVAL_SECONDS",
        "TE_SYNTHETIC_PROBE_FIRST_RUN_DELAY_SECONDS",
    ],
)
def test_startup_refuses_a_non_finite_interval_or_delay(name, value):
    result = run_probe({"BINANCE_TESTNET": "true", name: value})
    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    assert result.stderr.strip()


@pytest.mark.unit
def test_the_order_count_is_read_from_the_order_test_responses():
    """/time never carries X-MBX-ORDER-COUNT-10S; the order/test responses do."""
    client = FakeProbeClient()
    original = client.futures_create_test_order

    def with_order_count(**kwargs):
        client.response = SimpleNamespace(headers={"X-MBX-ORDER-COUNT-10S": "200"})
        return original(**kwargs)

    client.futures_create_test_order = with_order_count
    outcomes = cycle_of(client)
    assert outcomes["order_test_market"] is Result.SUCCESS
    assert (
        outcomes["order_test_limit"] is Result.SKIPPED
    )  # 200 > half of 300, seen after the first order test
    assert outcomes["order_test_negative_control"] is Result.SKIPPED
    assert sample("tradeengine_synthetic_probe_order_count_10s") == 200


@pytest.mark.unit
def test_the_weight_is_read_after_every_response():
    client = FakeProbeClient()
    original = client.futures_account

    def heavy_account():
        client.response = SimpleNamespace(headers={"X-MBX-USED-WEIGHT-1M": "1500"})
        return original()

    client.futures_account = heavy_account
    outcomes = cycle_of(client)
    assert outcomes["account"] is Result.SUCCESS
    assert outcomes["order_test_market"] is Result.SKIPPED
    assert sample("tradeengine_synthetic_probe_used_weight_1m") == 1500


@pytest.mark.unit
def test_the_limits_come_from_the_exchange_and_the_fallbacks_only_until_then():
    limits = [
        {
            "rateLimitType": "REQUEST_WEIGHT",
            "interval": "MINUTE",
            "intervalNum": 1,
            "limit": 1000,
        },
        {
            "rateLimitType": "ORDERS",
            "interval": "SECOND",
            "intervalNum": 10,
            "limit": 100,
        },
        {
            "rateLimitType": "ORDERS",
            "interval": "MINUTE",
            "intervalNum": 1,
            "limit": 1200,
        },
    ]
    busy = {
        "X-MBX-USED-WEIGHT-1M": "600"
    }  # above half of 1000, below half of the 2400 fallback
    client = FakeProbeClient(rate_limits=limits)
    state = ProbeState()
    cycle_of(
        client, state=state
    )  # reads exchangeInfo (after time_sync), no headers yet
    assert (state.weight_limit, state.order_limit_10s) == (1000, 100)
    client.response = SimpleNamespace(headers=busy)
    skipped = cycle_of(client, state=state)
    assert skipped["time_sync"] is Result.SUCCESS
    assert all(
        skipped[c] is Result.SKIPPED
        for c in ("signed_read", "account", "order_test_market")
    )
    # without limits from the exchange the labelled fallback (2400) stands: 600 is fine
    fallback = FakeProbeClient()
    fallback.response = SimpleNamespace(headers=busy)
    assert all(r is Result.SUCCESS for r in cycle_of(fallback).values())


@pytest.mark.unit
def test_last_run_is_set_by_every_cycle(monkeypatch):
    """The stale alert reads only this gauge."""
    fake_main_env(monkeypatch, FakeProbeClient())
    monkeypatch.setenv("TE_SYNTHETIC_PROBE_ONESHOT", "true")
    metrics.last_run.set(0)
    before = time.time()
    assert probe_main.main() == 0
    assert (
        before - 1
        <= sample("tradeengine_synthetic_probe_last_run_timestamp_seconds")
        <= time.time() + 1
    )


@pytest.mark.unit
def test_main_initialises_every_series_at_zero(monkeypatch):
    metrics.runs.clear()
    fake_main_env(monkeypatch, FakeProbeClient())
    monkeypatch.setenv("TE_SYNTHETIC_PROBE_ONESHOT", "true")
    probe_main.main()
    for check in metrics.CHECKS:
        for result in metrics.RESULTS:
            assert (
                REGISTRY.get_sample_value(
                    "tradeengine_synthetic_probe_runs_total",
                    {"check": check, "result": result},
                )
                is not None
            ), (check, result)  # present even where nothing was counted


@pytest.mark.unit
def test_clock_skew_uses_the_midpoint_of_the_request():
    """A slow request: the server stamped the middle of it, so the skew is ~0 (start or end would read +-0.2)."""
    client = FakeProbeClient()

    def slow_time():
        started = time.time()
        time.sleep(0.4)
        return {"serverTime": int((started + 0.2) * 1000)}

    client.futures_time = slow_time
    cycle_of(client)
    assert abs(sample("tradeengine_synthetic_probe_clock_skew_seconds")) < 0.05


@pytest.mark.unit
def test_the_loop_defaults_are_port_8000_interval_300_and_a_30_second_first_delay(
    monkeypatch,
):
    seen: dict = {}
    fake_main_env(monkeypatch, FakeProbeClient())
    for name in (
        "TE_SYNTHETIC_PROBE_ONESHOT",
        "TE_SYNTHETIC_PROBE_METRICS_PORT",
        "TE_SYNTHETIC_PROBE_INTERVAL_SECONDS",
        "TE_SYNTHETIC_PROBE_FIRST_RUN_DELAY_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        probe_main, "start_http_server", lambda port: seen.update(port=port)
    )
    monkeypatch.setattr(
        probe_main, "run_loop", lambda cycle, **kwargs: seen.update(kwargs)
    )
    assert probe_main.main() == 0
    assert seen["port"] == 8000
    assert seen["interval"] == 300
    assert seen["first_run_delay"] == 30
    assert sample("tradeengine_synthetic_probe_interval_seconds") == 300


@pytest.mark.unit
@pytest.mark.parametrize(
    ("elapsed", "expected"), [(44.9, Result.SUCCESS), (45.1, Result.TIMEOUT)]
)
def test_the_default_cycle_deadline_is_45_seconds(elapsed, expected):
    reads = iter([0.0])

    def clock():
        return next(reads, elapsed)

    outcomes = run_cycle(ProbeFacade(FakeProbeClient()), symbol="BTCUSDT", clock=clock)
    assert outcomes["time_sync"] is expected


@pytest.mark.unit
def test_the_order_limit_comes_from_the_exchange_too():
    limits = [
        {
            "rateLimitType": "REQUEST_WEIGHT",
            "interval": "MINUTE",
            "intervalNum": 1,
            "limit": 2400,
        },
        {
            "rateLimitType": "ORDERS",
            "interval": "SECOND",
            "intervalNum": 10,
            "limit": 100,
        },
    ]
    client = FakeProbeClient(rate_limits=limits)
    state = ProbeState()
    cycle_of(client, state=state)
    client.response = SimpleNamespace(
        headers={"X-MBX-ORDER-COUNT-10S": "60"}
    )  # > half of 100, < half of 300
    skipped = cycle_of(client, state=state)
    assert skipped["time_sync"] is Result.SUCCESS
    assert skipped["signed_read"] is Result.SKIPPED
    fallback = FakeProbeClient()
    fallback.response = SimpleNamespace(headers={"X-MBX-ORDER-COUNT-10S": "60"})
    assert all(r is Result.SUCCESS for r in cycle_of(fallback).values())


@pytest.mark.unit
def test_a_negative_or_unreadable_header_is_not_a_reading():
    """The testnet answers X-MBX-USED-WEIGHT-1M: -1 on order/test: it must not overwrite the gauge or skip."""
    client = FakeProbeClient()
    client.response = SimpleNamespace(headers={"X-MBX-USED-WEIGHT-1M": "42"})
    cycle_of(client)
    original = client.futures_create_test_order

    def negative(**kwargs):
        client.response = SimpleNamespace(
            headers={"X-MBX-USED-WEIGHT-1M": "-1", "X-MBX-ORDER-COUNT-10S": "oops"}
        )
        return original(**kwargs)

    client.futures_create_test_order = negative
    outcomes = cycle_of(client)
    assert all(result is Result.SUCCESS for result in outcomes.values())
    assert sample("tradeengine_synthetic_probe_used_weight_1m") == 42


# ---------------------------------------------------------------- round 3: busy start, limits, price filter, port
def limit_entries(weight=6000, orders=300):
    return [
        {
            "rateLimitType": "REQUEST_WEIGHT",
            "interval": "MINUTE",
            "intervalNum": 1,
            "limit": weight,
        },
        {
            "rateLimitType": "ORDERS",
            "interval": "SECOND",
            "intervalNum": 10,
            "limit": orders,
        },
    ]


def with_headers(client, **headers):
    client.response = SimpleNamespace(
        headers={k.replace("_", "-"): str(v) for k, v in headers.items()}
    )
    return client


@pytest.mark.unit
def test_a_restarted_probe_on_a_busy_key_learns_the_real_limit_before_judging_the_weight():
    """Weight 1500 is above half of the 2400 fallback but far below half of the real 6000."""
    client = with_headers(
        FakeProbeClient(rate_limits=limit_entries()), X_MBX_USED_WEIGHT_1M=1500
    )
    calls = []
    for name in ("futures_exchange_info", "futures_time"):
        original = getattr(client, name)
        setattr(
            client,
            name,
            lambda original=original, name=name: calls.append(name) or original(),
        )
    state = ProbeState()
    outcomes = cycle_of(client, state=state)
    assert calls[0] == "futures_exchange_info"
    assert all(result is Result.SUCCESS for result in outcomes.values()), outcomes
    assert state.weight_limit == 6000 and len(client.test_orders) == 3


@pytest.mark.unit
def test_a_busy_key_is_still_skipped_when_the_exchange_reports_no_limits():
    client = with_headers(
        FakeProbeClient(), X_MBX_USED_WEIGHT_1M=1500
    )  # no rateLimits: the 2400 fallback stands
    outcomes = cycle_of(client, state=ProbeState())
    assert outcomes["filters_changed"] is Result.SUCCESS
    assert outcomes["order_test_market"] is Result.SKIPPED and client.test_orders == []


@pytest.mark.unit
def test_a_busy_key_is_skipped_against_the_real_limit_too():
    client = with_headers(
        FakeProbeClient(rate_limits=limit_entries(weight=1000)),
        X_MBX_USED_WEIGHT_1M=600,
    )
    outcomes = cycle_of(client, state=ProbeState())
    assert (
        outcomes["filters_changed"] is Result.SUCCESS
        and outcomes["time_sync"] is Result.SKIPPED
    )


@pytest.mark.unit
@pytest.mark.parametrize("bad", [None, 0, -1, "abc", "", True, 5.5, [], {}])
@pytest.mark.parametrize("which", ["weight_1m", "orders_10s"])
def test_an_unusable_exchange_limit_is_ignored_counted_and_never_stops_the_cycle(
    bad, which
):
    entries = limit_entries(weight=1000, orders=100)
    index = 0 if which == "weight_1m" else 1
    entries[index]["limit"] = bad
    before = sample("tradeengine_synthetic_probe_rate_limit_ignored_total", limit=which)
    client = FakeProbeClient(rate_limits=entries)
    state = ProbeState()
    outcomes = cycle_of(client, state=state)
    assert all(result is Result.SUCCESS for result in outcomes.values()), outcomes
    assert len(client.test_orders) == 3
    assert (
        sample("tradeengine_synthetic_probe_rate_limit_ignored_total", limit=which)
        == before + 1
    )
    good = (1000, 100)
    assert (state.weight_limit, state.order_limit_10s)[1 - index] == good[
        1 - index
    ]  # the other limit is read
    assert (state.weight_limit, state.order_limit_10s)[
        index
    ] is None  # the fallback stands for the bad one


@pytest.mark.unit
def test_a_missing_limit_key_does_not_raise_and_a_good_value_is_kept_when_a_later_entry_is_bad():
    entries = limit_entries(weight=1000)
    del entries[0]["limit"]
    client = FakeProbeClient(rate_limits=entries)
    assert cycle_of(client)["filters_changed"] is Result.SUCCESS
    state = ProbeState(weight_limit=1000, order_limit_10s=100)
    cycle_of(
        FakeProbeClient(rate_limits=limit_entries(weight=0, orders=-5)), state=state
    )
    assert (state.weight_limit, state.order_limit_10s) == (1000, 100)


@pytest.mark.unit
@pytest.mark.parametrize("rate_limits", ["x", {"a": 1}, 5, [None, 3, "x", []]])
def test_malformed_rate_limits_are_ignored(rate_limits):
    client = FakeProbeClient(rate_limits=rate_limits)
    outcomes = cycle_of(client, state=ProbeState())
    assert all(result is Result.SUCCESS for result in outcomes.values())


@pytest.mark.unit
@pytest.mark.parametrize("raw", ["6000", " 6000 "])
def test_a_limit_given_as_digits_is_accepted(raw):
    state = ProbeState()
    cycle_of(
        FakeProbeClient(rate_limits=limit_entries(weight=raw, orders="300")),
        state=state,
    )
    assert (state.weight_limit, state.order_limit_10s) == (6000, 300)


@pytest.mark.unit
def test_the_ignored_limit_counter_is_present_at_zero():
    from tradeengine_probe import metrics

    metrics.initialise_series()
    for which in ("weight_1m", "orders_10s"):
        assert (
            REGISTRY.get_sample_value(
                "tradeengine_synthetic_probe_rate_limit_ignored_total", {"limit": which}
            )
            is not None
        )


@pytest.mark.unit
def test_without_percent_price_the_limit_sits_two_percent_below_the_mark():
    client = FakeProbeClient(
        filters=[f for f in FILTERS if f["filterType"] != "PERCENT_PRICE"]
    )
    cycle_of(client)
    limit = client.test_orders[1]
    assert limit["type"] == "LIMIT" and limit["price"] == "98.00"


@pytest.mark.unit
def test_with_percent_price_the_limit_is_the_midpoint_of_the_band_floored_to_the_tick():
    client = FakeProbeClient(
        price="83000.77", filters=filters_of(step="0.001", tick="0.1")
    )
    cycle_of(client)
    assert (
        client.test_orders[1]["price"] == "80925.7"
    )  # 83000.77 * 0.975 = 80925.75, floored, not rounded up


@pytest.mark.unit
def test_the_tick_floor_rounds_down():
    from tradeengine_probe.checks import _floor_to

    assert _floor_to(Decimal("1.99"), Decimal("0.5")) == Decimal("1.5")
    assert _floor_to(Decimal("1.5"), Decimal("0.5")) == Decimal("1.5")
    assert _floor_to(Decimal("0.0099"), Decimal("0.01")) == Decimal("0")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("price", "check", "result"),
    [
        ("0.005", "order_test_market", Result.UNEXPECTED),  # below minPrice 0.01
        (
            "1000000.01",
            "order_test_market",
            Result.UNEXPECTED,
        ),  # above maxPrice 1000000
        ("0.01", "order_test_market", Result.SUCCESS),  # exactly minPrice
        ("1000000", "order_test_market", Result.SUCCESS),  # exactly maxPrice
    ],
)
def test_a_ticker_price_outside_the_price_filter_is_a_failed_check_not_an_order(
    price, check, result
):
    client = FakeProbeClient(price=price)
    outcomes = cycle_of(client)
    assert outcomes[check] is result
    if result is Result.UNEXPECTED:
        assert all(order["type"] != "MARKET" for order in client.test_orders)


@pytest.mark.unit
def test_a_limit_price_below_the_price_filter_minimum_is_a_failed_check():
    # mark 0.011 with the 0.975 factor is 0.010725, above 0.01; mark 0.0101 gives 0.0098 (below minPrice)
    client = FakeProbeClient(price="0.0101")
    outcomes = cycle_of(client)
    assert outcomes["order_test_limit"] is Result.UNEXPECTED
    assert all(order["type"] != "LIMIT" for order in client.test_orders)


@pytest.mark.unit
def test_the_weight_is_read_from_the_response_of_a_failed_check_too():
    client = FakeProbeClient()

    def failing_account():
        client.response = SimpleNamespace(headers={"X-MBX-USED-WEIGHT-1M": "1500"})
        raise api_error(-1013)

    client.futures_account = failing_account
    outcomes = cycle_of(client, state=ProbeState(weight_limit=2400))
    assert outcomes["account"] is Result.EXCHANGE_REJECT
    assert outcomes["order_test_market"] is Result.SKIPPED
    assert sample("tradeengine_synthetic_probe_used_weight_1m") == 1500


@pytest.mark.unit
def test_the_order_count_is_read_from_the_response_of_a_failed_check_too():
    client = FakeProbeClient()

    def failing_account():
        client.response = SimpleNamespace(headers={"X-MBX-ORDER-COUNT-10S": "250"})
        raise api_error(-1013)

    client.futures_account = failing_account
    outcomes = cycle_of(client, state=ProbeState(order_limit_10s=300))
    assert outcomes["order_test_market"] is Result.SKIPPED
    assert sample("tradeengine_synthetic_probe_order_count_10s") == 250


@pytest.mark.unit
@pytest.mark.parametrize(
    ("headers", "skipped"),
    [
        ({"X-MBX-USED-WEIGHT-1M": "500"}, False),  # exactly half of 1000: still runs
        ({"X-MBX-USED-WEIGHT-1M": "501"}, True),
        ({"X-MBX-ORDER-COUNT-10S": "50"}, False),  # exactly half of 100
        ({"X-MBX-ORDER-COUNT-10S": "51"}, True),
    ],
)
def test_the_skip_starts_just_above_the_configured_fraction_of_the_limit(
    headers, skipped
):
    client = FakeProbeClient()
    client.response = SimpleNamespace(headers=headers)
    outcomes = cycle_of(
        client, state=ProbeState(weight_limit=1000, order_limit_10s=100)
    )
    assert (outcomes["order_test_market"] is Result.SKIPPED) is skipped
    assert (len(client.test_orders) == 0) is skipped


@pytest.mark.unit
@pytest.mark.parametrize(
    "port", ["99999", "65536", "0", "-1", "80.5", "1e999", "-1e999", "nan", "inf"]
)
def test_startup_refuses_a_metrics_port_that_is_not_a_port(port):
    result = run_probe(
        {"BINANCE_TESTNET": "true", "TE_SYNTHETIC_PROBE_METRICS_PORT": port}
    )
    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    assert "whole number from 1 to 65535" in result.stderr


@pytest.mark.unit
@pytest.mark.parametrize("port", ["1", "8000", "65535", "8000.0"])
def test_startup_accepts_a_valid_metrics_port(monkeypatch, port):
    seen: dict = {}
    fake_main_env(monkeypatch, FakeProbeClient())
    monkeypatch.delenv("TE_SYNTHETIC_PROBE_ONESHOT", raising=False)
    monkeypatch.setenv("TE_SYNTHETIC_PROBE_METRICS_PORT", port)
    monkeypatch.setattr(probe_main, "start_http_server", lambda p: seen.update(port=p))
    monkeypatch.setattr(probe_main, "run_loop", lambda cycle, **kwargs: None)
    assert probe_main.main() == 0
    assert seen["port"] == int(float(port)) and isinstance(seen["port"], int)
