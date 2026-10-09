"""Entry point for the process-isolated exchange-edge probe."""

from __future__ import annotations

import os
import sys
import time

from ._client import ProbeBinanceClient, ProbeForbidden
from .metrics import initialise_series


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
    if os.environ.get("TE_SYNTHETIC_PROBE_ONESHOT", "false").lower() == "true":
        client.futures_time()
        return 0
    while True:
        client.futures_time()
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
