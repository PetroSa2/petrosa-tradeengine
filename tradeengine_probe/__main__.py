"""Entry point for the process-isolated exchange-edge probe."""

from __future__ import annotations

import os
import sys
import time

from ._client import ProbeBinanceClient, ProbeForbidden
from ._facade import ProbeFacade
from .checks import Result, run_cycle
from .metrics import (
    initialise_series,
    interval as interval_metric,
    last_run,
)


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
        client = ProbeBinanceClient(
            api_key=os.environ["BINANCE_API_KEY"],
            api_secret=os.environ["BINANCE_API_SECRET"],
        )
    except ProbeForbidden as exc:
        print(str(exc), file=sys.stderr)
        return 2
    initialise_series()
    interval = float(os.environ.get("TE_SYNTHETIC_PROBE_INTERVAL_SECONDS", "300"))
    symbol = os.environ.get("TE_SYNTHETIC_PROBE_SYMBOL", "BTCUSDT").upper()
    interval_metric.set(interval)
    facade = ProbeFacade(client)

    def cycle() -> bool:
        outcomes = run_cycle(facade, symbol=symbol)
        last_run.set(time.time())
        return all(result is Result.SUCCESS for result in outcomes.values())

    if os.environ.get("TE_SYNTHETIC_PROBE_ONESHOT", "false").lower() == "true":
        return 0 if cycle() else 1
    while True:
        cycle()
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
