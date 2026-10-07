"""Derived minimum stop distance (petrosa-tradeengine#742, recorded rule 23 of PetroSa2/petrosa_k8s#1239).

Every stop is kept at least a floor away from the live market; the floor used to be the fixed
``TE_MIN_SL_DISTANCE_PCT`` of 6.0%. Per symbol it is now

    floor = max(technical floor, noise floor)

* **technical floor** = max(10 ticks, 3 x the median recent spread), capped by the exchange's PERCENT_PRICE
  band: a stop closer than that is inside the book's own noise;
* **noise floor** = k x sigma_1h x sqrt(H), k = -Phi^-1(q / 2): plain noise reaches the stop within the
  holding horizon H with probability q. sigma_1h is the realized volatility of 1h log returns over a trailing 14
  days with a longer-window floor, read from data-manager's risk inputs (petrosa-data-manager#538); H is the
  strategy's median holding time, 4 h while that is unavailable.

The fixed value stays as the labelled fallback, used only when an input is missing or stale; setting
``TE_MIN_SL_DISTANCE_PCT`` pins the floor (source ``env``). The result is reported on ``/state`` and logged
with sigma_1h, H and q on every use. A derived floor beyond the exchange's maximum placeable distance is not
placeable: the order is skipped with a labelled reason instead of being clamped or flattened silently.
Only the stop is widened; the take-profit is left as the producer carried it.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from collections import deque
from dataclasses import dataclass, field
from statistics import NormalDist, median
from typing import Any

logger = logging.getLogger(__name__)

RISK_INPUTS_URL = "/api/v1/risk/inputs"
REFRESH_SECONDS = 15 * 60
#: Inputs older than this many refresh periods are not used.
STALE_AFTER_REFRESHES = 3
DEFAULT_Q = 0.10
DEFAULT_H_HOURS = 4.0
TECHNICAL_TICKS = 10
TECHNICAL_SPREADS = 3
#: The margin the price-adjuster keeps inside the PERCENT_PRICE band (``binance.py``).
BAND_SAFETY_MARGIN = 0.01
SPREAD_SAMPLES = 96


def _env_float(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.environ[name])
    except (KeyError, ValueError):
        return default
    return value if low < value < high else default


def stop_floor_q() -> float:
    """q: the probability that plain noise reaches the stop within H (``TE_STOP_FLOOR_Q``, default 0.10)."""
    return _env_float("TE_STOP_FLOOR_Q", DEFAULT_Q, 0.0, 1.0)


def fallback_horizon_hours() -> float:
    """H while the strategy's median holding time is unavailable (``TE_STOP_FLOOR_H_HOURS``, default 4)."""
    return _env_float("TE_STOP_FLOOR_H_HOURS", DEFAULT_H_HOURS, 0.0, 24.0 * 30)


def noise_multiplier(q: float) -> float:
    """k = -Phi^-1(q / 2): 1.645 for q = 0.10."""
    return -NormalDist().inv_cdf(q / 2.0)


def noise_floor(sigma_1h: float, horizon_hours: float, q: float) -> float:
    """k x sigma_1h x sqrt(H), as a fraction of the price."""
    return noise_multiplier(q) * sigma_1h * math.sqrt(horizon_hours)


def technical_floor(
    tick: float,
    price: float,
    median_spread: float | None,
    band: float | None,
) -> float:
    """max(10 ticks, 3 x median recent spread) as a fraction of the price, capped by the PERCENT_PRICE band."""
    parts = [TECHNICAL_TICKS * tick / price]
    if median_spread is not None:
        parts.append(TECHNICAL_SPREADS * median_spread)
    value = max(parts)
    return min(value, band) if band is not None else value


def max_placeable_fraction(multiplier_up: float, multiplier_down: float) -> float:
    """The farthest stop distance the PERCENT_PRICE band allows, inside the adjuster's safety margin."""
    up = multiplier_up * (1 - BAND_SAFETY_MARGIN) - 1.0
    down = 1.0 - multiplier_down * (1 + BAND_SAFETY_MARGIN)
    return min(up, down)


@dataclass
class StopFloorResult:
    """The floor for one symbol and where it came from."""

    symbol: str
    pct: float  # percent, as ``/state`` reports it
    source: str  # derived | fallback | env
    reason: str | None = None  # why it is a fallback
    sigma_1h: float | None = None
    n_returns: int | None = None
    horizon_hours: float | None = None
    horizon_source: str | None = None
    q: float | None = None
    k: float | None = None
    technical_pct: float | None = None
    noise_pct: float | None = None
    max_placeable_pct: float | None = None
    placeable: bool = True
    spread_samples: int = 0

    @property
    def fraction(self) -> float:
        return self.pct / 100.0

    def as_state(self) -> dict[str, Any]:
        """The fields added to ``/state`` ``risk_limits`` for the symbol."""
        return {
            "min_sl_distance_pct": self.pct,
            "min_sl_distance_source": self.source,
            "min_sl_distance_reason": self.reason,
            "stop_floor": {
                "sigma_1h": self.sigma_1h,
                "n_returns": self.n_returns,
                "horizon_hours": self.horizon_hours,
                "horizon_source": self.horizon_source,
                "q": self.q,
                "k": self.k,
                "technical_pct": self.technical_pct,
                "noise_pct": self.noise_pct,
                "max_placeable_pct": self.max_placeable_pct,
                "placeable": self.placeable,
                "spread_samples": self.spread_samples,
            },
        }

    def log_line(self) -> str:
        return (
            f"STOP_FLOOR {self.symbol} floor={self.pct:.3f}% source={self.source}"
            f"{' reason=' + self.reason if self.reason else ''} sigma_1h={self.sigma_1h} "
            f"H={self.horizon_hours}h({self.horizon_source}) q={self.q} "
            f"technical={self.technical_pct} noise={self.noise_pct} "
            f"max_placeable={self.max_placeable_pct} placeable={self.placeable}"
        )


@dataclass
class _SymbolInputs:
    sigma_1h: float | None = None
    n_returns: int | None = None
    sufficient: bool = False
    tick: float | None = None
    price: float | None = None
    band: float | None = None  # max placeable distance (fraction)
    fetched_at: float = 0.0
    spreads: deque[float] = field(default_factory=lambda: deque(maxlen=SPREAD_SAMPLES))


class StopFloorProvider:
    """Per-symbol stop floors from data-manager's risk inputs and the exchange's filters, refreshed in the
    background and served from memory."""

    def __init__(self) -> None:
        self.client: Any = None
        self.exchange: Any = None
        self.symbols: list[str] = []
        self._inputs: dict[str, _SymbolInputs] = {}
        self._holding_hours: dict[str, float] = {}
        self._clock = time.monotonic

    def configure(self, client: Any, exchange: Any, symbols: list[str]) -> None:
        self.client = client
        self.exchange = exchange
        self.symbols = list(symbols)

    # -- refresh -----------------------------------------------------------------------------------

    async def refresh(self) -> None:
        """Read the risk inputs and the exchange state of every symbol; a failure keeps the last values."""
        try:
            body = await self.client.request("GET", RISK_INPUTS_URL)
        except Exception as exc:
            logger.warning("Stop floor: risk inputs not available: %s", exc)
            body = None
        if isinstance(body, dict):
            self._read_risk_inputs(body)
        for symbol in self.symbols:
            await self._read_exchange(symbol)

    def _read_risk_inputs(self, body: dict[str, Any]) -> None:
        now = self._clock()
        for symbol, item in (body.get("symbols") or {}).items():
            hourly = (item or {}).get("hourly") or {}
            sigma = hourly.get("sigma_1h")
            entry = self._inputs.setdefault(symbol, _SymbolInputs())
            if isinstance(sigma, int | float) and sigma > 0:
                entry.sigma_1h = float(sigma)
                entry.n_returns = hourly.get("n_returns")
                entry.sufficient = bool(hourly.get("sufficient"))
                entry.fetched_at = now
        for strategy, item in (body.get("strategies") or {}).items():
            seconds = (item or {}).get("median_holding_seconds")
            if isinstance(seconds, int | float) and seconds > 0:
                self._holding_hours[strategy] = float(seconds) / 3600.0

    async def _read_exchange(self, symbol: str) -> None:
        entry = self._inputs.setdefault(symbol, _SymbolInputs())
        try:
            filters = self.exchange.symbol_info[symbol]["filters"]
            entry.tick = float(
                next(f for f in filters if f["filterType"] == "PRICE_FILTER")[
                    "tickSize"
                ]
            )
            entry.price = float(await self.exchange._get_current_price(symbol))
            band = self.exchange.get_percent_price_filter(symbol)
            entry.band = max_placeable_fraction(
                float(band["multiplierUp"]), float(band["multiplierDown"])
            )
        except Exception as exc:
            logger.debug(
                "Stop floor: exchange filters not available for %s: %s", symbol, exc
            )
        try:
            ticker = await asyncio.to_thread(
                self.exchange.client.futures_orderbook_ticker, symbol=symbol
            )
            bid, ask = float(ticker["bidPrice"]), float(ticker["askPrice"])
            if bid > 0 and ask >= bid:
                entry.spreads.append((ask - bid) / ((ask + bid) / 2.0))
        except Exception as exc:
            logger.debug("Stop floor: no book ticker for %s: %s", symbol, exc)

    async def run(self, interval_seconds: float = REFRESH_SECONDS) -> None:
        """Refresh at startup, then every ``interval_seconds``, until cancelled."""
        while True:
            try:
                await self.refresh()
            except Exception as exc:
                logger.warning("Stop floor refresh failed: %s", exc)
            await asyncio.sleep(interval_seconds)

    # -- the floor ---------------------------------------------------------------------------------

    def horizon_for(self, strategy_id: str | None) -> tuple[float, str]:
        """H in hours and its source: the strategy's median holding time, else the labelled fallback."""
        if strategy_id and strategy_id in self._holding_hours:
            return self._holding_hours[strategy_id], "median_holding_time"
        return fallback_horizon_hours(), "fallback"

    def floor(self, symbol: str, strategy_id: str | None = None) -> StopFloorResult:
        """The floor for ``symbol`` from the cached inputs (never blocks)."""
        from shared.config import Settings

        fixed = float(Settings().te_min_sl_distance_pct)
        q = stop_floor_q()
        k = noise_multiplier(q)
        horizon, horizon_source = self.horizon_for(strategy_id)
        entry = self._inputs.get(symbol)
        result = StopFloorResult(
            symbol=symbol,
            pct=fixed,
            source="fallback",
            horizon_hours=horizon,
            horizon_source=horizon_source,
            q=q,
            k=k,
        )
        if entry is not None:
            result.sigma_1h = entry.sigma_1h
            result.n_returns = entry.n_returns
            result.spread_samples = len(entry.spreads)
            result.max_placeable_pct = (
                entry.band * 100.0 if entry.band is not None else None
            )
        missing = self._missing(entry)
        if missing is None:
            assert entry is not None and entry.sigma_1h and entry.tick and entry.price
            spread = median(entry.spreads) if entry.spreads else None
            technical = technical_floor(entry.tick, entry.price, spread, entry.band)
            noise = noise_floor(entry.sigma_1h, horizon, q)
            derived = max(technical, noise)
            result.technical_pct = technical * 100.0
            result.noise_pct = noise * 100.0
            result.pct = derived * 100.0
            result.source = "derived"
            result.placeable = entry.band is None or derived <= entry.band
        else:
            result.reason = missing
        if os.environ.get("TE_MIN_SL_DISTANCE_PCT", "").strip():
            # An explicit pin wins over the derivation; it keeps the old, clamping behaviour.
            result.pct = fixed
            result.source = "env"
            result.reason = None
            result.placeable = True
        return result

    def _missing(self, entry: _SymbolInputs | None) -> str | None:
        if entry is None:
            return "no_inputs"
        if entry.sigma_1h is None:
            return "sigma_1h_unavailable"
        if not entry.sufficient:
            return "sigma_1h_insufficient"
        if self._clock() - entry.fetched_at > STALE_AFTER_REFRESHES * REFRESH_SECONDS:
            return "sigma_1h_stale"
        if not entry.tick or not entry.price:
            return "exchange_filters_unavailable"
        return None


stop_floor = StopFloorProvider()
