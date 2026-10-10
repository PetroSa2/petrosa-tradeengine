"""Entry point for the process-isolated exchange-edge probe."""

from __future__ import annotations

import os
import random
import sys
import time
from collections.abc import Callable

from prometheus_client import start_http_server

from ._client import TESTNET_HOST, ProbeBinanceClient, ProbeForbidden
from ._facade import ProbeFacade
from .checks import ProbeState, Result, run_cycle
from .metrics import (
    info,
    initialise_series,
    interval as interval_metric,
    last_run,
    recv_window,
)

#: The probe shares the testnet key and IP with the live testnet tradeengine, so it never runs faster than this.
MIN_INTERVAL_SECONDS = 60.0
DEFAULT_INTERVAL_SECONDS = 300.0
DEFAULT_FIRST_RUN_DELAY_SECONDS = 30.0
JITTER_FRACTION = 0.10
BACKOFF_BASE_SECONDS = 60.0
BACKOFF_CAP_SECONDS = 900.0
DEFAULT_METRICS_PORT = 8000


class Backoff:
    """Exponential delay after a rate limit (429/418), never shorter than the exchange's Retry-After."""

    def __init__(self) -> None:
        self.delay = 0.0

    def update(self, outcomes: dict[str, Result], retry_after: float | None) -> None:
        if Result.RATE_LIMITED in outcomes.values():
            self.delay = min(
                max(BACKOFF_BASE_SECONDS, self.delay * 2), BACKOFF_CAP_SECONDS
            )
            if retry_after:
                self.delay = max(self.delay, retry_after)
        else:
            self.delay = 0.0


def next_delay(
    interval: float,
    backoff: Backoff,
    uniform: Callable[[float, float], float] = random.uniform,
) -> float:
    """The sleep before the next cycle: the interval with +-10 % jitter, or the backoff when that is longer."""
    jittered = interval * uniform(1 - JITTER_FRACTION, 1 + JITTER_FRACTION)
    return max(jittered, backoff.delay)


def run_loop(
    cycle: Callable[[], dict[str, Result]],
    *,
    interval: float,
    first_run_delay: float,
    state: ProbeState,
    sleep: Callable[[float], None] = time.sleep,
    uniform: Callable[[float, float], float] = random.uniform,
    max_cycles: int | None = None,
) -> None:
    backoff = Backoff()
    sleep(first_run_delay)
    done = 0
    while max_cycles is None or done < max_cycles:
        outcomes = cycle()
        done += 1
        backoff.update(outcomes, state.retry_after)
        state.retry_after = None
        if max_cycles is not None and done >= max_cycles:
            return
        sleep(next_delay(interval, backoff, uniform))


def _read_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return default if raw is None or raw == "" else float(raw)


def main() -> int:
    if os.environ.get("BINANCE_TESTNET", "").lower() != "true":
        print("BINANCE_TESTNET=true is required", file=sys.stderr)
        return 2
    if not os.environ.get("BINANCE_API_KEY") or not os.environ.get(
        "BINANCE_API_SECRET"
    ):
        print("BINANCE_API_KEY and BINANCE_API_SECRET are required", file=sys.stderr)
        return 2
    try:
        interval = _read_float(
            "TE_SYNTHETIC_PROBE_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS
        )
        first_run_delay = _read_float(
            "TE_SYNTHETIC_PROBE_FIRST_RUN_DELAY_SECONDS",
            DEFAULT_FIRST_RUN_DELAY_SECONDS,
        )
        port = int(_read_float("TE_SYNTHETIC_PROBE_METRICS_PORT", DEFAULT_METRICS_PORT))
    except ValueError:
        print("the TE_SYNTHETIC_PROBE_* settings must be numbers", file=sys.stderr)
        return 2
    if interval < MIN_INTERVAL_SECONDS:
        print(
            f"TE_SYNTHETIC_PROBE_INTERVAL_SECONDS must be at least {MIN_INTERVAL_SECONDS:g}"
            " (the probe shares the testnet key with the live tradeengine)",
            file=sys.stderr,
        )
        return 2
    if first_run_delay < 0:
        print(
            "TE_SYNTHETIC_PROBE_FIRST_RUN_DELAY_SECONDS must not be negative",
            file=sys.stderr,
        )
        return 2
    try:
        client = ProbeBinanceClient(
            api_key=os.environ["BINANCE_API_KEY"],
            api_secret=os.environ["BINANCE_API_SECRET"],
        )
    except ProbeForbidden as exc:
        print(str(exc), file=sys.stderr)
        return 2
    oneshot = os.environ.get("TE_SYNTHETIC_PROBE_ONESHOT", "false").lower() == "true"
    symbol = os.environ.get("TE_SYNTHETIC_PROBE_SYMBOL", "BTCUSDT").upper()
    facade = ProbeFacade(client)
    initialise_series()
    interval_metric.set(interval)
    window = facade.recv_window_seconds()
    if window is not None:
        recv_window.set(window)
    info.labels(
        mode="oneshot" if oneshot else "loop",
        testnet="true",
        endpoint_host=TESTNET_HOST,
    ).set(1)
    state = ProbeState()

    def cycle() -> dict[str, Result]:
        outcomes = run_cycle(facade, symbol=symbol, state=state)
        last_run.set(time.time())
        return outcomes

    if oneshot:
        return 0 if all(r is Result.SUCCESS for r in cycle().values()) else 1
    start_http_server(port)  # the scrape endpoint (:8000), up before the first cycle
    run_loop(cycle, interval=interval, first_run_delay=first_run_delay, state=state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
