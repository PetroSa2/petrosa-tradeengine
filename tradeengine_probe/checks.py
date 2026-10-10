"""Result classification and execution for bounded exchange checks (the checks of petrosa_k8s#1305)."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal
from enum import StrEnum
from time import perf_counter, time
from typing import Any

from requests import exceptions as requests_exceptions

from . import metrics
from ._client import ProbeForbidden
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


class CheckFailure(RuntimeError):
    """Raised when an exchange response violates a probe check."""


class SkipCheck(RuntimeError):
    """Raised when a check cannot run because what it needs is not available (not a failure of its own)."""


#: The real classes, taken at import: another test may replace an attribute of ``requests.exceptions`` later.
_TIMEOUT_ERRORS = (TimeoutError, requests_exceptions.Timeout)
AUTH_CODES = {-1022, -2014, -2015, -2008, -4045}
#: -4061: the order's position side does not match the account's position mode.
REJECT_CODES = {-1013, -1111, -4003, -4005, -4014, -4061, -4131, -4164, -2019}
#: What the exchange answers to a quantity below the minimum (the negative control expects one of these).
FILTER_REJECT_CODES = {-1013, -1111, -4003, -4005, -4014, -4131, -4164}
FILTER_TYPES = (
    "LOT_SIZE",
    "MARKET_LOT_SIZE",
    "PRICE_FILTER",
    "MIN_NOTIONAL",
    "PERCENT_PRICE",
)
REQUIRED_FILTERS = ("LOT_SIZE", "MIN_NOTIONAL", "PRICE_FILTER")
FILTERS_TTL_SECONDS = 3600.0
CYCLE_DEADLINE_SECONDS = 45.0
#: Binance USD-M futures limits per account (shared with the live testnet tradeengine): request weight per
#: minute and orders per 10 seconds.
DEFAULT_WEIGHT_LIMIT_1M = 2400
DEFAULT_ORDER_LIMIT_10S = 300
DEFAULT_MAX_LIMIT_FRACTION = 0.5


def classify_error(error: BaseException) -> Result:
    if isinstance(error, ProbeForbidden):
        return Result.BLOCKED
    if isinstance(error, CheckFailure):
        return Result.UNEXPECTED
    if isinstance(error, SkipCheck):
        return Result.SKIPPED
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
    if isinstance(error, _TIMEOUT_ERRORS):
        return Result.TIMEOUT
    if code is not None:
        return (
            Result.UNEXPECTED
        )  # an exchange error (BinanceAPIException carries a code) we have no class for
    return Result.TRANSPORT_ERROR


def retry_after_seconds(error: BaseException) -> float | None:
    """The ``Retry-After`` of a 429/418 response, in seconds, when the error carries one."""
    headers = getattr(getattr(error, "response", None), "headers", None) or {}
    try:
        value = float(headers.get("Retry-After"))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


@dataclass
class ProbeState:
    """What survives between cycles: the filter snapshots, the position mode and the last Retry-After."""

    filters: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    filters_fetched_at: float | None = None
    hedge: bool | None = None
    retry_after: float | None = None


def _decimal_places(value: str) -> int:
    return max(0, -Decimal(str(value)).normalize().as_tuple().exponent)


def _floor_to(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def _snapshot_of(
    exchange_info: dict[str, Any], symbol: str
) -> dict[str, dict[str, Any]]:
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
    if not set(REQUIRED_FILTERS).issubset(filters):
        raise CheckFailure("required exchange filter missing")
    return {kind: dict(filters[kind]) for kind in FILTER_TYPES if kind in filters}


def _sizing(snapshot: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Step, minimum quantity and notional for a test order: the stricter of LOT_SIZE and MARKET_LOT_SIZE."""
    lots = [snapshot["LOT_SIZE"]]
    if "MARKET_LOT_SIZE" in snapshot:
        lots.append(snapshot["MARKET_LOT_SIZE"])
    step = max(lots, key=lambda lot: Decimal(str(lot["stepSize"])))["stepSize"]
    min_qty = max(lots, key=lambda lot: Decimal(str(lot["minQty"])))["minQty"]
    return {
        "step": str(step),
        "min_qty": str(min_qty),
        "min_notional": str(snapshot["MIN_NOTIONAL"]["notional"]),
    }


class _Cycle:
    """One run of every check, recording a metric per check and stopping early when the exchange says to."""

    def __init__(
        self,
        client: Any,
        state: ProbeState,
        *,
        deadline_seconds: float,
        max_limit_fraction: float,
        weight_limit: int,
        order_limit_10s: int,
        clock: Callable[[], float],
        wall: Callable[[], float],
    ) -> None:
        self.client = client
        self.state = state
        self.deadline_seconds = deadline_seconds
        self.max_limit_fraction = max_limit_fraction
        self.weight_limit = weight_limit
        self.order_limit_10s = order_limit_10s
        self.clock = clock
        self.wall = wall
        self.started = clock()
        self.outcomes: dict[str, Result] = {}
        self.stopped: Result | None = None

    def run(self, check: str, operation: Callable[[], Any]) -> Any | None:
        if self.stopped is None and self.clock() - self.started > self.deadline_seconds:
            self.stopped = (
                Result.TIMEOUT
            )  # the cycle ran past its deadline: the rest did not run
        if self.stopped is not None:
            self._count(check, self.stopped)
            self.outcomes[check] = self.stopped
            return None
        started = self.clock()
        try:
            value = operation()
        except Exception as error:
            result = classify_error(error)
            self._count(check, result)
            metrics.duration.labels(check=check).observe(self.clock() - started)
            if result is not Result.SKIPPED:
                metrics.last_error.labels(check=check).set(
                    getattr(error, "code", 0) or 0
                )
            if result is Result.RATE_LIMITED:
                self.state.retry_after = retry_after_seconds(error)
                self.stopped = (
                    Result.SKIPPED
                )  # do not push on while the exchange says wait
            self.outcomes[check] = result
            return None
        self._count(check, Result.SUCCESS)
        metrics.duration.labels(check=check).observe(self.clock() - started)
        metrics.last_success.labels(check=check).set(self.wall())
        metrics.last_error.labels(check=check).set(0)
        self.outcomes[check] = Result.SUCCESS
        return value

    @staticmethod
    def _count(check: str, result: Result) -> None:
        metrics.runs.labels(check=check, result=result.value).inc()

    # -- the checks ---------------------------------------------------------------------------------

    def time_sync(self) -> None:
        started_wall = self.wall()
        response = self.client.futures_time()
        finished_wall = self.wall()
        midpoint = started_wall + (finished_wall - started_wall) / 2
        metrics.clock_skew.set(float(response["serverTime"]) / 1000.0 - midpoint)
        self._observe_limits()

    def _observe_limits(self) -> None:
        """Publish the used weight and order count of the last response; stop the cycle when they are high."""
        headers_of = getattr(self.client, "response_headers", None)
        headers = headers_of() if callable(headers_of) else {}
        try:
            used_weight = int(headers.get("x-mbx-used-weight-1m", ""))
            metrics.used_weight.set(used_weight)
            if used_weight > self.max_limit_fraction * self.weight_limit:
                self.stopped = Result.SKIPPED
        except ValueError:
            pass
        try:
            orders = int(headers.get("x-mbx-order-count-10s", ""))
            metrics.order_count_10s.set(orders)
            if orders > self.max_limit_fraction * self.order_limit_10s:
                self.stopped = Result.SKIPPED
        except ValueError:
            pass

    def signed_read(self) -> None:
        response = self.client.futures_get_position_mode()
        if "dualSidePosition" not in response:
            raise CheckFailure("position mode missing")
        self.state.hedge = bool(response["dualSidePosition"])
        metrics.hedge_mode.set(1 if self.state.hedge else 0)

    def account(self) -> None:
        can_trade = self.client.futures_account().get("canTrade") is True
        metrics.can_trade.set(1 if can_trade else 0)
        if not can_trade:
            raise CheckFailure("account cannot trade")

    def filters_changed(self, symbol: str) -> None:
        snapshot = _snapshot_of(self.client.futures_exchange_info(), symbol)
        previous = self.state.filters.get(symbol)
        metrics.filters_changed.labels(symbol=symbol).set(
            1 if previous is not None and previous != snapshot else 0
        )
        self.state.filters[symbol] = snapshot
        self.state.filters_fetched_at = self.clock()

    def _snapshot(self, symbol: str) -> dict[str, dict[str, Any]]:
        snapshot = self.state.filters.get(symbol)
        if snapshot is None:
            raise SkipCheck("exchange filters unavailable")
        return snapshot

    def _order_params(
        self,
        symbol: str,
        order_type: str,
        price: Decimal,
        *,
        limit_price: Decimal | None = None,
    ) -> dict[str, str | bool]:
        sizing = _sizing(self._snapshot(symbol))
        return build_order_params(
            symbol=symbol,
            side="BUY",
            order_type=order_type,
            price=float(price),
            step=sizing["step"],
            min_qty=sizing["min_qty"],
            min_notional=sizing["min_notional"],
            limit_price=None if limit_price is None else float(limit_price),
            # In hedge mode the order must carry its position side (-4061 otherwise); never reduceOnly with it.
            position_side="LONG" if self.state.hedge else None,
            client_order_id=f"ptest-{int(self.wall())}-{secrets.token_hex(4)}",
        )

    def _tick_price(self, snapshot: dict[str, dict[str, Any]], raw: str) -> Decimal:
        price_filter = snapshot["PRICE_FILTER"]
        price = Decimal(str(raw))
        if (
            not Decimal(str(price_filter["minPrice"]))
            <= price
            <= Decimal(str(price_filter["maxPrice"]))
        ):
            raise CheckFailure("price outside the price filter")
        return _floor_to(price, Decimal(str(price_filter["tickSize"])))

    def order_test_market(self, symbol: str) -> None:
        snapshot = self._snapshot(symbol)
        price = self._tick_price(
            snapshot, self.client.futures_symbol_ticker(symbol=symbol)["price"]
        )
        self.client.futures_create_test_order(
            **self._order_params(symbol, "MARKET", price)
        )

    def order_test_limit(self, symbol: str) -> None:
        """A BUY LIMIT GTC below the mark, inside the PERCENT_PRICE band: accepted, never marketable."""
        snapshot = self._snapshot(symbol)
        mark = Decimal(str(self.client.futures_mark_price(symbol=symbol)["markPrice"]))
        percent = snapshot.get("PERCENT_PRICE")
        factor = (
            (Decimal("1") + Decimal(str(percent["multiplierDown"]))) / Decimal("2")
            if percent and percent.get("multiplierDown") is not None
            else Decimal("0.98")
        )
        limit = self._tick_price(snapshot, str(mark * factor))
        self.client.futures_create_test_order(
            **self._order_params(symbol, "LIMIT", limit, limit_price=limit)
        )

    def order_test_negative_control(self, symbol: str) -> None:
        """A quantity below the minimum must be rejected by the exchange: proves order/test enforces filters."""
        snapshot = self._snapshot(symbol)
        price = self._tick_price(
            snapshot, self.client.futures_symbol_ticker(symbol=symbol)["price"]
        )
        params = self._order_params(symbol, "MARKET", price)
        sizing = _sizing(snapshot)
        step, min_qty = Decimal(sizing["step"]), Decimal(sizing["min_qty"])
        below = min_qty - step if min_qty - step > 0 else min_qty / Decimal("10")
        params["quantity"] = format(below, "f")
        try:
            self.client.futures_create_test_order(**params)
        except Exception as error:
            # Duck-typed: a BinanceAPIException carries the exchange ``code`` (the class itself is not imported,
            # so a process that replaced ``binance.exceptions`` cannot break the check).
            if getattr(error, "code", None) in FILTER_REJECT_CODES:
                return  # the expected answer
            raise
        raise CheckFailure("order/test accepted a quantity below the minimum")


def run_cycle(
    client: Any,
    *,
    symbol: str,
    state: ProbeState | None = None,
    deadline_seconds: float = CYCLE_DEADLINE_SECONDS,
    max_limit_fraction: float = DEFAULT_MAX_LIMIT_FRACTION,
    weight_limit: int = DEFAULT_WEIGHT_LIMIT_1M,
    order_limit_10s: int = DEFAULT_ORDER_LIMIT_10S,
    filters_ttl_seconds: float = FILTERS_TTL_SECONDS,
    clock: Callable[[], float] = perf_counter,
    wall: Callable[[], float] = time,
) -> dict[str, Result]:
    """Run every check once and return only classifications.

    The cycle stops early (the remaining checks count as ``skipped``) when the used weight or order count of
    the shared key is above ``max_limit_fraction`` of its limit, or when the exchange rate-limits it; checks
    that did not start before the deadline count as ``timeout``. The exchange filters are fetched at most
    once per ``filters_ttl_seconds``.
    """
    state = state or ProbeState()
    cycle = _Cycle(
        client,
        state,
        deadline_seconds=deadline_seconds,
        max_limit_fraction=max_limit_fraction,
        weight_limit=weight_limit,
        order_limit_10s=order_limit_10s,
        clock=clock,
        wall=wall,
    )
    cycle.run("time_sync", cycle.time_sync)
    cycle.run("signed_read", cycle.signed_read)
    cycle.run("account", cycle.account)
    due = (
        state.filters.get(symbol) is None
        or state.filters_fetched_at is None
        or clock() - state.filters_fetched_at >= filters_ttl_seconds
    )
    if due:
        cycle.run("filters_changed", lambda: cycle.filters_changed(symbol))
    cycle.run("order_test_market", lambda: cycle.order_test_market(symbol))
    cycle.run("order_test_limit", lambda: cycle.order_test_limit(symbol))
    cycle.run(
        "order_test_negative_control", lambda: cycle.order_test_negative_control(symbol)
    )
    return cycle.outcomes
