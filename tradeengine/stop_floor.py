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
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from statistics import NormalDist, median
from typing import Any

from petrosa_otel import get_meter

logger = logging.getLogger(__name__)
stop_contract_total = get_meter("tradeengine.stop_contract").create_counter(
    "tradeengine_stop_contract_total",
    description="Resolved stops by source, reason and placeability",
)

RISK_INPUTS_URL = "/api/v1/risk/inputs"
CANDLES_HISTORIC_URL = "/data/candles/historic"
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
HOURLY_SHORT_DAYS = 14
HOURLY_FLOOR_DAYS = 60
MIN_HOURLY_RETURNS = 7 * 24
CANDLE_PAGE_SIZE = 1000


def order_stop_contract(order: Any) -> dict[str, Any] | None:
    from shared.constants import MIN_SL_DISTANCE_PCT

    metadata = getattr(order, "strategy_metadata", None) or {}
    contract = metadata.get("stop_contract")
    if contract is None:
        contract = (metadata.get("metadata") or {}).get("stop_contract")
    if contract is None:
        return None
    if not isinstance(contract, dict) or contract.get("version") != 1:
        raise ValueError("invalid stop contract version")
    if (
        contract.get("symbol") != order.symbol
        or contract.get("side") != str(order.position_side).upper()
    ):
        raise ValueError("stop contract symbol or side mismatch")
    for key in ("reference_price", "stop_price", "stop_pct", "min_distance_pct"):
        value = contract.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError("invalid stop contract number")
    reference, price = contract["reference_price"], contract["stop_price"]
    fraction = (
        reference - price if contract["side"] == "LONG" else price - reference
    ) / reference
    if (
        contract.get("placeable") is not True
        or contract.get("source") not in ("signal", "derived", "fallback", "env")
        or not 0 < fraction < 1
        or not math.isclose(fraction, contract["stop_pct"], abs_tol=1e-12)
        or contract["min_distance_pct"] < MIN_SL_DISTANCE_PCT * 100 - 1e-12
        or not math.isclose(float(order.stop_loss or 0), price, abs_tol=1e-10)
    ):
        raise ValueError("inconsistent or unplaceable stop contract")
    return contract


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
    exchange_fetched_at: float = 0.0
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
        self._warm_lock = asyncio.Lock()
        self._warm_attempts: dict[str, float] = {}

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
            entry = self._inputs.get(symbol)
            if (
                entry is None
                or entry.sigma_1h is None
                or not entry.sufficient
                or self._clock() - entry.fetched_at
                > STALE_AFTER_REFRESHES * REFRESH_SECONDS
            ):
                await self._read_hourly_volatility(symbol)
            await self._read_exchange(symbol)

    def _read_risk_inputs(self, body: dict[str, Any]) -> None:
        now = self._clock()
        report_fetched_at = now
        if "as_of" in body:
            try:
                as_of = datetime.fromisoformat(
                    str(body["as_of"]).replace("Z", "+00:00")
                )
                as_of = as_of if as_of.tzinfo else as_of.replace(tzinfo=UTC)
                age = max(0.0, (datetime.now(UTC) - as_of).total_seconds())
                report_fetched_at = now - age
            except (TypeError, ValueError):
                report_fetched_at = 0.0
        for symbol, item in (body.get("symbols") or {}).items():
            hourly = (item or {}).get("hourly") or {}
            sigma = hourly.get("sigma_1h")
            entry = self._inputs.setdefault(symbol, _SymbolInputs())
            if (
                not isinstance(sigma, bool)
                and isinstance(sigma, int | float)
                and math.isfinite(sigma)
                and sigma > 0
            ):
                entry.sigma_1h = float(sigma)
                entry.n_returns = hourly.get("n_returns")
                entry.sufficient = bool(hourly.get("sufficient"))
                entry.fetched_at = report_fetched_at
        for strategy, item in (body.get("strategies") or {}).items():
            seconds = (item or {}).get("median_holding_seconds")
            if isinstance(seconds, int | float) and seconds > 0:
                self._holding_hours[strategy] = float(seconds) / 3600.0

    async def _read_hourly_volatility(self, symbol: str) -> None:
        """Recover hourly sigma from bounded data-manager candle pages."""
        candles: list[dict[str, Any]] = []
        end = datetime.now(UTC)
        start = end - timedelta(days=HOURLY_FLOOR_DAYS)
        try:
            for offset in (0, CANDLE_PAGE_SIZE):
                body = await self.client.request(
                    "GET",
                    CANDLES_HISTORIC_URL,
                    params={
                        "pair": symbol,
                        "period": "1h",
                        "start": start.isoformat(),
                        "end": end.isoformat(),
                        "limit": CANDLE_PAGE_SIZE,
                        "offset": offset,
                        "sort_order": "desc",
                    },
                )
                page = body.get("data") if isinstance(body, dict) else None
                if not isinstance(page, list):
                    return
                candles.extend(item for item in page if isinstance(item, dict))
                if len(page) < CANDLE_PAGE_SIZE:
                    break
        except Exception as exc:
            logger.warning(
                "Stop floor: hourly candles not available for %s: %s", symbol, exc
            )
            return

        series: dict[datetime, float] = {}
        for candle in candles:
            try:
                when = datetime.fromisoformat(
                    str(candle.get("timestamp")).replace("Z", "+00:00")
                )
                when = when if when.tzinfo else when.replace(tzinfo=UTC)
                price = float(candle.get("close"))
            except (TypeError, ValueError):
                continue
            if math.isfinite(price) and price > 0:
                series[when] = price

        ordered = sorted(series.items())
        now = datetime.now(UTC)
        timed_returns = [
            (timestamp, math.log(current_price / previous_price))
            for (previous_time, previous_price), (timestamp, current_price) in zip(
                ordered, ordered[1:], strict=False
            )
            if timestamp - previous_time == timedelta(hours=1)
        ]
        short = [
            value
            for timestamp, value in timed_returns
            if timestamp > now - timedelta(days=HOURLY_SHORT_DAYS)
        ]
        long = [
            value
            for timestamp, value in timed_returns
            if timestamp > now - timedelta(days=HOURLY_FLOOR_DAYS)
        ]
        candidates = [self._sample_sigma(values) for values in (short, long)]
        candidates = [value for value in candidates if value is not None]
        sigma = max(candidates) if candidates else None
        if sigma is None:
            return
        entry = self._inputs.setdefault(symbol, _SymbolInputs())
        entry.sigma_1h = sigma
        entry.n_returns = len(short)
        entry.sufficient = len(short) >= MIN_HOURLY_RETURNS
        entry.fetched_at = self._clock()
        logger.info(
            "Stop floor: recovered hourly volatility for %s sigma_1h=%s n_returns=%s sufficient=%s",
            symbol,
            sigma,
            len(short),
            entry.sufficient,
        )

    @staticmethod
    def _sample_sigma(values: list[float]) -> float | None:
        if len(values) < 2:
            return None
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
        return math.sqrt(variance) if math.isfinite(variance) else None

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
            if not all(
                math.isfinite(v) and v > 0
                for v in (entry.tick, entry.price, entry.band)
            ):
                raise ValueError("invalid exchange stop inputs")
            entry.exchange_fetched_at = self._clock()
        except Exception as exc:
            entry.tick = entry.price = entry.band = None
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

    async def resolve_stop(
        self,
        symbol: str,
        side: str,
        *,
        stop_loss: float | None = None,
        stop_loss_pct: float | None = None,
        reference_price: float | None = None,
        strategy_id: str | None = None,
        timeframe: str | None = None,
    ) -> dict[str, Any]:
        from shared.constants import MIN_SL_DISTANCE_PCT

        side = side.upper()
        if side not in ("LONG", "SHORT"):
            raise ValueError("stop contract requires LONG or SHORT")
        for value in (stop_loss, stop_loss_pct, reference_price):
            if value is not None and (
                isinstance(value, bool) or not math.isfinite(value) or value <= 0
            ):
                raise ValueError("stop inputs must be finite and positive")
        if stop_loss_pct is not None and stop_loss_pct >= 1:
            raise ValueError("stop fraction must be less than one")
        async with self._warm_lock:
            entry = self._inputs.get(symbol)
            cold = (
                self._missing(entry) is not None
                or entry is None
                or self._clock() - entry.exchange_fetched_at > REFRESH_SECONDS
            )
            if (
                cold
                and self._clock() - self._warm_attempts.get(symbol, -math.inf) >= 15
            ):
                self._warm_attempts[symbol] = self._clock()
                try:
                    body = await self.client.request("GET", RISK_INPUTS_URL)
                    if isinstance(body, dict):
                        self._read_risk_inputs(body)
                except Exception as exc:
                    logger.warning(
                        "Stop contract warmup failed for %s: %s", symbol, exc
                    )
                await self._read_exchange(symbol)
        entry = self._inputs.get(symbol)
        if (
            entry is None
            or not entry.tick
            or not entry.price
            or entry.band is None
            or self._clock() - entry.exchange_fetched_at > REFRESH_SECONDS
        ):
            raise ValueError("stop contract exchange inputs unavailable")
        reference = reference_price if reference_price is not None else entry.price
        if not reference:
            raise ValueError("stop contract reference price unavailable")
        technical = max(
            MIN_SL_DISTANCE_PCT,
            technical_floor(
                entry.tick,
                entry.price,
                median(entry.spreads) if entry.spreads else None,
                entry.band,
            ),
        )
        derived = self.floor(symbol, strategy_id)
        source, reason = "signal", None
        if stop_loss is not None:
            fraction = (
                (reference - stop_loss) / reference
                if side == "LONG"
                else (stop_loss - reference) / reference
            )
            if fraction <= 0 or fraction >= 1:
                raise ValueError("signal stop is not on the protective side")
        elif stop_loss_pct is not None:
            fraction = stop_loss_pct
        else:
            fraction = derived.fraction
            source, reason = derived.source, derived.reason
        fraction = max(fraction, technical)
        price = Decimal(str(reference)) * (
            Decimal(1) - Decimal(str(fraction))
            if side == "LONG"
            else Decimal(1) + Decimal(str(fraction))
        )
        tick = Decimal(str(entry.tick))
        price = (price / tick).to_integral_value(
            rounding=ROUND_FLOOR if side == "LONG" else ROUND_CEILING
        ) * tick
        price_float = float(price)
        fraction = abs(reference - price_float) / reference
        placeable = (
            price_float > 0
            and fraction < 1
            and fraction <= entry.band
            and (
                entry.price - price_float
                if side == "LONG"
                else price_float - entry.price
            )
            / entry.price
            >= technical - 1e-12
        )
        contract = {
            "version": 1,
            "symbol": symbol,
            "side": side,
            "timeframe": timeframe,
            "strategy_id": strategy_id,
            "reference_price": reference,
            "stop_price": price_float,
            "stop_pct": fraction,
            "source": source,
            "reason": reason,
            "min_distance_pct": technical * 100,
            "placeable": placeable,
            "inputs": derived.as_state(),
        }
        stop_contract_total.add(
            1,
            {
                "source": source,
                "reason": reason or "none",
                "placeable": str(placeable).lower(),
            },
        )
        log = logger.warning if source in ("fallback", "env") else logger.info
        log(
            "STOP_CONTRACT symbol=%s source=%s reason=%s stop=%s fraction=%s timeframe=%s placeable=%s",
            symbol,
            source,
            reason,
            price_float,
            fraction,
            timeframe,
            placeable,
        )
        return contract

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
