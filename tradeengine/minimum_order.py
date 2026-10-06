"""The one definition of the smallest order the exchange accepts.

Probe sizing, the dispatcher's order sizing, the USD ceiling and the exchange helper all use
:func:`minimum_quantity`, so no path can send a quantity below it.

The safety margin over MIN_NOTIONAL covers the price move between sizing and placement. It is derived from
short-term volatility (:func:`volatility_margin`); ``FALLBACK_MARGIN`` is used, and labelled as a fallback,
only when no recent prices are available.
"""

from __future__ import annotations

import math
from decimal import ROUND_UP, Decimal

#: Margin used only when recent prices are unavailable (reported with ``source: fallback``).
FALLBACK_MARGIN = 0.02
#: Model constants of the volatility margin: how long a quantity may sit between sizing and placement, and
#: how many standard deviations of that move to cover.
LATENCY_SECONDS = 15.0
Z_SCORE = 3.0
#: Fewest 1m returns the volatility estimate needs, and the largest margin it may produce.
MIN_RETURNS = 10
MAX_MARGIN = 0.5


def volatility_margin(closes: list[float]) -> float | None:
    """Expected adverse price move over the sizing-to-placement latency, as a fraction of the price.

    ``Z_SCORE x sigma(1m log returns) x sqrt(LATENCY_SECONDS / 60)``; ``None`` without enough prices.
    """
    prices = [float(close) for close in closes if close and float(close) > 0]
    returns = [math.log(b / a) for a, b in zip(prices, prices[1:], strict=False)]
    if len(returns) < MIN_RETURNS:
        return None
    mean = sum(returns) / len(returns)
    sigma = math.sqrt(sum((r - mean) ** 2 for r in returns) / (len(returns) - 1))
    return min(MAX_MARGIN, Z_SCORE * sigma * math.sqrt(LATENCY_SECONDS / 60.0))


def minimum_quantity(
    *,
    price: float,
    step: float | str,
    min_qty: float | str,
    min_notional: float | str,
    margin: float,
) -> float:
    """Smallest valid quantity: ``max(minQty, MIN_NOTIONAL / (price x (1 - margin)))`` rounded UP to the step.

    After rounding, ``quantity x price x (1 - margin) >= MIN_NOTIONAL`` still holds when the price falls by
    ``margin`` before placement, and ``quantity >= minQty``.
    """
    if price <= 0:
        raise ValueError("price must be positive")
    if not 0 <= margin < 1:
        raise ValueError("margin must be in [0, 1)")
    step_d = Decimal(str(step))
    wanted = max(
        Decimal(str(min_qty)),
        Decimal(str(min_notional))
        / (Decimal(str(price)) * (Decimal(1) - Decimal(str(margin)))),
    )
    if step_d > 0:
        wanted = (wanted / step_d).to_integral_value(rounding=ROUND_UP) * step_d
    return float(wanted)
