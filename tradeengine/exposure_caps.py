"""Exposure caps derived from the risk budget and volatility stress (petrosa-tradeengine#731, rule 11).

A fixed budget B (an operator input: ``TE_RISK_BUDGET_PCT``, 3% of equity) is the loss the book may take in a
stress of ``q`` sigma (2.33, 99% one-sided) of the daily move:

* **net cap** = B / (q x sigma of the held-pairs basket, 1d), as a fraction of equity. The basket is weighted by the
  held pairs' |net notional| with the measured correlations, so it follows what is actually held;
* **per-symbol cap** = B / (q x sigma of the symbol, 1d): per-pair sigma, no diversification;
* **gross cap**: unchanged, the fixed ratio of the exposure gate, labelled ``fallback`` until the gross definition is
  decided (PetroSa2/petrosa-tradeengine#684);
* **worst-case stop-risk budget**: the sum over open legs of notional x the stop distance of the aggregated leg
  must stay within B x equity (one netted leg per symbol and side, not each add-on entry).

Every cap that lacks its input (sigma or correlation insufficient or unavailable) falls back to the fixed ratio and
says so on ``/state`` (``source: fallback``). Daily sigma and correlation come from data-manager's risk inputs
(petrosa-data-manager#538). Reduce-only orders are always exempt (the gate runs only on orders that add exposure).
Pure functions plus a small cache for the risk inputs.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

RISK_INPUTS_URL = "/api/v1/risk/inputs"
REFRESH_SECONDS = 60 * 60
STALE_AFTER_REFRESHES = 3
DEFAULT_RISK_BUDGET_PCT = 3.0
DEFAULT_STRESS_QUANTILE = 2.33
FALLBACK_NET_RATIO = 0.6  # recorded decision 11
FALLBACK_SYMBOL_RATIO = 0.15
SOURCE_OPERATOR = "operator"


def _env_float(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.environ[name])
    except (KeyError, ValueError):
        return default
    return value if low < value <= high else default


def risk_budget() -> float:
    """B as a fraction of equity (``TE_RISK_BUDGET_PCT``, default 3.0 = 3%)."""
    return _env_float("TE_RISK_BUDGET_PCT", DEFAULT_RISK_BUDGET_PCT, 0.0, 100.0) / 100.0


def stress_quantile() -> float:
    return _env_float("TE_STRESS_QUANTILE", DEFAULT_STRESS_QUANTILE, 0.0, 10.0)


def caps_mode() -> str:
    """``report`` (default: caps are computed, shown on /state and breaches are logged), ``enforce`` (an order
    that would breach a cap is rejected) or ``off``."""
    value = os.environ.get("TE_EXPOSURE_CAPS_MODE", "report").strip().lower()
    return value if value in ("report", "enforce", "off") else "report"


@dataclass
class RiskInputs:
    """Sufficient daily sigmas per symbol and pair correlations, from data-manager."""

    sigma_daily: dict[str, float] = field(default_factory=dict)
    correlation: dict[str, dict[str, float | None]] = field(default_factory=dict)
    fetched_at: float = 0.0


def parse_risk_inputs(body: dict[str, Any], now: float = 0.0) -> RiskInputs:
    """Keep only what data-manager marks sufficient (``sigma_daily_best`` and the correlation matrix)."""
    sigmas: dict[str, float] = {}
    for symbol, item in (body.get("symbols") or {}).items():
        best = (item or {}).get("sigma_daily_best") or {}
        if best.get("sufficient") and best.get("value"):
            sigmas[str(symbol)] = float(best["value"])
    corr = body.get("correlation") or {}
    matrix, ok = corr.get("matrix") or {}, corr.get("sufficient") or {}
    correlation = {
        a: {
            b: (v if (ok.get(a) or {}).get(b) else None) for b, v in (row or {}).items()
        }
        for a, row in matrix.items()
    }
    return RiskInputs(sigma_daily=sigmas, correlation=correlation, fetched_at=now)


def basket_sigma(
    net_by_symbol: dict[str, float], inputs: RiskInputs
) -> tuple[float | None, str | None]:
    """Sigma of the held pairs weighted by |net notional|, from the measured correlations.

    None (with the reason) when a held pair has no sufficient sigma or two held pairs have no sufficient
    correlation: no number is assumed.
    """
    held = {s: abs(n) for s, n in net_by_symbol.items() if abs(n) > 0}
    total = sum(held.values())
    if not held or total <= 0:
        return None, "no_held_pairs"
    weights = {s: n / total for s, n in held.items()}
    for a in weights:
        if a not in inputs.sigma_daily:
            return None, f"no_sufficient_sigma_{a}"
    var = 0.0
    for a in weights:
        for b in weights:
            if a == b:
                rho = 1.0
            else:
                rho = (inputs.correlation.get(a) or {}).get(b)
                if rho is None:
                    return None, f"no_sufficient_correlation_{a}_{b}"
            var += (
                weights[a]
                * weights[b]
                * rho
                * inputs.sigma_daily[a]
                * inputs.sigma_daily[b]
            )
    return math.sqrt(max(var, 0.0)), None


@dataclass
class Cap:
    ratio: float  # of equity
    source: str  # derived | fallback
    sigma: float | None = None
    reason: str | None = None  # why it is a fallback

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def net_cap(
    net_by_symbol: dict[str, float], inputs: RiskInputs | None, budget: float, q: float
) -> Cap:
    if inputs is not None:
        sigma, why = basket_sigma(net_by_symbol, inputs)
        if sigma:
            return Cap(budget / (q * sigma), "derived", sigma)
        return Cap(FALLBACK_NET_RATIO, "fallback", None, why)
    return Cap(FALLBACK_NET_RATIO, "fallback", None, "risk_inputs_unavailable")


def symbol_cap(symbol: str, inputs: RiskInputs | None, budget: float, q: float) -> Cap:
    sigma = inputs.sigma_daily.get(symbol) if inputs is not None else None
    if sigma:
        return Cap(budget / (q * sigma), "derived", sigma)
    return Cap(
        FALLBACK_SYMBOL_RATIO,
        "fallback",
        None,
        "risk_inputs_unavailable"
        if inputs is None
        else f"no_sufficient_sigma_{symbol}",
    )


def leg_stop_risk(
    quantity: float,
    mark_price: float,
    stop_price: float | None,
    side: str,
    floor_fraction: float,
) -> float:
    """Worst-case loss of one aggregated leg if its stop is hit, in USD.

    The distance from the mark to the stop when the stop is on the protective side of the mark; otherwise (no
    stop recorded or a wrong-side one) the stop floor, as the stop would be re-anchored to it.
    """
    if quantity <= 0 or mark_price <= 0:
        return 0.0
    distance = None
    if stop_price and stop_price > 0:
        d = (
            (mark_price - stop_price) / mark_price
            if side == "LONG"
            else (stop_price - mark_price) / mark_price
        )
        if d > 0:
            distance = d
    return (
        quantity
        * mark_price
        * max(distance if distance is not None else floor_fraction, 0.0)
    )


@dataclass
class CapBreach:
    cap: str  # net_exposure_cap | symbol_exposure_cap | stop_risk_budget
    detail: str


def check_projection(
    *,
    symbol: str,
    signed_notional: float,
    equity: float,
    net_by_symbol: dict[str, float],
    gross_by_symbol: dict[str, float],
    stop_risk_usd: float,
    new_stop_risk_usd: float,
    inputs: RiskInputs | None,
) -> list[CapBreach]:
    """The caps an order that adds ``signed_notional`` (USD, signed by its position side) would breach."""
    if equity <= 0:
        return []
    budget, q = risk_budget(), stress_quantile()
    breaches: list[CapBreach] = []
    projected_net = dict(net_by_symbol)
    projected_net[symbol] = projected_net.get(symbol, 0.0) + signed_notional
    net = net_cap(projected_net, inputs, budget, q)
    net_ratio = abs(sum(projected_net.values())) / equity
    if net_ratio > net.ratio:
        breaches.append(
            CapBreach(
                "net_exposure_cap",
                f"projected net {net_ratio:.2%} of equity > {net.ratio:.2%} ({net.source}, sigma {net.sigma})",
            )
        )
    sym = symbol_cap(symbol, inputs, budget, q)
    sym_ratio = (gross_by_symbol.get(symbol, 0.0) + abs(signed_notional)) / equity
    if sym_ratio > sym.ratio:
        breaches.append(
            CapBreach(
                "symbol_exposure_cap",
                f"{symbol} projected {sym_ratio:.2%} of equity > {sym.ratio:.2%} ({sym.source}, sigma {sym.sigma})",
            )
        )
    if stop_risk_usd + new_stop_risk_usd > budget * equity:
        breaches.append(
            CapBreach(
                "stop_risk_budget",
                f"worst-case stop risk ${stop_risk_usd + new_stop_risk_usd:.2f} > "
                f"{budget:.2%} x equity ${equity:.2f}",
            )
        )
    return breaches


def snapshot(
    *,
    equity: float,
    net_by_symbol: dict[str, float],
    gross_by_symbol: dict[str, float],
    symbols: list[str],
    stop_risk_usd: float,
    inputs: RiskInputs | None,
    gross_ratio: float,
) -> dict[str, Any]:
    """``/state`` ``risk_limits.exposure_caps``: each cap with its source and inputs, and the stop-risk budget."""
    budget, q = risk_budget(), stress_quantile()
    net = net_cap(net_by_symbol, inputs, budget, q)
    per_symbol = {
        s: {
            **symbol_cap(s, inputs, budget, q).as_dict(),
            "gross_ratio": (gross_by_symbol.get(s, 0.0) / equity)
            if equity > 0
            else None,
        }
        for s in sorted(set(symbols) | set(gross_by_symbol))
    }
    return {
        "mode": caps_mode(),
        "risk_budget": {"ratio": budget, "source": SOURCE_OPERATOR},
        "stress_quantile": q,
        "net": {
            **net.as_dict(),
            "net_ratio": (abs(sum(net_by_symbol.values())) / equity)
            if equity > 0
            else None,
        },
        "per_symbol": per_symbol,
        "gross": {
            "ratio": gross_ratio,
            "source": "fallback",
            "reason": "gross definition pending petrosa-tradeengine#684",
        },
        "stop_risk": {
            "used_usd": stop_risk_usd,
            "budget_usd": budget * equity if equity > 0 else None,
            "ratio_of_budget": (stop_risk_usd / (budget * equity))
            if equity > 0 and budget > 0
            else None,
            "source": SOURCE_OPERATOR,
        },
        "inputs": {
            "available": inputs is not None,
            "fetched_at": inputs.fetched_at if inputs else None,
        },
    }


class RiskInputsCache:
    """data-manager's risk inputs, refreshed hourly in the background and served from memory."""

    def __init__(self) -> None:
        self.client: Any = None
        self.inputs: RiskInputs | None = None
        self._clock = __import__("time").monotonic

    def configure(self, client: Any) -> None:
        self.client = client

    async def refresh(self) -> None:
        try:
            body = await self.client.request("GET", RISK_INPUTS_URL)
        except Exception as exc:
            logger.warning("Exposure caps: risk inputs not available: %s", exc)
            return
        if isinstance(body, dict):
            self.inputs = parse_risk_inputs(body, self._clock())

    def current(self) -> RiskInputs | None:
        """The inputs, or None when absent or older than three refreshes."""
        if self.inputs is None:
            return None
        if (
            self._clock() - self.inputs.fetched_at
            > STALE_AFTER_REFRESHES * REFRESH_SECONDS
        ):
            return None
        return self.inputs

    async def run(self, interval_seconds: float = REFRESH_SECONDS) -> None:
        while True:
            try:
                await self.refresh()
            except Exception as exc:
                logger.warning("Exposure caps refresh failed: %s", exc)
            await asyncio.sleep(interval_seconds)


risk_inputs_cache = RiskInputsCache()
