"""Result classification and execution for bounded exchange checks."""

from __future__ import annotations

from collections.abc import Callable
from decimal import ROUND_DOWN, Decimal
from enum import StrEnum
from time import perf_counter, time
from typing import Any

from binance.exceptions import BinanceAPIException

from . import metrics
from .params import build_order_params


class Result(StrEnum):
    SUCCESS = "success"
    AUTH_ERROR = "auth_error"
    CLOCK_ERROR = "clock_error"
    EXCHANGE_REJECT = "exchange_reject"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    TRANSPORT_ERROR = "transport_error"
    BLOCKED = "blocked"
    UNEXPECTED = "unexpected"
    SKIPPED = "skipped"


AUTH_CODES = {-1022, -2014, -2015, -2008, -4045}
REJECT_CODES = {-1013, -1111, -4003, -4005, -4014, -4131, -4164, -2019}


def classify_error(error: BaseException) -> Result:
    if isinstance(error, CheckFailure):
        return Result.UNEXPECTED
    code = getattr(error, "code", None)
    status = getattr(error, "status_code", None)
    if code in AUTH_CODES or status in {401, 403}:
        return Result.AUTH_ERROR
    if code == -1021:
        return Result.CLOCK_ERROR
    if code in REJECT_CODES:
        return Result.EXCHANGE_REJECT
    if code in {-1003, -1015} or status in {418, 429}:
        return Result.RATE_LIMITED
    if isinstance(error, TimeoutError):
        return Result.TIMEOUT
    if isinstance(error, BinanceAPIException):
        return Result.UNEXPECTED
    return Result.TRANSPORT_ERROR


class CheckFailure(RuntimeError):
    """Raised when an exchange response violates a probe check."""


def _record(check: str, operation: Callable[[], Any]) -> tuple[Result, Any | None]:
    started = perf_counter()
    try:
        value = operation()
    except Exception as error:
        result = classify_error(error)
        metrics.runs.labels(check=check, result=result.value).inc()
        metrics.duration.labels(check=check).observe(perf_counter() - started)
        metrics.last_error.labels(check=check).set(getattr(error, "code", 0) or 0)
        return result, None
    metrics.runs.labels(check=check, result=Result.SUCCESS.value).inc()
    metrics.duration.labels(check=check).observe(perf_counter() - started)
    metrics.last_success.labels(check=check).set(time())
    metrics.last_error.labels(check=check).set(0)
    return Result.SUCCESS, value


def _check_time(client: Any) -> tuple[Result, Result]:
    started = perf_counter()
    started_wall = time()
    try:
        response = client.futures_time()
        finished = perf_counter()
        finished_wall = time()
        server_ms = float(response["serverTime"])
        midpoint_ms = (started_wall + (finished_wall - started_wall) / 2) * 1000
        metrics.clock_skew.set(server_ms - midpoint_ms)
    except Exception as error:
        result = classify_error(error)
        for check in ("clock_skew", "latency"):
            metrics.runs.labels(check=check, result=result.value).inc()
            metrics.duration.labels(check=check).observe(perf_counter() - started)
            metrics.last_error.labels(check=check).set(getattr(error, "code", 0) or 0)
        return result, result
    elapsed = finished - started
    for check in ("clock_skew", "latency"):
        metrics.runs.labels(check=check, result=Result.SUCCESS.value).inc()
        metrics.duration.labels(check=check).observe(elapsed)
        metrics.last_success.labels(check=check).set(time())
        metrics.last_error.labels(check=check).set(0)
    return Result.SUCCESS, Result.SUCCESS


def _check_auth(client: Any) -> dict[str, Any]:
    account = client.futures_account()
    can_trade = account.get("canTrade") is True
    metrics.can_trade.set(1 if can_trade else 0)
    if not can_trade:
        raise CheckFailure("account cannot trade")
    return account


def _check_hedge_mode(client: Any) -> dict[str, Any]:
    response = client.futures_get_position_mode()
    if "dualSidePosition" not in response:
        raise CheckFailure("position mode missing")
    metrics.hedge_mode.set(1 if response["dualSidePosition"] else 0)
    return response


def _filters_for(exchange_info: dict[str, Any], symbol: str) -> dict[str, str]:
    symbol_info = next(
        (
            item
            for item in exchange_info.get("symbols", [])
            if item.get("symbol") == symbol
        ),
        None,
    )
    if symbol_info is None:
        raise CheckFailure("symbol missing")
    filters = {item["filterType"]: item for item in symbol_info.get("filters", [])}
    required = {"LOT_SIZE", "MIN_NOTIONAL", "PRICE_FILTER"}
    if not required.issubset(filters):
        raise CheckFailure("required exchange filter missing")
    return {
        "step": filters["LOT_SIZE"]["stepSize"],
        "min_qty": filters["LOT_SIZE"]["minQty"],
        "min_notional": filters["MIN_NOTIONAL"]["notional"],
        "min_price": filters["PRICE_FILTER"]["minPrice"],
        "max_price": filters["PRICE_FILTER"]["maxPrice"],
        "tick_size": filters["PRICE_FILTER"]["tickSize"],
    }


def run_cycle(client: Any, *, symbol: str) -> dict[str, Result]:
    """Run every check once and return only classifications."""
    outcomes: dict[str, Result] = {}
    _, time_result = _check_time(client)
    outcomes["clock_skew"] = time_result
    outcomes["latency"] = time_result
    outcomes["auth"], _ = _record("auth", lambda: _check_auth(client))
    outcomes["hedge_mode"], _ = _record("hedge_mode", lambda: _check_hedge_mode(client))
    filter_result, filter_values = _record(
        "filters", lambda: _filters_for(client.futures_exchange_info(), symbol)
    )
    outcomes["filters"] = filter_result

    def order_test() -> None:
        if filter_values is None:
            raise CheckFailure("order test prerequisites unavailable")
        price = float(client.futures_symbol_ticker(symbol=symbol)["price"])
        if (
            not float(filter_values["min_price"])
            <= price
            <= float(filter_values["max_price"])
        ):
            raise CheckFailure("ticker outside price filter")
        tick = Decimal(filter_values["tick_size"])
        price = float(
            (Decimal(str(price)) / tick).to_integral_value(rounding=ROUND_DOWN) * tick
        )
        params = build_order_params(
            symbol=symbol,
            side="BUY",
            order_type="MARKET",
            price=price,
            step=filter_values["step"],
            min_qty=filter_values["min_qty"],
            min_notional=filter_values["min_notional"],
        )
        client.futures_create_test_order(**params)

    outcomes["order_test"], _ = _record("order_test", order_test)
    return outcomes
